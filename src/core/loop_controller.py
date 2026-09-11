"""Unified runtime liveness policy and the canonical tool-attempt event stream.

CommandNormalizer creates deterministic intent keys.  RuntimePolicy consumes
AttemptHistory-derived evidence and is the only component that chooses a
runtime action.  CircuitBreaker remains only as the actuator for HARD_STOP.
"""
from __future__ import annotations
import shlex
import json
import hashlib
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, replace
from enum import Enum
from typing import Deque, Dict, List, Optional, Any, Iterable
from urllib.parse import urlsplit

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

    @classmethod
    def subject_key(cls, tool_name: str, args: Dict[str, Any]) -> str:
        """Return a small, deterministic identity for cross-tool resolution."""
        args = args if isinstance(args, dict) else {}
        if tool_name == "health_check":
            port = args.get("port")
            if port is not None:
                return f"service://localhost:{int(port)}"
            url = str(args.get("url", ""))
            if url:
                return cls._service_subject(url)
            return ""

        command = str(args.get("command", "")) if tool_name == "bash" else ""
        urls = re.findall(r"https?://[^\s'\"]+", command, re.IGNORECASE)
        if urls:
            return cls._service_subject(urls[0])

        intent = cls.normalize(tool_name, args)
        if re.search(r"\bpytest\b", command, re.IGNORECASE):
            return f"test://pytest/{intent.target or 'full'}"
        if intent.action == "INSTALL_PACKAGE" and intent.target:
            return f"dependency://{intent.target.lower()}"
        return ""

    @staticmethod
    def _service_subject(url: str) -> str:
        parsed = urlsplit(url if "://" in url else f"http://{url}")
        if not parsed.hostname:
            return ""
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        return f"service://{parsed.hostname.lower()}:{port}"

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
#  Unified attempt facts and policy
# ──────────────────────────────────────────────────────────────────────

class AttemptStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    BLOCKED = "BLOCKED"


class RuntimeDecision(str, Enum):
    ALLOW = "ALLOW"
    SOFT_BLOCK = "SOFT_BLOCK"
    REPLAN = "REPLAN"
    HARD_STOP = "HARD_STOP"


@dataclass(frozen=True)
class AttemptEvent:
    """One immutable fact about one proposed Tool Attempt."""
    sequence: int
    turn: int
    tool_name: str
    intent_key: str
    args_fingerprint: str
    status: AttemptStatus
    execution_success: bool = True
    observed_failure: bool = False
    semantic_status: str = ""
    observation: str = ""
    exit_code: Optional[int] = None
    segment_exit_codes: tuple[int, ...] = ()
    failure_category: str = ""
    recoverability: str = ""
    strategy_fingerprint: str = ""
    workspace_changed: bool = False
    observation_fingerprint: str = ""
    block_reason: str = ""
    duration_ms: float = 0.0
    timestamp: float = 0.0
    changed_paths: tuple[str, ...] = ()
    semantic_state: str = ""
    verification_improved: bool = False
    subject_key: str = ""
    resolution_key: str = ""
    resolution_reason: str = ""
    governance_decision: str = ""
    governance_reason: str = ""
    completion_decision: str = ""
    completion_reason: str = ""
    workspace_before_digest: str = ""
    workspace_after_digest: str = ""
    # Evaluator-issued observation references are annotations only.  Runtime
    # policy never reads these fields when choosing an action.
    evidence_ids: tuple[str, ...] = ()
    governance_evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sequence": self.sequence, "turn": self.turn,
            "tool_name": self.tool_name, "intent_key": self.intent_key,
            "args_fingerprint": self.args_fingerprint,
            "status": self.status.value,
            "execution_success": self.execution_success,
            "observed_failure": self.observed_failure,
            "semantic_status": self.semantic_status,
            "observation": self.observation,
            "exit_code": self.exit_code,
            "segment_exit_codes": list(self.segment_exit_codes),
            "failure_category": self.failure_category,
            "recoverability": self.recoverability,
            "strategy_fingerprint": self.strategy_fingerprint,
            "workspace_changed": self.workspace_changed,
            "observation_fingerprint": self.observation_fingerprint,
            "block_reason": self.block_reason, "duration_ms": round(self.duration_ms, 1),
            "timestamp": round(self.timestamp, 3), "changed_paths": list(self.changed_paths),
            "semantic_state": self.semantic_state,
            "verification_improved": self.verification_improved,
            "subject_key": self.subject_key,
            "resolution_key": self.resolution_key,
            "resolution_reason": self.resolution_reason,
            "governance_decision": self.governance_decision,
            "governance_reason": self.governance_reason,
            "completion_decision": self.completion_decision,
            "completion_reason": self.completion_reason,
            "workspace_before_digest": self.workspace_before_digest,
            "workspace_after_digest": self.workspace_after_digest,
            "evidence_ids": list(self.evidence_ids),
            "governance_evidence_ids": list(self.governance_evidence_ids),
        }


class AttemptHistory:
    """The only runtime fact store. Detectors read it; they never own history."""

    def __init__(self, maxlen: int = 32):
        self.maxlen = maxlen
        self._events: Deque[AttemptEvent] = deque(maxlen=maxlen)
        self._next_sequence = 1

    def append(self, event: AttemptEvent) -> AttemptEvent:
        if event.sequence != self._next_sequence:
            event = AttemptEvent(**{**event.__dict__, "sequence": self._next_sequence})
        self._events.append(event)
        self._next_sequence += 1
        return event

    def add(self, *, turn: int, tool_name: str, args_fingerprint: str,
            intent_key: str = "",
            status: AttemptStatus, failure_category: str = "",
            recoverability: str = "", strategy_fingerprint: str = "",
            workspace_changed: bool = False, observation_fingerprint: str = "",
            block_reason: str = "", duration_ms: float = 0.0,
            execution_success: Optional[bool] = None, observed_failure: bool = False,
            semantic_status: str = "", observation: str = "",
            changed_paths: Iterable[str] = (), exit_code: Optional[int] = None,
            segment_exit_codes: Iterable[int] = ()) -> AttemptEvent:
        event = AttemptEvent(
            sequence=self._next_sequence, turn=turn, tool_name=tool_name,
            intent_key=intent_key or f"{tool_name}:UNKNOWN:", args_fingerprint=args_fingerprint,
            status=status,
            execution_success=(status is AttemptStatus.SUCCESS
                               if execution_success is None else execution_success),
            observed_failure=observed_failure, semantic_status=semantic_status,
            observation=observation, exit_code=exit_code,
            segment_exit_codes=tuple(segment_exit_codes),
            failure_category=failure_category,
            recoverability=recoverability, strategy_fingerprint=strategy_fingerprint,
            workspace_changed=workspace_changed,
            observation_fingerprint=observation_fingerprint,
            block_reason=block_reason, duration_ms=duration_ms,
            timestamp=time.time(), changed_paths=tuple(changed_paths),
        )
        return self.append(event)

    def record(self, *, turn: int, tool_name: str, intent_key: str,
               args_fingerprint: str, status: AttemptStatus,
               failure_category: str = "", recoverability: str = "",
               strategy_fingerprint: str = "", workspace_changed: bool = False,
               observation_fingerprint: str = "", block_reason: str = "",
               duration_ms: float = 0.0, changed_paths: Iterable[str] = (),
               timestamp: Optional[float] = None, semantic_state: str = "",
               verification_improved: bool = False, execution_success: Optional[bool] = None,
               observed_failure: bool = False, semantic_status: str = "",
               observation: str = "", exit_code: Optional[int] = None,
               segment_exit_codes: Iterable[int] = ()) -> AttemptEvent:
        return self.append(AttemptEvent(
            sequence=self._next_sequence, turn=turn, tool_name=tool_name,
            intent_key=intent_key, args_fingerprint=args_fingerprint,
            status=status,
            execution_success=(status is AttemptStatus.SUCCESS
                               if execution_success is None else execution_success),
            observed_failure=observed_failure, semantic_status=semantic_status,
            observation=observation, exit_code=exit_code,
            segment_exit_codes=tuple(segment_exit_codes),
            failure_category=failure_category,
            recoverability=recoverability, strategy_fingerprint=strategy_fingerprint,
            workspace_changed=workspace_changed,
            observation_fingerprint=observation_fingerprint,
            block_reason=block_reason, duration_ms=duration_ms,
            timestamp=timestamp or time.time(), changed_paths=tuple(changed_paths),
            semantic_state=semantic_state, verification_improved=verification_improved,
        ))

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self._events)

    def all(self) -> tuple[AttemptEvent, ...]:
        return tuple(self._events)

    def recent(self, horizon: int) -> tuple[AttemptEvent, ...]:
        return tuple(list(self._events)[-horizon:])

    def update_last(self, **changes: Any) -> AttemptEvent | None:
        """Attach derived policy facts to the last stream entry."""
        if not self._events:
            return None
        updated = replace(self._events[-1], **changes)
        self._events[-1] = updated
        return updated

    def reset(self) -> None:
        self._events.clear()
        self._next_sequence = 1


@dataclass(frozen=True)
class LoopEvidence:
    suspected: bool = False
    kind: str = ""
    occurrences: int = 0
    horizon: int = 0
    reason: str = ""


class LoopDetector:
    """Stateless loop/observation detector over AttemptHistory."""

    def __init__(self, intent_horizon: int = 8):
        self.intent_horizon = intent_horizon

    def inspect(self, history: AttemptHistory, candidate: AttemptEvent | None = None) -> LoopEvidence:
        raw_events = list(history.recent(self.intent_horizon))
        events = list(raw_events)
        if candidate is not None:
            events.append(candidate)
        if not events:
            return LoopEvidence()
        current = events[-1]
        same = [event for event in events if event.intent_key == current.intent_key]
        if len(same) >= 4 and not self._has_intervening_change(events, same[-4:]):
            if len({event.observation_fingerprint for event in same[-4:]}) == 1:
                return LoopEvidence(True, "OBSERVATION_STAGNATION", len(same), len(events),
                                    "same intent returned equivalent observation")
            return LoopEvidence(True, "INTENT_REPETITION", len(same), len(events),
                                "same intent repeated in a short window")
        if current.tool_name in {"edit_file", "write_file"}:
            writes = events[-3:]
            if len(writes) >= 2 and all(
                event.tool_name in {"edit_file", "write_file"}
                and event.status is AttemptStatus.SUCCESS
                and not event.workspace_changed for event in writes
            ):
                return LoopEvidence(True, "NOOP_MUTATION", len(writes), len(events),
                                    "consecutive write attempts produced no diff")
        # Explicit state markers (for example ``OBSERVED:A``) are stronger
        # evidence than a changing file digest.  A probe can mutate a file on
        # every run while still oscillating between the same business states.
        state_values = []
        for event in raw_events[-8:]:
            if event.semantic_state:
                state_values.extend(part for part in event.semantic_state.split("|") if part)
        observations = state_values or [event.observation_fingerprint for event in events[-6:]
                                        if event.observation_fingerprint]
        for period in (2, 3):
            if len(observations) >= period * 2:
                left = observations[-period * 2:-period]
                right = observations[-period:]
                if left == right and len(set(right)) > 1:
                    return LoopEvidence(True, "STATE_OSCILLATION", len(observations), len(events),
                                        f"observation cycle of period {period} repeated")
        return LoopEvidence()

    @staticmethod
    def _has_intervening_change(events: list[AttemptEvent], matching: list[AttemptEvent]) -> bool:
        """Use mutation as weak counter-evidence, never as history erasure."""
        if len(matching) < 2:
            return False
        positions = [events.index(event) for event in matching]
        return any(events[i].workspace_changed for i in range(positions[0] + 1, len(events)))


@dataclass(frozen=True)
class FailureEvidence:
    category: str = ""
    occurrences: int = 0
    strategy_diversity: int = 0
    horizon: int = 0
    reason: str = ""


class FailureRecurrenceDetector:
    """Compute recent failure recurrence from the shared stream."""

    def __init__(self, horizon: int = 16):
        self.horizon = horizon

    def inspect(self, history: AttemptHistory, category: str) -> FailureEvidence:
        if not category:
            return FailureEvidence(horizon=self.horizon)
        events = list(history.recent(self.horizon))
        events = [event for event in events
                  if event.failure_category == category
                  and (event.status is not AttemptStatus.SUCCESS or event.observed_failure)]
        strategies = {event.strategy_fingerprint for event in events if event.strategy_fingerprint}
        return FailureEvidence(category, len(events), len(strategies), self.horizon,
                                f"recent {category} failures={len(events)}, strategies={len(strategies)}")


@dataclass(frozen=True)
class RuntimePolicyDecision:
    action: RuntimeDecision = RuntimeDecision.ALLOW
    reason: str = ""
    loop: LoopEvidence = LoopEvidence()
    failure: FailureEvidence = FailureEvidence()

    @property
    def should_block(self) -> bool:
        return self.action in {RuntimeDecision.SOFT_BLOCK, RuntimeDecision.HARD_STOP}


class RuntimePolicy:
    """Single decision point for ALLOW/SOFT_BLOCK/REPLAN/HARD_STOP."""

    def __init__(self, history: Optional[AttemptHistory] = None):
        self.history = history if history is not None else AttemptHistory()
        self.loop_detector = LoopDetector()
        self.failure_detector = FailureRecurrenceDetector()

    def reset(self) -> None:
        self.history.reset()

    @staticmethod
    def _replans(events: Iterable[AttemptEvent]) -> int:
        """Read prior replans from the event stream, not object-local state."""
        return sum(event.governance_decision == RuntimeDecision.REPLAN.value for event in events)

    def _remember_attempt_decision(self, decision: RuntimePolicyDecision) -> AttemptEvent | None:
        return self.history.update_last(
            governance_decision=decision.action.value,
            governance_reason=decision.reason,
        )

    def _remember_completion_decision(self, decision: RuntimePolicyDecision) -> AttemptEvent | None:
        return self.history.update_last(
            completion_decision=decision.action.value,
            completion_reason=decision.reason,
        )

    def before_execution(self, *, tool_name: str, args: Dict[str, Any],
                         args_fingerprint: str, turn: int) -> RuntimePolicyDecision:
        intent = CommandNormalizer.normalize(tool_name, args)
        prior = list(self.history.recent(self.loop_detector.intent_horizon))
        same = [event for event in prior if event.intent_key == intent.to_key()]
        noop_writes = [event for event in prior[-3:]
                       if event.tool_name in {"edit_file", "write_file"}
                       and event.status is AttemptStatus.SUCCESS
                       and not event.workspace_changed]
        if len(noop_writes) >= 2 and tool_name in {"edit_file", "write_file"}:
            return RuntimePolicyDecision(RuntimeDecision.REPLAN,
                "consecutive no-op writes", LoopEvidence(True, "NOOP_MUTATION", len(noop_writes), 3,
                                                          "consecutive write attempts produced no diff"))
        if len(same) >= 4 and not self.loop_detector._has_intervening_change(prior, same[-4:]):
            kind = "OBSERVATION_STAGNATION" if len({e.observation_fingerprint for e in same[-4:]}) == 1 else "INTENT_REPETITION"
            evidence = LoopEvidence(True, kind, len(same), self.loop_detector.intent_horizon,
                                    "same intent repeated without relevant state change")
            if self._replans(prior) >= 1:
                return RuntimePolicyDecision(RuntimeDecision.HARD_STOP,
                    "repeated intent after replan opportunity", evidence)
            return RuntimePolicyDecision(RuntimeDecision.REPLAN, evidence.reason, evidence)
        return RuntimePolicyDecision()

    def observe(self, event: AttemptEvent) -> RuntimePolicyDecision:
        loop = self.loop_detector.inspect(self.history)
        failure = self.failure_detector.inspect(self.history, event.failure_category)
        if (event.status is AttemptStatus.FAILURE
                and event.failure_category == "CAPABILITY_UNAVAILABLE"):
            decision = RuntimePolicyDecision(
                RuntimeDecision.HARD_STOP,
                "high-confidence capability invariant reported by the tool",
                loop, failure,
            )
            self._remember_attempt_decision(decision)
            return decision
        if loop.suspected:
            if self._replans(self.history) >= 1 and loop.occurrences >= 5:
                decision = RuntimePolicyDecision(RuntimeDecision.HARD_STOP,
                    f"{loop.kind}: {loop.reason}; replan already attempted", loop, failure)
                self._remember_attempt_decision(decision)
                return decision
            decision = RuntimePolicyDecision(RuntimeDecision.REPLAN,
                f"{loop.kind}: {loop.reason}", loop, failure)
            self._remember_attempt_decision(decision)
            return decision
        if failure.occurrences >= 3 and failure.strategy_diversity <= 1:
            # This is only a recent stagnation signal. A single failure or an
            # old task-lifetime count never has authority to terminate.
            if failure.occurrences >= 5 and self._replans(self.history) >= 1:
                decision = RuntimePolicyDecision(RuntimeDecision.HARD_STOP,
                    f"recent unrecovered failure recurrence after replan: {failure.reason}", loop, failure)
                self._remember_attempt_decision(decision)
                return decision
            decision = RuntimePolicyDecision(RuntimeDecision.REPLAN,
                f"recent failure recurrence: {failure.reason}", loop, failure)
            self._remember_attempt_decision(decision)
            return decision
        decision = RuntimePolicyDecision(RuntimeDecision.ALLOW, "recent evidence is not stagnant", loop, failure)
        self._remember_attempt_decision(decision)
        return decision

    def finalize(self) -> RuntimePolicyDecision:
        """Gate an unsupported final answer without killing a live recovery."""
        events = list(self.history)
        recent_loop = self.loop_detector.inspect(self.history)
        if recent_loop.suspected and self._replans(events) >= 1:
            decision = RuntimePolicyDecision(
                RuntimeDecision.HARD_STOP,
                f"{recent_loop.kind}: {recent_loop.reason}; replan opportunity was already given",
                recent_loop,
            )
            self._remember_completion_decision(decision)
            return decision
        unresolved_indexes = [i for i, event in enumerate(events)
                             if event.status is AttemptStatus.FAILURE or event.observed_failure]
        if not unresolved_indexes:
            decision = RuntimePolicyDecision()
            self._remember_completion_decision(decision)
            return decision

        unresolved = [
            event for index, event in enumerate(events)
            if index in unresolved_indexes
            and not any(
                later.resolution_key
                and later.resolution_key in {
                    event.intent_key,
                    event.subject_key,
                }
                for later in events[index + 1:]
            )
        ]
        if not unresolved:
            decision = RuntimePolicyDecision()
            self._remember_completion_decision(decision)
            return decision

        completion_replans = sum(
            event.completion_decision == RuntimeDecision.REPLAN.value
            for event in events
        )
        if completion_replans == 0:
            decision = RuntimePolicyDecision(
                RuntimeDecision.REPLAN,
                "final answer follows an unresolved failure; verify or recover first",
            )
        else:
            decision = RuntimePolicyDecision(
                RuntimeDecision.HARD_STOP,
                "final answer still follows an unresolved failure after replan",
            )
        self._remember_completion_decision(decision)
        return decision

    def record_attempt(self, *, turn: int, tool_name: str, intent_key: str,
                       args_fingerprint: str, success: bool,
                       result_text: str = "", failure_category: str = "",
                       recoverability: str = "", strategy_fingerprint: str = "",
                       workspace_before: Optional[Dict[str, str]] = None,
                       workspace_after: Optional[Dict[str, str]] = None,
                       block_reason: str = "", duration_ms: float = 0.0,
                       execution_success: Optional[bool] = None,
                       observed_failure: bool = False, semantic_status: str = "",
                       observation: str = "", exit_code: Optional[int] = None,
                       segment_exit_codes: Iterable[int] = (),
                       resolution_evidence: str = "", subject_key: str = "",
                       evidence_ids: Iterable[str] = ()) -> tuple[AttemptEvent, RuntimePolicyDecision]:
        """Append exactly one outcome event, then derive a decision from it."""
        before = workspace_before or {}
        after = workspace_after or {}
        changed_paths = tuple(sorted(path for path in set(before) | set(after)
                                     if before.get(path) != after.get(path)))
        before_digest = hashlib.sha256(json.dumps(dict(sorted(before.items())), sort_keys=True).encode()).hexdigest()[:16]
        after_digest = hashlib.sha256(json.dumps(dict(sorted(after.items())), sort_keys=True).encode()).hexdigest()[:16]
        normalized = " ".join(str(result_text or "").split()).lower()
        observation_fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        semantic_state = self._semantic_state(result_text)
        resolution_key, resolution_reason = self._resolution_for(
            intent_key=intent_key,
            subject_key=subject_key,
            status=AttemptStatus.SUCCESS if success else AttemptStatus.BLOCKED if block_reason else AttemptStatus.FAILURE,
            execution_success=(success if execution_success is None else execution_success),
            observed_failure=observed_failure,
            resolution_evidence=resolution_evidence,
        )
        event = self.history.append(AttemptEvent(
            sequence=self.history._next_sequence, turn=turn, tool_name=tool_name,
            intent_key=intent_key, args_fingerprint=args_fingerprint,
            status=AttemptStatus.SUCCESS if success else AttemptStatus.BLOCKED if block_reason else AttemptStatus.FAILURE,
            execution_success=(success if execution_success is None else execution_success),
            observed_failure=observed_failure, semantic_status=semantic_status,
            observation=observation, exit_code=exit_code,
            segment_exit_codes=tuple(segment_exit_codes),
            failure_category=failure_category, recoverability=recoverability,
            strategy_fingerprint=strategy_fingerprint,
            workspace_changed=bool(changed_paths),
            observation_fingerprint=observation_fingerprint, block_reason=block_reason,
            duration_ms=duration_ms, timestamp=time.time(), changed_paths=changed_paths,
            semantic_state=semantic_state,
            verification_improved=bool(resolution_key),
            subject_key=subject_key,
            resolution_key=resolution_key,
            resolution_reason=resolution_reason,
            workspace_before_digest=before_digest,
            workspace_after_digest=after_digest,
            evidence_ids=tuple(dict.fromkeys(str(item) for item in evidence_ids)),
        ))
        decision = self.observe(event)
        event = self.history.update_last(
            governance_decision=decision.action.value,
            governance_reason=decision.reason,
            governance_evidence_ids=(
                event.evidence_ids if decision.action is RuntimeDecision.HARD_STOP else ()
            ),
        ) or event
        return event, decision

    def _resolution_for(
        self, *, intent_key: str, subject_key: str, status: AttemptStatus,
        execution_success: bool, observed_failure: bool,
        resolution_evidence: str,
    ) -> tuple[str, str]:
        """Find a relevant prior failure resolved by this concrete outcome.

        A success is relevant only for the same normalized intent.  Health
        evidence is supplied by a tool-aware normalizer, so prose such as
        ``echo READY`` never reaches this path as proof of recovery.
        """
        if status is not AttemptStatus.SUCCESS or not execution_success or observed_failure:
            return "", ""
        prior = list(self.history)
        matching = [
            event for event in prior
            if (
                event.intent_key == intent_key
                or (subject_key and event.subject_key == subject_key)
            )
            and (event.status is AttemptStatus.FAILURE or event.observed_failure)
        ]
        if not matching:
            return "", ""
        latest = matching[-1]
        # An unhealthy resource needs a positive observation from the same
        # probe family.  A later process exit of zero is not enough to prove
        # that HTTP 503/404 (or another target failure) disappeared.
        if latest.semantic_status == "UNHEALTHY" and not resolution_evidence:
            return "", ""
        reason = resolution_evidence or "same normalized intent completed successfully"
        return intent_key if latest.intent_key == intent_key else subject_key, reason

    @staticmethod
    def _semantic_state(result_text: str) -> str:
        """Extract explicit, tool-produced state tokens without an LLM.

        This deliberately recognises only labelled state output.  Arbitrary
        prose is left as a fingerprint so normal explanations do not become
        accidental state machines.
        """
        normalized = " ".join(str(result_text or "").split()).lower()
        matches = re.findall(
            r"\b(?:observed|state|status|phase)\s*[:=]\s*([a-z0-9_.-]+)",
            normalized,
        )
        return "|".join(matches)

    @staticmethod
    def message(decision: RuntimePolicyDecision) -> str:
        prefix = "[Runtime Policy]"
        if decision.action is RuntimeDecision.HARD_STOP:
            return f"{prefix} HARD_STOP: {decision.reason}"
        if decision.action is RuntimeDecision.REPLAN:
            return f"{prefix} 当前操作未执行，需要重新规划：{decision.reason}"
        return f"{prefix} {decision.action.value}: {decision.reason}"


class CircuitBreaker:
    """Actuator only: execute a policy-approved hard stop."""

    def stop(self, decision: RuntimePolicyDecision) -> None:
        if decision.action is RuntimeDecision.HARD_STOP:
            raise RuntimeEscalationException(
                f"⛔ [Runtime Policy HARD_STOP] {decision.reason}"
            )


class LoopController:
    """Compatibility facade exposing the unified RuntimePolicy."""

    def __init__(self, history: Optional[AttemptHistory] = None,
                 policy: Optional[RuntimePolicy] = None, **_legacy: Any):
        self.policy = policy or RuntimePolicy(history=history)
        self.circuit_breaker = CircuitBreaker()

    @property
    def history(self) -> AttemptHistory:
        return self.policy.history

    def clear(self) -> None:
        self.policy.reset()

    def check(self, tool_name: str, args: Dict[str, Any]) -> Optional[str]:
        decision = self.policy.before_execution(
            tool_name=tool_name, args=args,
            args_fingerprint=json.dumps(args, sort_keys=True, ensure_ascii=False),
            turn=0,
        )
        return self.policy.message(decision) if decision.action is not RuntimeDecision.ALLOW else None

    def register_failure(self, *_args: Any, **_kwargs: Any) -> None:
        """Deprecated compatibility hook; outcomes are recorded once after execution."""
        return None

    @property
    def trigger_count(self) -> int:
        return sum(event.status is AttemptStatus.BLOCKED for event in self.history)


class RuntimePolicyAdapter:
    """Small compatibility surface for old trace plumbing, backed by one policy."""

    def __init__(self, policy: RuntimePolicy):
        self.policy = policy

    def reset(self) -> None:
        self.policy.reset()

    def finalize(self):
        decision = self.policy.finalize()
        return _AdapterDecision(
            event=self.policy.history.all()[-1] if self.policy.history.all() else AttemptEvent(
                0, 0, "", "", "", AttemptStatus.SUCCESS
            ),
            decision=decision,
            progress_detected=False,
        )

    def observe(self, *, turn: int, tool_name: str, intent_key: str = "",
                strategy_fingerprint: str = "", success: bool = True,
                result_text: str = "", failure_category: str = "",
                blocker_category: str = "", workspace_before=None,
                workspace_after=None, workspace_root: str = "",
                command_blocked: bool = False, block_reason: str = "",
                args_fingerprint: str = "", execution_success: Optional[bool] = None,
                observed_failure: bool = False, semantic_status: str = "",
                observation: str = "", exit_code: Optional[int] = None,
                segment_exit_codes: Iterable[int] = (), resolution_evidence: str = "",
                subject_key: str = "", evidence_ids: Iterable[str] = ()):
        event, decision = self.policy.record_attempt(
            turn=turn, tool_name=tool_name, intent_key=intent_key,
            args_fingerprint=args_fingerprint, success=success, result_text=result_text,
            failure_category=blocker_category or failure_category,
            strategy_fingerprint=strategy_fingerprint,
            workspace_before=workspace_before if isinstance(workspace_before, dict) else None,
            workspace_after=workspace_after if isinstance(workspace_after, dict) else None,
            block_reason=block_reason if command_blocked else "",
            execution_success=execution_success,
            observed_failure=observed_failure,
            semantic_status=semantic_status,
            observation=observation,
            exit_code=exit_code,
            segment_exit_codes=segment_exit_codes,
            resolution_evidence=resolution_evidence,
            subject_key=subject_key,
            evidence_ids=evidence_ids,
        )
        previous = self.policy.history.recent(2)
        progress = len(previous) < 2 or event.observation_fingerprint != previous[-2].observation_fingerprint
        return _AdapterDecision(event=event, decision=decision, progress_detected=progress)


@dataclass(frozen=True)
class _AdapterDecision:
    event: AttemptEvent
    decision: RuntimePolicyDecision
    progress_detected: bool = False

    @property
    def action(self):
        return self.decision.action

    @property
    def should_replan(self) -> bool:
        return self.action is RuntimeDecision.REPLAN

    @property
    def should_terminate(self) -> bool:
        return self.action is RuntimeDecision.HARD_STOP

    @property
    def reason(self) -> str:
        return self.decision.reason

    @property
    def progress_reason(self) -> tuple[str, ...]:
        return ("OBSERVATION_CHANGED",) if self.progress_detected else ()

    @property
    def stagnation_reason(self) -> tuple[str, ...]:
        return () if self.progress_detected else ("SAME_OBSERVATION",)

    @property
    def recovery_stage(self):
        return type("Stage", (), {"value": "HEALTHY" if self.progress_detected else "SUSPECTED_STALL"})()

    @property
    def open_blocker_count(self) -> int:
        return 0

    @property
    def oscillation_detected(self) -> bool:
        return False
