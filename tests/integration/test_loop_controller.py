"""Tests for LoopController normalization and LoopGuard."""

from core.loop_controller import CommandNormalizer, NormalizedIntent


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
