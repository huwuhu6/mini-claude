"""
Loop Controller V3 — Combined Circuit Breaker Defense with Intent Normalization.

Three-layer defense pipeline:
  1. CommandNormalizer — strips CLI noise (cd, chcp, set ENV, redirects, -X flags)
     to extract canonical {action, target} intent fingerprints.
  2. Intent-Aware LoopGuard — compares normalized intents instead of raw command
     strings, injects LOOP_GUARD_PREVENTED virtual failures into FailureMemory.
  3. Hard Circuit Breaker — when the same intent accumulates 5 failures
     (TOOL_CRASH + LOOP_GUARD_PREVENTED combined), raises RuntimeEscalationException
     to physically terminate the agent loop.

Usage:
    controller = LoopController(failure_memory=memory)

    block_msg = controller.check_and_record("bash", {"command": "python run_test.py"})
    if block_msg:
        # Tool was intercepted — return block_msg as result
        ...

    # After real tool failure, register the strike
    controller.register_failure("bash", {"command": "..."}, "TOOL_CRASH")
"""
from __future__ import annotations
import re
import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger(__name__)


class RuntimeEscalationException(Exception):
    """Hard circuit breaker exception — terminates the agent loop immediately.

    When raised, the caller (MiniClaudeAgent._llm_tool_cycle) MUST:
    1. Catch it at the loop level.
    2. Set final_status = "CIRCUIT_BROKEN" on the current trace.
    3. NOT return any tool_result to the LLM.
    4. Return the exception message as the final answer.
    """
    pass


# ──────────────────────────────────────────────────────────────────────
#  Layer 1: Command Normalization
# ──────────────────────────────────────────────────────────────────────

@dataclass
class NormalizedIntent:
    """Canonical semantic fingerprint of a tool call.

    Two bash commands that do "the same thing" but with different CLI noise
    will produce the same (action, target) pair.
    """
    action: str       # e.g. "EXECUTE", "INSTALL_PACKAGE", "READ", "WRITE"
    target: str       # e.g. "run_test.py", "db.py", "pygame"
    tool: str = ""    # original tool name (for non-bash tools)

    def to_key(self) -> str:
        """Globally unique key for dedup & strike counting."""
        return f"{self.tool}:{self.action}:{self.target}"


class CommandNormalizer:
    """Strips CLI environment noise from bash commands.

    Noise sources removed (in order):
      - `chcp 65001 > nul &&` / `chcp 65001 &&`
      - `set VAR=value &&`
      - `cd /d X:/path &&`
      - `cd X:/path &&`
      - `python -X utf8` / `py -X utf8`
      - `2>&1`
      - `> file` redirects
      - `| type file` pipe noise
    """

    _NOISE_PATTERNS = [
        # chcp code-page switching
        re.compile(r'chcp\s+\d+\s*(?:>\s*nul\s*)?&&?\s*'),
        # set ENV var
        re.compile(r'set\s+\w+=\w+\s*&&?\s*'),
        # cd /d X:\path  (use \S+ for path to avoid Python 3.14 regex char-class issues)
        re.compile(r'cd\s+/d\s+\S+\s*&&?\s*'),
        # cd X:\path
        re.compile(r'cd\s+\S+\s*&&?\s*'),
        # pushd X:\path
        re.compile(r'pushd\s+\S+\s*&&?\s*'),
        # python -X flag (e.g. -X utf8)
        re.compile(r'(?:python|py)\s+-X\s+\w+\s+'),
        # stderr redirect
        re.compile(r'\s+2>&1'),
        # stdout redirect (> file, > nul, etc.)
        re.compile(r'\s+>\s*\S+(?:\s+2>&1)?'),
        # pipe to type (Windows: cmd1 | type cmd2)
        re.compile(r'\s*\|\s*type\s+\S+'),
    ]

    _ACTION_PATTERNS: List[Tuple[str, re.Pattern]] = [
        ("INSTALL_PACKAGE", re.compile(
            r'(?:pip|pip3|npm|conda|brew|apt(?:-get)?|choco|yum)\s+install\s+'
        )),
        ("PACKAGE_QUERY", re.compile(
            r'(?:pip|pip3)\s+(?:list|show)\s*'
        )),
        ("NETWORK_DOWNLOAD", re.compile(
            r'(?:curl|wget)\s+'
        )),
        ("EXECUTE", re.compile(
            r'(?:python|py|python3|node|ruby|perl|bash)\s+'
        )),
        ("COMPILE", re.compile(
            r'(?:gcc|g\+\+|make|cmake|clang|rustc|go\s+build)\s+'
        )),
        ("VCS", re.compile(
            r'(?:^|\s)git\s+'
        )),
    ]

    # File-based tool action mapping
    _FILE_TOOL_ACTIONS = {
        "read_file": "READ",
        "read_file_lines": "READ",
        "write_file": "WRITE",
        "edit_file": "EDIT",
    }

    @classmethod
    def normalize(cls, tool_name: str, args: Dict[str, Any]) -> NormalizedIntent:
        """Extract a canonical semantic intent from any tool call."""
        # ── Non-bash tools (file ops, etc.) ──────────────────
        if tool_name != "bash":
            action = cls._FILE_TOOL_ACTIONS.get(tool_name, tool_name.upper())
            path = args.get("path", "") if isinstance(args, dict) else ""
            target = path.replace("\\", "/").rstrip("/").split("/")[-1] if path else tool_name
            return NormalizedIntent(action=action, target=target, tool=tool_name)

        # ── Bash command normalization ───────────────────────
        cmd = args.get("command", "") if isinstance(args, dict) else str(args)
        if not cmd:
            return NormalizedIntent(action="UNKNOWN", target="", tool=tool_name)

        # Step 1: Strip noise
        cleaned = cmd
        for pat in cls._NOISE_PATTERNS:
            cleaned = pat.sub("", cleaned)
        cleaned = cleaned.strip()

        if not cleaned:
            cleaned = cmd.strip()[:80]  # fallback to raw prefix

        # Step 2: Classify action
        action = "EXECUTE"
        for act, pattern in cls._ACTION_PATTERNS:
            if pattern.search(cleaned):
                action = act
                break

        # Step 3: Extract target
        target = cls._extract_target(action, cleaned)

        return NormalizedIntent(action=action, target=target, tool=tool_name)

    @classmethod
    def _extract_target(cls, action: str, cleaned: str) -> str:
        """Extract the key target (file/package) from a cleaned command."""
        if action == "INSTALL_PACKAGE":
            m = re.search(
                r'(?:pip|pip3|npm|conda|brew|apt(?:-get)?|choco|yum)\s+install\s+(\S+)',
                cleaned
            )
            return m.group(1) if m else cleaned[:60]

        if action in ("EXECUTE", "COMPILE"):
            m = re.search(
                r'(?:python|py|python3|node|ruby|perl|bash|gcc|g\+\+|rustc)\s+(\S+)',
                cleaned
            )
            return m.group(1) if m else cleaned[:60]

        if action in ("VCS",):
            m = re.search(r'git\s+(\S+)', cleaned)
            return f"git {m.group(1)}" if m else cleaned[:60]

        return cleaned[:60]


# ──────────────────────────────────────────────────────────────────────
#  Layer 2: Intent-Aware LoopGuard
# ──────────────────────────────────────────────────────────────────────

class V3LoopGuard:
    """Intent-based loop guard that normalizes commands before comparison.

    When a duplicate intent is detected:
      1. Physically blocks the tool from executing.
      2. Injects a LOOP_GUARD_PREVENTED virtual failure into FailureMemory,
         so the Failure Intelligence layer sees the guard's activity.
      3. Registers a strike on the CircuitBreaker.
    """

    def __init__(
        self,
        max_recent: int = 5,
        min_occurrences: int = 2,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ):
        self.max_recent = max_recent
        self.min_occurrences = min_occurrences
        self.circuit_breaker = circuit_breaker
        self.recent_intents: List[NormalizedIntent] = []
        self.trigger_count: int = 0

    def set_circuit_breaker(self, cb: CircuitBreaker) -> None:
        self.circuit_breaker = cb

    def check_and_record(self, tool_name: str, args: Dict[str, Any]) -> Optional[str]:
        """Check the proposed call and record the intent.

        Returns:
            None if the call is safe to execute.
            A block-message string if the call should be intercepted.
        """
        intent = CommandNormalizer.normalize(tool_name, args)
        intent_key = intent.to_key()

        # ── Record intent unconditionally ──
        self.recent_intents.append(intent)
        if len(self.recent_intents) > self.max_recent * 5:
            self.recent_intents = self.recent_intents[-self.max_recent:]

        if len(self.recent_intents) < 2:
            return None

        # ── Check: frequency in sliding window ──
        # (exclude current call from window)
        window = self.recent_intents[-self.max_recent - 1:-1]
        match_count = sum(1 for i in window if i.to_key() == intent_key)

        if match_count >= self.min_occurrences:
            self.trigger_count += 1
            logger.warning(
                f"[V3] 意图重复拦截 (频率 {match_count}/{self.max_recent}): "
                f"{intent_key}"
            )
            if self.circuit_breaker:
                self.circuit_breaker.register_failure(
                    tool_name, args, "LOOP_GUARD_PREVENTED"
                )
            return self._build_block_message(intent)

        return None

    def _build_block_message(self, intent: NormalizedIntent) -> str:
        return (
            f"⛔ [系统安全拦截 — 防死循环保护]\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"检测到你正在重复相同的操作：\n"
            f"  操作: {intent.action}\n"
            f"  目标: {intent.target}\n"
            f"\n"
            f"该调用已被系统物理拦截——工具未被执行。\n"
            f"请勿盲目重试！\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    def clear(self) -> None:
        self.recent_intents.clear()
        self.trigger_count = 0


# ──────────────────────────────────────────────────────────────────────
#  Layer 3: Hard Circuit Breaker
# ──────────────────────────────────────────────────────────────────────

class CircuitBreaker:
    """Hard-stop circuit breaker with per-intent strike counting.

    When an intent accumulates STRIKE_LIMIT failures (combining real tool
    errors and LOOP_GUARD_PREVENTED virtual records), raises
    RuntimeEscalationException to physically terminate the agent loop.

    Design principle: this is a HARD stop, not a soft suggestion.
    The LLM never sees a tool_result — the loop catches the exception
    and returns the escalation message directly to the user.
    """

    STRIKE_LIMIT: int = 5

    def __init__(self, strike_limit: int = 5):
        self.STRIKE_LIMIT = strike_limit
        # per-intent-key → cumulative strike count
        self._strikes: Dict[str, int] = {}
        # intents that already triggered escalation (prevent re-fire)
        self._escalated: set = set()

    def register_failure(
        self,
        tool_name: str,
        args: Dict[str, Any],
        failure_category: str,
    ) -> None:
        """Register a failure for circuit breaker evaluation.

        Call this BOTH when:
          - A real tool error is detected (TOOL_CRASH, TIMEOUT, etc.)
          - The LoopGuard intercepts a call (LOOP_GUARD_PREVENTED)

        Raises RuntimeEscalationException when the strike limit is reached.
        """
        intent = CommandNormalizer.normalize(tool_name, args)
        key = intent.to_key()

        # Accumulate strike
        self._strikes[key] = self._strikes.get(key, 0) + 1
        total = self._strikes[key]

        logger.warning(
            f"[V3] 断路器登记失败: intent={key}, category={failure_category}, "
            f"strikes={total}/{self.STRIKE_LIMIT}"
        )

        # Check threshold
        if total >= self.STRIKE_LIMIT and key not in self._escalated:
            self._escalated.add(key)
            logger.critical(
                f"[V3] 断路器触发！intent={key}, strikes={total}/{self.STRIKE_LIMIT}"
            )
            raise RuntimeEscalationException(
                f"⛔ [V3 硬断路器] 累计失败 {total} 次（阈值: {self.STRIKE_LIMIT}），"
                f"操作: {intent.action}，目标: {intent.target}\n"
                f"Runtime 已物理掐断 Agent 循环。"
            )

    def get_strike_count(self, intent_key: str) -> int:
        return self._strikes.get(intent_key, 0)

    def reset(self) -> None:
        self._strikes.clear()
        self._escalated.clear()


# ──────────────────────────────────────────────────────────────────────
#  Facade: LoopController (convenience wrapper)
# ──────────────────────────────────────────────────────────────────────

class LoopController:
    """Unified V3 defense controller combining all three layers.

    This is the single entry point that MiniClaudeAgent uses:
      controller = LoopController()
      controller.set_failure_memory(fi_memory)

      # Before tool execution:
      block_msg = controller.check("bash", {"command": "..."})
      if block_msg:
          # tool was intercepted, use block_msg as result
          ...

      # After real tool failure:
      controller.register_failure("bash", {"command": "..."}, "TOOL_CRASH")
    """

    def __init__(self, failure_memory=None, strike_limit: int = 5):
        self.circuit_breaker = CircuitBreaker(strike_limit=strike_limit)
        self.guard = V3LoopGuard(circuit_breaker=self.circuit_breaker)
        self.failure_memory = failure_memory

    def set_failure_memory(self, memory) -> None:
        self.failure_memory = memory

    def check(self, tool_name: str, args: Dict[str, Any]) -> Optional[str]:
        """Layer 2 + 3: intent-aware dedup → circuit breaker escalation.

        Returns block message or None. May raise RuntimeEscalationException.
        """
        block_msg = self.guard.check_and_record(tool_name, args)

        if block_msg and self.failure_memory:
            # Inject LOOP_GUARD_PREVENTED virtual failure into FI
            intent = CommandNormalizer.normalize(tool_name, args)
            self.failure_memory.record(
                category="LOOP_GUARD_PREVENTED",
                strategy_fp=intent.action,
            )

        return block_msg

    def register_failure(self, tool_name: str, args: Dict[str, Any],
                          failure_category: str) -> None:
        """Register a real tool failure with the circuit breaker.

        May raise RuntimeEscalationException.
        """
        self.circuit_breaker.register_failure(tool_name, args, failure_category)

    @property
    def trigger_count(self) -> int:
        return self.guard.trigger_count

    def clear(self) -> None:
        self.guard.clear()
        self.circuit_breaker.reset()
