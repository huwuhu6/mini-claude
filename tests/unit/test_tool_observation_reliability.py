"""Deterministic contracts for safe, honest tool observations."""

import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent.mini_claude_agent import MiniClaudeAgent
from core.runtime_context.observation import ObservationNormalizer
from core.runtime_context.shell_session import ShellSession
from core.runtime_context.workspace_authority import WorkspaceAuthority
from core.tools.base_tools import BaseTools, ToolResult
from core.tools.registry import ToolRegistry


@pytest.fixture
def tmp_path():
    """Use a task-local workspace temp dir; the host Temp tree is restricted."""
    path = Path.cwd() / f".test-tool-observation-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        if path.resolve().parent == Path.cwd().resolve():
            shutil.rmtree(path)


def _tools(tmp_path, *, additional_root=None):
    authority = WorkspaceAuthority(tmp_path)
    if additional_root:
        assert "已添加" in authority.add_root(str(additional_root))
    return BaseTools(tmp_path, authority=authority)


def test_posix_simple_semicolon_chains_are_instrumented_conservatively(monkeypatch):
    monkeypatch.setattr("core.runtime_context.shell_session.sys.platform", "linux")

    transformed = ShellSession._instrument_composite("false; true; printf finished")
    assert transformed.count("__MINICLAUDE_SEGMENT_EXIT_") == 2
    assert "false; command printf" in transformed
    assert "true; command printf" in transformed


@pytest.mark.skipif(os.name != "posix", reason="requires a POSIX shell")
@pytest.mark.parametrize(
    ("command", "expected_segments", "expected_exit"),
    [
        ("true; false", [0], 1),
        ("false; true", [1], 0),
        ("false; true; false; true", [1, 0, 1], 0),
    ],
)
def test_posix_semicolon_chain_exit_observations(command, expected_segments, expected_exit):
    result = ShellSession(Path.cwd()).execute(command)
    assert result["exit_code"] == expected_exit
    assert result["segment_exit_codes"] == expected_segments
    assert "__MINICLAUDE_SEGMENT_EXIT_" not in result["content"]


@pytest.mark.skipif(os.name != "posix", reason="requires a POSIX shell")
def test_posix_single_command_success_and_failure_keep_final_exit_codes():
    session = ShellSession(Path.cwd())
    success = session.execute("echo ok")
    failure = session.execute("sh -c 'exit 7'")

    assert success["execution_success"] is True
    assert success["exit_code"] == 0
    assert success["segment_exit_codes"] == []
    assert failure["execution_success"] is False
    assert failure["exit_code"] == 7
    assert failure["segment_exit_codes"] == []


@pytest.mark.skipif(os.name != "posix", reason="requires a POSIX shell")
def test_posix_semicolon_inside_quotes_is_not_a_separator():
    result = ShellSession(Path.cwd()).execute("printf '%s\\n' 'left;right'; true")
    assert result["success"]
    assert result["segment_exit_codes"] == [0]
    assert "left;right" in result["stdout"]


def test_posix_heredoc_and_multiline_shell_are_left_unmodified():
    command = "cat <<'EOF'\nleft;right\nEOF\nprintf 'after\\n'"
    assert ShellSession._instrument_posix_composite(command) == command
    assert ShellSession._instrument_posix_composite("false;") == "false;"
    control = "if true; then echo ready; fi; echo done"
    assert ShellSession._instrument_posix_composite(control) == control


@pytest.mark.skipif(os.name != "posix", reason="requires a POSIX shell")
def test_posix_multiline_heredoc_then_command_keeps_original_semantics():
    command = "cat <<'EOF'\nleft;right\nEOF\nprintf 'after\\n'"
    result = ShellSession(Path.cwd()).execute(command)
    assert result["success"]
    assert result["segment_exit_codes"] == []
    assert "left;right" in result["stdout"]
    assert "after" in result["stdout"]


@pytest.mark.skipif(os.name != "posix", reason="requires a POSIX shell")
def test_posix_failing_heredoc_preserves_failure_without_segment_markers():
    command = "python3 <<'EOF'\nraise RuntimeError('boom')\nEOF"
    result = ShellSession(Path.cwd()).execute(command)

    assert result["execution_success"] is False
    assert result["exit_code"] != 0
    assert result["segment_exit_codes"] == []
    assert "RuntimeError: boom" in result["stderr"]
    assert "__MINICLAUDE_SEGMENT_EXIT_" not in result["content"]


def test_partial_shell_failure_has_stable_model_visible_and_structured_evidence(tmp_path):
    class ShellStub:
        def execute(self, *_args, **_kwargs):
            return {
                "content": "[Exit Code: 0]\nlast command succeeded",
                "success": True,
                "execution_success": True,
                "exit_code": 0,
                "stdout": "last command succeeded",
                "stderr": "",
                "segment_exit_codes": [2],
            }

    result = BaseTools(tmp_path, shell_session=ShellStub()).run_bash("echo ok")
    assert "Partial process failure" in result.content
    assert "Final shell exit code: 0" in result.content
    assert result.segment_exit_codes == [2]
    assert result.output_visibility["partial_process_failure"] is True

    evidence = ObservationNormalizer.normalize("bash", {}, result)
    assert evidence.observed_failure is True
    assert evidence.semantic_status == "PARTIAL_PROCESS_FAILURE"


@pytest.mark.skipif(os.name != "posix", reason="requires a POSIX shell")
def test_posix_partial_failure_flows_from_shell_to_runtime_and_model_observation(tmp_path):
    tools = BaseTools(tmp_path, shell_session=ShellSession(tmp_path))
    result = tools.run_bash("false ; echo ok")
    evidence = ObservationNormalizer.normalize("bash", {"command": "false ; echo ok"}, result)

    assert result.execution_success is True
    assert result.exit_code == 0
    assert result.segment_exit_codes == [1]
    assert "Partial process failure" in result.content
    assert "Final shell exit code: 0" in result.content
    assert "ok" in result.stdout
    assert evidence.semantic_status == "PARTIAL_PROCESS_FAILURE"
    assert evidence.observed_failure is True


def test_stderr_warning_alone_is_not_classified_as_process_failure():
    warning = ToolResult(
        "[Exit Code: 0]\nwarning: deprecated option",
        success=True,
        exit_code=0,
        stderr="warning: deprecated option",
    )
    evidence = ObservationNormalizer.normalize("bash", {}, warning)
    assert evidence.observed_failure is False


def test_search_rejects_traversal_absolute_outside_and_direct_symlink(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    outside_file = outside / "secret.txt"
    outside_file.write_text("needle", encoding="utf-8")
    tools = _tools(workspace)

    assert not tools.search_code(["../outside/secret.txt"], ["needle"]).success
    assert not tools.search_code([str(outside_file)], ["needle"]).success
    assert not tools.count_occurrences(["../outside/secret.txt"], ["needle"]).success
    link = workspace / "link.txt"
    try:
        link.symlink_to(outside_file)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not available")
    assert not tools.search_code(["link.txt"], ["needle"]).success


def test_search_glob_cannot_escape_authority_through_traversal_or_symlink(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("needle", encoding="utf-8")
    tools = _tools(workspace)

    assert not tools.search_code(["../outside/*.txt"], ["needle"]).success
    link_dir = workspace / "linked"
    try:
        link_dir.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not available")
    result = tools.search_code(["linked/*.txt"], ["needle"])
    assert "needle" not in result.content


def test_search_preserves_explicitly_authorized_additional_root_for_both_tools(tmp_path):
    workspace = tmp_path / "workspace"
    external = tmp_path / "authorized"
    workspace.mkdir()
    external.mkdir()
    target = external / "source.txt"
    target.write_text("needle needle", encoding="utf-8")
    tools = _tools(workspace, additional_root=external)

    searched = tools.search_code([str(target)], ["needle"])
    globbed = tools.search_code([str(external / "*.txt")], ["needle"])
    counted = tools.count_occurrences([str(target)], ["needle"])
    assert searched.success and "needle needle" in searched.content
    assert globbed.success and "needle needle" in globbed.content
    assert counted.success and "2 occurrences" in counted.content


def test_search_walk_and_glob_results_are_deterministic(tmp_path):
    (tmp_path / "z_dir").mkdir()
    (tmp_path / "a_dir").mkdir()
    (tmp_path / "z_dir" / "z.txt").write_text("needle", encoding="utf-8")
    (tmp_path / "a_dir" / "a.txt").write_text("needle", encoding="utf-8")
    tools = _tools(tmp_path)

    walked = tools.search_code(["."], ["needle"])
    globbed = tools.search_code(["*/*.txt"], ["needle"])
    assert walked.content.index("a_dir") < walked.content.index("z_dir")
    assert globbed.content.index("a_dir") < globbed.content.index("z_dir")


def test_exact_max_matches_is_complete_but_one_more_is_reported(tmp_path):
    exact = tmp_path / "exact.txt"
    exact.write_text("MATCH\n" * 50, encoding="utf-8")
    exact_result = _tools(tmp_path).search_code(["exact.txt"], ["MATCH"], max_matches=50)
    assert exact_result.output_visibility["truncated"] is False
    assert exact_result.output_visibility["returned_matching_lines"] == 50

    extra = tmp_path / "extra.txt"
    extra.write_text("MATCH\n" * 51, encoding="utf-8")
    extra_result = _tools(tmp_path).search_code(["extra.txt"], ["MATCH"], max_matches=50)
    assert extra_result.output_visibility["truncated"] is True
    assert "max_matches" in extra_result.output_visibility["truncation_reasons"]
    assert "additional_matching_lines_omitted: at least 1" in extra_result.content


def test_search_runtime_clamps_context_and_match_limit(tmp_path):
    (tmp_path / "context.txt").write_text(
        "\n".join([f"line-{index}" for index in range(1, 122)]),
        encoding="utf-8",
    )
    context_result = _tools(tmp_path).search_code(
        ["context.txt"], ["line-61"], context_lines=100,
    )
    assert "11: line-11" in context_result.content
    assert "111: line-111" in context_result.content
    assert "10: line-10" not in context_result.content
    assert "112: line-112" not in context_result.content

    (tmp_path / "matches.txt").write_text("MATCH\n" * 201, encoding="utf-8")
    capped = _tools(tmp_path).search_code(
        ["matches.txt"], ["MATCH"], max_matches=1000,
    )
    assert capped.output_visibility["returned_matching_lines"] == 200
    assert capped.output_visibility["max_matches"] == 200
    assert "max_matches" in capped.output_visibility["truncation_reasons"]


def test_search_budget_drops_complete_context_blocks_and_keeps_footer(tmp_path, monkeypatch):
    source = tmp_path / "source.txt"
    source.write_text("\n".join(
        [f"line-{index}-" + ("x" * 240) for index in range(20)]
        + ["MATCH"]
        + [f"line-{index}-" + ("y" * 240) for index in range(21, 42)]
    ), encoding="utf-8")
    monkeypatch.setattr("core.tools.base_tools.TOOL_OUTPUT_MAX_CHARS", 3500)
    monkeypatch.setattr("core.tools.base_tools.TOOL_OUTPUT_MAX_BYTES", 14000)

    result = _tools(tmp_path).search_code(["source.txt"], ["MATCH"], context_lines=20)
    assert result.output_visibility["truncated"] is True
    assert "output_budget" in result.output_visibility["truncation_reasons"]
    assert "Search visibility:" in result.content
    # The matching line and its requested context appear as one atomic block.
    assert "MATCH" not in result.content


def test_search_line_regex_does_not_match_across_newline(tmp_path):
    (tmp_path / "multiline.txt").write_text("before\nafter\n", encoding="utf-8")
    result = _tools(tmp_path).search_code(["multiline.txt"], ["before.*after"])
    assert result.output_visibility["returned_matching_lines"] == 0
    assert "0 matching lines" in result.content


def test_long_matching_line_preview_is_explicit_in_text_and_metadata(tmp_path):
    (tmp_path / "long.txt").write_text("MATCH " + ("x" * 400), encoding="utf-8")
    result = _tools(tmp_path).search_code(["long.txt"], ["MATCH"])
    assert "line preview clipped" in result.content
    assert result.output_visibility["truncated"] is True
    assert "line_preview" in result.output_visibility["truncation_reasons"]


def test_search_reports_skipped_large_files_and_metadata_consistently(tmp_path, monkeypatch):
    (tmp_path / "large.txt").write_text("MATCH" * 20, encoding="utf-8")
    (tmp_path / "small.txt").write_text("MATCH", encoding="utf-8")
    monkeypatch.setattr(BaseTools, "MAX_FILE_SIZE", 8)

    result = _tools(tmp_path).search_code(["."], ["MATCH"])
    meta = result.output_visibility
    assert "large.txt" in result.content
    assert meta["truncated"] is True
    assert meta["skipped_file_count"] == 1
    assert meta["skipped_files"][0]["reason"] == "file_size_limit"
    assert meta["returned_matching_lines"] == 1


def test_broad_search_omits_agent_runtime_data_but_explicit_paths_are_allowed(tmp_path):
    source = tmp_path / "src.txt"
    source.write_text("needle", encoding="utf-8")
    runtime_dir = tmp_path / ".agent" / "logs"
    runtime_dir.mkdir(parents=True)
    runtime_file = runtime_dir / "run.log"
    runtime_file.write_text("needle", encoding="utf-8")
    tools = _tools(tmp_path)

    broad = tools.search_code(["."], ["needle"])
    explicit_file = tools.search_code([".agent/logs/run.log"], ["needle"])
    explicit_dir = tools.search_code([".agent/logs"], ["needle"])
    assert "run.log" not in broad.content
    assert explicit_file.output_visibility["returned_matching_lines"] == 1
    assert explicit_dir.output_visibility["returned_matching_lines"] == 1


def test_search_code_schema_and_tool_descriptions_state_actual_contract():
    agent = object.__new__(MiniClaudeAgent)
    agent.feature_manager = type(
        "FeatureStub", (), {"filter_tools": staticmethod(lambda tools: tools)}
    )()
    agent.tool_registry = ToolRegistry()
    agent._register_tools()
    definitions = {tool["name"]: tool for tool in agent._get_llm_tools()}

    search = definitions["search_code"]
    props = search["input_schema"]["properties"]
    assert (props["context_lines"]["minimum"], props["context_lines"]["maximum"],
            props["context_lines"]["default"]) == (0, 50, 0)
    assert (props["max_matches"]["minimum"], props["max_matches"]["maximum"],
            props["max_matches"]["default"]) == (1, 200, 50)
    assert "matching lines" in search["description"]
    assert "do not match across lines" in search["description"]
    assert "Count regex occurrences" in definitions["count_occurrences"]["description"]
    assert "known file" in definitions["read_file"]["description"]
    assert "builds/tests" in definitions["bash"]["description"]
    assert "Does not read file contents" in definitions["list_files"]["description"]


def test_file_observation_handlers_preserve_toolresult_metadata():
    agent = object.__new__(MiniClaudeAgent)
    agent.feature_manager = type(
        "FeatureStub", (), {"filter_tools": staticmethod(lambda tools: tools)}
    )()
    agent.tool_registry = ToolRegistry()
    agent._register_tools()
    facts = {
        "truncated": True,
        "truncation_reasons": ["output_budget"],
        "returned_matching_lines": 3,
    }
    expected = ToolResult("observed", output_visibility=facts)
    agent.tools = type(
        "ToolsStub", (), {
            "run_bash": staticmethod(lambda *_a, **_k: expected),
            "read_file": staticmethod(lambda *_a, **_k: expected),
            "search_code": staticmethod(lambda *_a, **_k: expected),
            "count_occurrences": staticmethod(lambda *_a, **_k: expected),
            "list_files": staticmethod(lambda *_a, **_k: expected),
        }
    )()

    for name, args in [
        ("bash", {"command": "true"}),
        ("read_file", {"path": "file.txt"}),
        ("search_code", {"patterns": ["needle"]}),
        ("count_occurrences", {"patterns": ["needle"]}),
        ("list_files", {}),
    ]:
        assert agent._execute_tool(name, args) is expected
        assert agent._execute_tool(name, args).output_visibility == facts
