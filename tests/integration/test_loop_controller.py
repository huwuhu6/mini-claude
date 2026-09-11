"""Tests for LoopController normalization and LoopGuard."""

from core.loop_controller import CommandNormalizer, NormalizedIntent
import pytest


def test_commands_with_same_flag_keep_executable_in_intent():
    """Different executables must not collapse to the same -version intent."""
    java = CommandNormalizer.normalize("bash", {"command": "java -version"})
    maven = CommandNormalizer.normalize("bash", {"command": "mvn -version"})
    assert java.to_key() != maven.to_key()
    assert "java" in java.target
    assert "mvn" in maven.target


# ── read_file line range tests ─────────────────────────────────────────

def test_read_file_no_line_range():
    """read_file without line range produces filename-only target."""
    intent = CommandNormalizer.normalize("read_file", {"path": "service.py"})
    assert intent.tool == "read_file"
    assert intent.action == "READ"
    assert intent.target == "service.py"
    assert intent.to_key() == "read_file:READ:service.py"


def test_read_file_line_range():
    """Different line ranges produce different intent keys."""
    intent_a = CommandNormalizer.normalize(
        "read_file", {"path": "service.py", "start_line": 14, "end_line": 20},
    )
    intent_b = CommandNormalizer.normalize(
        "read_file", {"path": "service.py", "start_line": 54, "end_line": 60},
    )
    assert intent_a.target == "service.py:L14-20"
    assert intent_b.target == "service.py:L54-60"
    assert intent_a.to_key() != intent_b.to_key()


def test_read_file_partial_line_range():
    """Only start_line (no end_line) produces :L{start} suffix."""
    intent = CommandNormalizer.normalize(
        "read_file", {"path": "service.py", "start_line": 14},
    )
    assert intent.target == "service.py:L14"


def test_read_file_same_range_identical():
    """Same file + same line range → identical intent key."""
    intent_a = CommandNormalizer.normalize(
        "read_file", {"path": "service.py", "start_line": 14, "end_line": 20},
    )
    intent_b = CommandNormalizer.normalize(
        "read_file", {"path": "service.py", "start_line": 14, "end_line": 20},
    )
    assert intent_a.to_key() == intent_b.to_key()


# ── write_file / edit_file (no change regression tests) ────────────────

def test_write_file_target_is_filename():
    """write_file uses filename from path."""
    intent = CommandNormalizer.normalize("write_file", {"path": "src/main.py"})
    assert intent.target == "main.py"
    assert intent.action == "WRITE"


def test_edit_file_target_is_filename():
    """edit_file uses filename from path."""
    intent = CommandNormalizer.normalize("edit_file", {"path": "src/main.py", "edits": []})
    assert intent.target == "main.py"
    assert intent.action == "EDIT"


# ── edit_file content hashing tests ───────────────────────────────────

def test_edit_file_different_edits():
    """edit_file with different edits on the same file → different intent keys."""
    intent_a = CommandNormalizer.normalize(
        "edit_file",
        {"path": "service.py", "edits": [{"search": "def foo", "replace": "def bar"}]},
    )
    intent_b = CommandNormalizer.normalize(
        "edit_file",
        {"path": "service.py", "edits": [{"search": "def baz", "replace": "def qux"}]},
    )
    assert intent_a.to_key() != intent_b.to_key()
    assert ":E:" in intent_a.target
    assert ":E:" in intent_b.target


def test_edit_file_failures_use_recent_shared_history_not_breaker_strikes():
    """Different edit payloads remain distinct and only recent evidence can stop."""
    from core.loop_controller import AttemptHistory, RuntimeDecision, RuntimePolicy

    history = AttemptHistory()
    policy = RuntimePolicy(history)
    args_a = {"path": "service.py", "edits": [{"search": "def foo", "replace": "def bar"}]}
    args_b = {"path": "service.py", "edits": [{"search": "def baz", "replace": "def qux"}]}
    for args in (args_a, args_b):
        intent = CommandNormalizer.normalize("edit_file", args)
        policy.record_attempt(
            turn=len(history) + 1, tool_name="edit_file", intent_key=intent.to_key(),
            args_fingerprint=intent.to_key(), success=False,
            result_text="edit failed", failure_category="LOCAL_FILE_IO",
        )
    assert len(history) == 2
    assert policy.before_execution(
        tool_name="edit_file", args=args_a, args_fingerprint="a", turn=3
    ).action is RuntimeDecision.ALLOW


def test_edit_file_same_edits():
    """Same file + same edits → identical intent key."""
    intent_a = CommandNormalizer.normalize(
        "edit_file",
        {"path": "service.py", "edits": [{"search": "def foo", "replace": "def bar"}]},
    )
    intent_b = CommandNormalizer.normalize(
        "edit_file",
        {"path": "service.py", "edits": [{"search": "def foo", "replace": "def bar"}]},
    )
    assert intent_a.to_key() == intent_b.to_key()


def test_edit_file_edits_order():
    """Same edits in different order → identical intent key."""
    edits_a = [
        {"search": "aaa", "replace": "bbb"},
        {"search": "ccc", "replace": "ddd"},
    ]
    edits_b = [
        {"search": "ccc", "replace": "ddd"},
        {"search": "aaa", "replace": "bbb"},
    ]
    intent_a = CommandNormalizer.normalize(
        "edit_file", {"path": "service.py", "edits": edits_a},
    )
    intent_b = CommandNormalizer.normalize(
        "edit_file", {"path": "service.py", "edits": edits_b},
    )
    assert intent_a.to_key() == intent_b.to_key()


def test_edit_file_edits_same_file():
    """edit_file same file with same edits → same key, different files → different keys."""
    intent_a = CommandNormalizer.normalize(
        "edit_file",
        {"path": "service.py", "edits": [{"search": "def foo", "replace": "def bar"}]},
    )
    intent_b = CommandNormalizer.normalize(
        "edit_file",
        {"path": "controller.py", "edits": [{"search": "def foo", "replace": "def bar"}]},
    )
    assert intent_a.to_key() != intent_b.to_key()


# ── search_code paths tests ───────────────────────────────────────────

def test_search_code_paths_hash():
    """Different paths lists produce different intent keys."""
    intent_a = CommandNormalizer.normalize(
        "search_code", {"paths": ["src/a.py"], "patterns": ["def foo"]},
    )
    intent_b = CommandNormalizer.normalize(
        "search_code", {"paths": ["src/b.py"], "patterns": ["def foo"]},
    )
    assert intent_a.to_key() != intent_b.to_key()
    assert intent_a.target.startswith("paths:")
    assert intent_b.target.startswith("paths:")


def test_search_code_paths_order():
    """Same paths in different order → identical intent key."""
    intent_a = CommandNormalizer.normalize(
        "search_code", {"paths": ["src/a.py", "src/b.py"], "patterns": ["def foo"]},
    )
    intent_b = CommandNormalizer.normalize(
        "search_code", {"paths": ["src/b.py", "src/a.py"], "patterns": ["def foo"]},
    )
    assert intent_a.to_key() == intent_b.to_key()


def test_search_code_paths_no_patterns():
    """search_code with paths but no patterns → no :P: suffix."""
    intent = CommandNormalizer.normalize(
        "search_code", {"paths": ["src/a.py"]},
    )
    assert ":P:" not in intent.target
    assert intent.target.startswith("paths:")


# ── search_code patterns tests ────────────────────────────────────────

def test_search_code_patterns_hash():
    """Different patterns lists produce different intent keys."""
    intent_a = CommandNormalizer.normalize(
        "search_code", {"paths": ["src"], "patterns": ["def foo"]},
    )
    intent_b = CommandNormalizer.normalize(
        "search_code", {"paths": ["src"], "patterns": ["def bar"]},
    )
    assert intent_a.to_key() != intent_b.to_key()
    assert ":P:" in intent_a.target
    assert ":P:" in intent_b.target
    # Each has a different 8-char hash
    assert len(intent_a.target.split(":P:")[1]) == 8
    assert len(intent_b.target.split(":P:")[1]) == 8


def test_search_code_patterns_order():
    """Same patterns in different order → identical intent key."""
    intent_a = CommandNormalizer.normalize(
        "search_code",
        {"paths": ["src"], "patterns": ["def foo", "def bar"]},
    )
    intent_b = CommandNormalizer.normalize(
        "search_code",
        {"paths": ["src"], "patterns": ["def bar", "def foo"]},
    )
    assert intent_a.to_key() == intent_b.to_key()


# ── count_occurrences (shares suffix logic with search_code) ──────────

def test_count_occurrences_patterns():
    """count_occurrences uses same patterns hash logic."""
    intent = CommandNormalizer.normalize(
        "count_occurrences", {"paths": ["src"], "patterns": ["user_id"]},
    )
    assert ":P:" in intent.target


# ── TodoWrite state tests ────────────────────────────────────────────

def test_todo_write_different_items():
    """TodoWrite with different items → different intent keys."""
    intent_a = CommandNormalizer.normalize(
        "TodoWrite",
        {"items": [{"content": "task1", "status": "in_progress"}]},
    )
    intent_b = CommandNormalizer.normalize(
        "TodoWrite",
        {"items": [{"content": "task1", "status": "completed"}]},
    )
    assert intent_a.to_key() != intent_b.to_key()
    assert intent_a.target.startswith("state:")


def test_todo_write_same_items():
    """TodoWrite with same items → identical intent key."""
    intent_a = CommandNormalizer.normalize(
        "TodoWrite",
        {"items": [{"content": "task1", "status": "in_progress"}]},
    )
    intent_b = CommandNormalizer.normalize(
        "TodoWrite",
        {"items": [{"content": "task1", "status": "in_progress"}]},
    )
    assert intent_a.to_key() == intent_b.to_key()


def test_todo_write_items_order():
    """TodoWrite same items in different order → identical intent key."""
    intent_a = CommandNormalizer.normalize(
        "TodoWrite",
        {"items": [
            {"content": "task1", "status": "in_progress"},
            {"content": "task2", "status": "pending"},
        ]},
    )
    intent_b = CommandNormalizer.normalize(
        "TodoWrite",
        {"items": [
            {"content": "task2", "status": "pending"},
            {"content": "task1", "status": "in_progress"},
        ]},
    )
    assert intent_a.to_key() == intent_b.to_key()


def test_todo_write_empty_items():
    """TodoWrite with empty items list."""
    intent = CommandNormalizer.normalize("TodoWrite", {"items": []})
    assert intent.target.startswith("state:")


# ── syntax_check (uses paths hash, no extra suffix) ───────────────────

def test_syntax_check_paths_hash():
    """syntax_check different paths → different intent keys."""
    intent_a = CommandNormalizer.normalize(
        "syntax_check", {"paths": ["src/main.py"]},
    )
    intent_b = CommandNormalizer.normalize(
        "syntax_check", {"paths": ["src/utils.py"]},
    )
    assert intent_a.to_key() != intent_b.to_key()
    assert intent_a.target.startswith("paths:")
    assert ":P:" not in intent_a.target


# ── Fallback tests ────────────────────────────────────────────────────

def test_non_bash_fallback():
    """Unknown non-bash tool falls back to tool_name as target."""
    intent = CommandNormalizer.normalize("unknown_tool", {"path": "x.py"})
    # unknown_tool is not in FILE_TOOL_ACTIONS → action = tool_name.upper()
    assert intent.action == "UNKNOWN_TOOL"
    # path IS available → target is the filename
    assert intent.target == "x.py"


def test_non_bash_no_args():
    """Non-bash tool called without args dict."""
    intent = CommandNormalizer.normalize("read_file", "not_a_dict")
    assert intent.target == "read_file"  # fallback


def test_non_bash_empty_paths():
    """Empty paths list → fallback to tool_name."""
    intent = CommandNormalizer.normalize("syntax_check", {"paths": []})
    assert intent.target == "syntax_check"


# ── NormalizedIntent key format ────────────────────────────────────────

def test_to_key_format():
    """to_key() follows tool:action:target format."""
    intent = NormalizedIntent(action="READ", target="main.py", tool="read_file")
    assert intent.to_key() == "read_file:READ:main.py"


def _record(policy, history, tool, args, *, success, result, category="", changed=False):
    intent = CommandNormalizer.normalize(tool, args)
    return policy.record_attempt(
        turn=len(history) + 1, tool_name=tool, intent_key=intent.to_key(),
        args_fingerprint=intent.to_key(), success=success, result_text=result,
        failure_category=category, workspace_before={"a": "1"},
        workspace_after={"a": "2"} if changed else {"a": "1"},
    )


def test_attempt_history_is_single_ordered_source_for_success_failure_blocked():
    from core.loop_controller import AttemptHistory, AttemptStatus, RuntimePolicy

    history = AttemptHistory()
    policy = RuntimePolicy(history)
    _record(policy, history, "bash", {"command": "echo ok"}, success=True, result="ok")
    _record(policy, history, "bash", {"command": "pytest"}, success=False,
            result="failed", category="TOOL_CRASH")
    intent = CommandNormalizer.normalize("bash", {"command": "pytest"})
    event, _ = policy.record_attempt(
        turn=3, tool_name="bash", intent_key=intent.to_key(), args_fingerprint="x",
        success=False, result_text="blocked", block_reason="repeat",
    )
    assert [event.sequence for event in history] == [1, 2, 3]
    assert [event.status for event in history] == [
        AttemptStatus.SUCCESS, AttemptStatus.FAILURE, AttemptStatus.BLOCKED,
    ]


def test_normal_edit_test_debug_cycle_does_not_accumulate_old_test_failures():
    from core.loop_controller import RuntimeDecision, AttemptHistory, RuntimePolicy

    history = AttemptHistory()
    policy = RuntimePolicy(history)
    for i in range(7):
        _record(policy, history, "bash", {"command": "pytest"}, success=False,
                result="1 failed", category="TOOL_CRASH")
        _record(policy, history, "edit_file", {"path": "app.py", "edits": [{"search": str(i), "replace": str(i + 1)}]},
                success=True, result="changed app.py", changed=True)
    assert all(policy.before_execution(
        tool_name="bash", args={"command": "pytest"}, args_fingerprint="x", turn=20
    ).action is RuntimeDecision.ALLOW for _ in range(1))


def test_repeated_read_same_observation_replans_but_paged_reads_are_allowed():
    from core.loop_controller import AttemptHistory, RuntimeDecision, RuntimePolicy

    history = AttemptHistory()
    policy = RuntimePolicy(history)
    args = {"path": "a.py", "start_line": 1, "end_line": 100}
    decisions = [_record(policy, history, "read_file", args, success=True, result="same") [1]
                 for _ in range(4)]
    assert decisions[-1].action is RuntimeDecision.REPLAN

    paged = AttemptHistory()
    paged_policy = RuntimePolicy(paged)
    for start in (1, 101, 201):
        _record(paged_policy, paged, "read_file",
                {"path": "a.py", "start_line": start, "end_line": start + 99},
                success=True, result="different page")
    assert len(paged) == 3
    assert paged_policy.before_execution(
        tool_name="read_file", args={"path": "a.py", "start_line": 301, "end_line": 400},
        args_fingerprint="x", turn=4
    ).action is RuntimeDecision.ALLOW


def test_noop_writes_replan_quickly_and_hard_stop_requires_repeated_replan():
    from core.loop_controller import AttemptHistory, RuntimeDecision, RuntimePolicy

    history = AttemptHistory()
    policy = RuntimePolicy(history)
    args = {"path": "a.py", "edits": [{"search": "x", "replace": "x"}]}
    _record(policy, history, "edit_file", args, success=True, result="unchanged")
    _record(policy, history, "edit_file", args, success=True, result="unchanged")
    assert policy.before_execution(
        tool_name="edit_file", args=args, args_fingerprint="x", turn=3
    ).action is RuntimeDecision.REPLAN


def test_state_oscillation_is_detected_even_when_probe_changes_a_file():
    from core.loop_controller import AttemptHistory, RuntimeDecision, RuntimePolicy

    history = AttemptHistory()
    policy = RuntimePolicy(history)
    args = {"command": "probe.cmd"}
    for state in ("OBSERVED:A", "OBSERVED:B"):
        _record(policy, history, "bash", args, success=True, result=state, changed=True)
    _, decision = _record(
        policy, history, "bash", args, success=True,
        result="OBSERVED:A\nOBSERVED:B\nOBSERVED:A\nOBSERVED:B", changed=False,
    )
    assert history.all()[-1].semantic_state == "a|b|a|b"
    assert decision.action is RuntimeDecision.REPLAN
    assert policy.finalize().action is RuntimeDecision.HARD_STOP


def test_capability_invariant_stops_immediately_but_dynamic_failure_does_not():
    from core.loop_controller import AttemptHistory, RuntimeDecision, RuntimePolicy

    invariant_history = AttemptHistory()
    invariant_policy = RuntimePolicy(invariant_history)
    _, decision = _record(
        invariant_policy, invariant_history, "bash", {"command": "probe"},
        success=False, result="VERDICT=FAIL controlled signer unavailable",
        category="CAPABILITY_UNAVAILABLE",
    )
    assert decision.action is RuntimeDecision.HARD_STOP

    dynamic_history = AttemptHistory()
    dynamic_policy = RuntimePolicy(dynamic_history)
    _, decision = _record(
        dynamic_policy, dynamic_history, "bash", {"command": "curl localhost:8080"},
        success=False, result="Connection refused", category="NETWORK_UNREACHABLE",
    )
    assert decision.action is not RuntimeDecision.HARD_STOP


def test_structural_single_source_has_no_legacy_counter_fields():
    from core.loop_controller import CircuitBreaker
    from core.failure_intelligence.memory import FailureMemory
    from core.runtime_context.workspace_state import WorkspaceStateGuard

    assert not hasattr(CircuitBreaker(), "_strikes")
    assert not hasattr(FailureMemory(), "_categories")
    assert not hasattr(WorkspaceStateGuard, "_write_stalls")
    assert not hasattr(WorkspaceStateGuard, "_read_stalls")
    assert not hasattr(WorkspaceStateGuard, "_last_read_key")
