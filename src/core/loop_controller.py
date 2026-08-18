"""
Loop Controller V3 — Combined Circuit Breaker Defense with Intent Normalization.

Three-layer defense pipeline:
  1. CommandNormalizer — token-based CLI normalizer (shlex.split) that strips
     shell noise and extracts canonical {action, target} intent fingerprints.
  2. Intent-Aware LoopGuard — compares normalized intents instead of raw command
     strings, injects LOOP_GUARD_PREVENTED virtual failures into FailureMemory.
  3. Hard Circuit Breaker — when the same intent accumulates 5 failures
     (TOOL_CRASH + LOOP_GUARD_PREVENTED combined), raises RuntimeEscalationException
     to physically terminate the agent loop.

Usage:
    controller = LoopController(failure_memory=memory)

    block_msg = controller.check("bash", {"command": "python run_test.py"})
    if block_msg:
        # Tool was intercepted — return block_msg as result
        ...

    # After real tool failure, register the strike
    controller.register_failure("bash", {"command": "..."}, "TOOL_CRASH")
"""
from __future__ import annotations
import shlex
import json
import hashlib
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Any

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
    """Token-based CLI normalizer using shlex.split.

    Transforms shell command strings into canonical {action, target} intent
    fingerprints by tokenizing with shlex.split rather than applying regex
    noise patterns. This catches CLI evasion variants (chcp + cd + set + -X
    + redirects) that regex cannot generalize.

    Pipeline:
      1. Tokenize with shlex.split, then split on &&/; into segments.
      2. Find the action-bearing segment (skip env-prefix segments).
      3. Strip trailing shell redirect tokens (2>&1, > file, | type).
      4. Classify action from executable name.
      5. Extract target (first non-flag argument).
    """

    # Commands that only set up environment — skip when found as segment head
    _ENV_PREFIX_COMMANDS = frozenset({'chcp', 'cd', 'pushd', 'set'})

    # Shell redirect operators used at command tail
    _REDIRECT_OPS = frozenset({'>', '>>', '<', '2>', '2>>', '|'})

    # File-based tool action mapping (non-bash tools)
    _FILE_TOOL_ACTIONS = {
        "read_file":        "READ",
        "read_file_lines":  "READ",
        "write_file":       "WRITE",
        "edit_file":        "EDIT",
    }

    # ── Non-bash target extraction helpers ─────────────────────────────

    @classmethod
    def _extract_non_bash_target(cls, tool_name: str, args: Dict[str, Any]) -> str:
        """Build a disambiguated target string for non-bash tool calls.

        Pipeline:
          1. Extract base target from ``path`` (singular) or ``paths``
             (plural — sorted + hashed to distinguish different sets).
          2. Append tool-specific suffixes (line range, patterns, etc.).
        """
        if not isinstance(args, dict):
            return tool_name

        # ── TodoWrite: hash items content so status changes produce different keys ──
        if tool_name == "TodoWrite":
            items = args.get("items", [])
            # Sort by content to ignore item reordering
            sorted_items = sorted(items, key=lambda x: json.dumps(x, sort_keys=True))
            h = hashlib.md5(json.dumps(sorted_items, sort_keys=True).encode()).hexdigest()[:8]
            return f"state:{h}"

        target = cls._extract_base_path(args)

        # Tool-specific disambiguation
        if tool_name == "read_file":
            target = cls._suffix_line_range(target, args)
        elif tool_name in ("search_code", "count_occurrences"):
            target = cls._suffix_patterns(target, args)
        elif tool_name == "edit_file":
            target = cls._suffix_edits(target, args)

        return target or tool_name

    @classmethod
    def _extract_base_path(cls, args: Dict[str, Any]) -> str:
        """Extract base target from ``path`` (singular) or ``paths`` (plural).

        - ``path`` → filename extracted from the path string.
        - ``paths`` → ``paths:<md5-of-sorted-tuple>`` to distinguish sets.
        """
        path = args.get("path")
        if path and isinstance(path, str):
            return path.replace("\\", "/").rstrip("/").split("/")[-1]
        paths = args.get("paths")
        if paths and isinstance(paths, (list, tuple)) and len(paths) > 0:
            sorted_tuple = tuple(sorted(str(p) for p in paths))
            h = hashlib.md5(str(sorted_tuple).encode()).hexdigest()[:8]
            return f"paths:{h}"
        return ""

    @classmethod
    def _suffix_line_range(cls, target: str, args: Dict[str, Any]) -> str:
        """Append ``:L{start}[-{end}]`` for read_file with line range."""
        start_line = args.get("start_line")
        if start_line is not None:
            suffix = f":L{int(start_line)}"
            end_line = args.get("end_line")
            if end_line is not None:
                suffix += f"-{int(end_line)}"
            return target + suffix
        return target

    @classmethod
    def _suffix_patterns(cls, target: str, args: Dict[str, Any]) -> str:
        """Append ``:P:<hash>`` for tools with a patterns parameter."""
        patterns = args.get("patterns")
        if patterns and isinstance(patterns, (list, tuple)) and len(patterns) > 0:
            sorted_tuple = tuple(sorted(str(p) for p in patterns))
            h = hashlib.md5(str(sorted_tuple).encode()).hexdigest()[:8]
            return f"{target}:P:{h}"
        return target

    @classmethod
    def _suffix_edits(cls, target: str, args: Dict[str, Any]) -> str:
        """Append ``:E:<hash>`` for edit_file so different edits produce different keys."""
        edits = args.get("edits")
        if edits and isinstance(edits, (list, tuple)) and len(edits) > 0:
            # Sort by canonical JSON to ignore item reordering
            sorted_edits = sorted(edits, key=lambda x: json.dumps(x, sort_keys=True))
            h = hashlib.md5(json.dumps(sorted_edits, sort_keys=True).encode()).hexdigest()[:8]
            return f"{target}:E:{h}"
        return target

    # ── Public entry point ─────────────────────────────────────────────

    @classmethod
    def normalize(cls, tool_name: str, args: Dict[str, Any]) -> NormalizedIntent:
        """Extract a canonical semantic intent from any tool call."""
        # ── Non-bash tools (file ops, etc.) ──────────────────
        if tool_name != "bash":
            action = cls._FILE_TOOL_ACTIONS.get(tool_name, tool_name.upper())
            target = cls._extract_non_bash_target(tool_name, args)
            intent = NormalizedIntent(action=action, target=target, tool=tool_name)
            logger.debug(f"[CommandNormalizer] {tool_name} → {intent.to_key()}")
            return intent

        # ── Bash command normalization ───────────────────────
        cmd = args.get("command", "") if isinstance(args, dict) else str(args)
        if not cmd:
            return NormalizedIntent(action="UNKNOWN", target="", tool=tool_name)

        # Step 1: Tokenize and split on &&/;
        try:
            all_tokens = shlex.split(cmd, posix=False)
        except ValueError:
            # Malformed quoting — fallback to raw prefix
            return NormalizedIntent(action="UNKNOWN", target=cmd[:40], tool=tool_name)
        segments = cls._split_compound(all_tokens)

        # Step 2: Find the action-bearing segment
        action_tokens = cls._find_action_segment(segments)
        if not action_tokens:
            return NormalizedIntent(action="UNKNOWN", target="", tool=tool_name)

        # Step 3: Strip trailing redirect tokens
        clean = cls._strip_redirect_tail(action_tokens)
        if not clean:
            return NormalizedIntent(action="UNKNOWN", target="", tool=tool_name)

        # Step 4: Classify action from executable
        executable = clean[0].lower()
        first_arg = clean[1] if len(clean) > 1 else None
        action = cls._classify_action(executable, first_arg)

        # Step 5: Extract target
        target = cls._extract_target(action, clean)

        return NormalizedIntent(action=action, target=target, tool=tool_name)

    # ── Pipeline helpers ───────────────────────────────────────────────

    @classmethod
    def _split_compound(cls, tokens: List[str]) -> List[List[str]]:
        """Split a flat token list on ``&&`` and ``;`` into segments."""
        segments: List[List[str]] = []
        current: List[str] = []
        for tok in tokens:
            if tok in ('&&', ';'):
                if current:
                    segments.append(current)
                    current = []
            else:
                current.append(tok)
        if current:
            segments.append(current)
        return segments

    @classmethod
    def _find_action_segment(cls, segments: List[List[str]]) -> List[str]:
        """Return the first segment whose first token is not an env prefix."""
        for seg in segments:
            if seg and seg[0].lower() not in cls._ENV_PREFIX_COMMANDS:
                return seg
        return []

    @classmethod
    def _strip_redirect_tail(cls, tokens: List[str]) -> List[str]:
        """Remove trailing shell redirect operators and their targets."""
        result = list(tokens)
        while result:
            # Two-token: redirect_op filename
            if len(result) >= 2 and result[-2].lower() in cls._REDIRECT_OPS:
                result.pop()
                result.pop()
                continue
            # Two-token: ... 2>&1 (special redirect operator)
            if len(result) >= 2 and result[-2] == '2>&1':
                result.pop()
                result.pop()
                continue
            # Single-token: 2>&1, >file, <file, >nul
            last = result[-1]
            if last == '2>&1' or last.startswith('>') or last.startswith('<'):
                result.pop()
                continue
            # Pipe tail: | type nul (Windows idiom)
            if len(result) >= 3 and result[-3] == '|' and result[-2].lower() == 'type':
                result.pop()
                result.pop()
                result.pop()
                continue
            break
        return result

    @classmethod
    def _classify_action(cls, executable: str, first_arg: Optional[str]) -> str:
        """Classify action from executable name and first argument."""
        # Package installers
        if executable in ('pip', 'pip3'):
            if first_arg in ('list', 'show'):
                return 'PACKAGE_QUERY'
            return 'INSTALL_PACKAGE'
        if executable in ('npm', 'npx'):
            if first_arg == 'install':
                return 'INSTALL_PACKAGE'
            return 'EXECUTE'
        if executable == 'conda':
            if first_arg == 'install':
                return 'INSTALL_PACKAGE'
            return 'EXECUTE'
        if executable in ('brew', 'choco', 'apt-get', 'apt', 'yum'):
            if first_arg == 'install':
                return 'INSTALL_PACKAGE'
            return 'EXECUTE'
        # Network
        if executable in ('curl', 'wget'):
            return 'NETWORK_DOWNLOAD'
        # Runtimes / interpreters
        if executable in ('python', 'py', 'python3', 'node', 'ruby', 'perl', 'bash'):
            return 'EXECUTE'
        # Compilers
        if executable in ('gcc', 'g++', 'clang', 'rustc'):
            return 'COMPILE'
        if executable == 'go':
            if first_arg in ('build',):
                return 'COMPILE'
            return 'EXECUTE'
        if executable in ('make', 'cmake'):
            return 'COMPILE'
        # VCS
        if executable == 'git':
            return 'VCS'
        # Default
        return 'EXECUTE'

    @classmethod
    def _extract_target(cls, action: str, tokens: List[str]) -> str:
        """Extract the key target (file/package/subcommand) from cleaned tokens."""
        if len(tokens) <= 1:
            return tokens[0] if tokens else ""

        executable = tokens[0].lower()

        if action == 'VCS':
            # git status → "git status", git log → "git log"
            if len(tokens) > 1 and not tokens[1].startswith('-'):
                return f"git {tokens[1]}"
            return "git"

        if action == 'INSTALL_PACKAGE':
            # pip install pygame → "pygame"
            for i, tok in enumerate(tokens):
                if tok == 'install' and i + 1 < len(tokens):
                    if not tokens[i + 1].startswith('-'):
                        return tokens[i + 1]
            return "install"

        if action == 'PACKAGE_QUERY':
            for tok in tokens:
                if tok in ('list', 'show'):
                    return tok
            return "query"

        # EXECUTE / COMPILE / NETWORK_DOWNLOAD / default
        # Walk past executable and all flags to find the target argument
        i = 1
        while i < len(tokens):
            tok = tokens[i]
            if tok == '-m' and i + 1 < len(tokens):
                # python -m pytest → module name is the target
                return tokens[i + 1]
            if tok == '-c' and i + 1 < len(tokens):
                # python -c "code" → hash of -c content to distinguish
                # genuinely different inline commands (e.g. compiling
                # different files). Same content = same hash = same intent.
                code_hash = hashlib.md5(
                    tokens[i + 1].encode()
                ).hexdigest()[:8]
                return f"-c:{code_hash}"
            if tok.startswith('-'):
                # Flag: skip it and its value (if the value is not itself a flag)
                i += 1
                if i < len(tokens) and not tokens[i].startswith('-'):
                    i += 1
                continue
            return tok
        # If the command contains only flags, keep the executable in the
        # fingerprint. Otherwise `java -version` and `mvn -version` collapse
        # to the same `-version` intent and valid exploration gets blocked.
        return " ".join(tokens)[:160]


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
        max_recent: int = 6,
        min_occurrences: int = 4,
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

        if len(self.recent_intents) < self.min_occurrences:
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
        recent_keys = [i.to_key() for i in self.recent_intents[-self.max_recent:]]
        return (
            f"[重复操作提醒 — 本次调用未执行]\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"意图键: {intent.to_key()}\n"
            f"最近窗口 ({len(recent_keys)}): {', '.join(recent_keys)}\n"
            f"\n"
            f"检测到你正在重复相同的操作：\n"
            f"  操作: {intent.action}\n"
            f"  目标: {intent.target}\n"
            f"\n"
            f"系统判断该操作在最近窗口中重复出现，因此本次调用未执行。\n"
            f"请结合前面的工具结果自行判断：是更换策略、调整参数，还是确实需要重试。\n"
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
        key = self._failure_key(tool_name, args, intent)

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

    @staticmethod
    def _failure_key(
        tool_name: str,
        args: Dict[str, Any],
        intent: NormalizedIntent,
    ) -> str:
        """Group failed local edits by file while preserving intent dedup keys."""
        if tool_name == "edit_file":
            target = CommandNormalizer._extract_base_path(args) or "edit_file"
            return f"edit_file:EDIT:{target}"
        return intent.to_key()

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
