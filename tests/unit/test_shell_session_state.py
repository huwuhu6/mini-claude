from pathlib import Path
import os
import shlex
import shutil
import subprocess
import sys
import uuid

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.runtime_context.shell_session import ShellSession


@pytest.fixture
def shell_workspace():
    root = Path.cwd() / f".test-shell-session-{uuid.uuid4().hex}"
    root.mkdir()
    (root / "src").mkdir()
    (root / "override" / "child").mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _python_command(code: str) -> str:
    args = [sys.executable, "-c", code]
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return shlex.join(args)


def test_standalone_cd_updates_only_after_existing_directory_is_confirmed(shell_workspace):
    session = ShellSession(shell_workspace)

    result = session.execute("cd src")

    assert result["success"] is True
    assert session.cwd == (shell_workspace / "src").resolve()
    assert result["cwd"] == str(session.cwd)


def test_missing_standalone_cd_keeps_cwd_and_does_not_spawn(shell_workspace, monkeypatch):
    session = ShellSession(shell_workspace)

    def unexpected_spawn(*_args, **_kwargs):
        raise AssertionError("a rejected literal cd should not spawn a shell")

    monkeypatch.setattr("core.runtime_context.shell_session.subprocess.run", unexpected_spawn)
    result = session.execute("cd missing")

    assert result["success"] is False
    assert result["execution_success"] is False
    assert result["exit_code"] == 1
    assert "cd: missing: No such file or directory" in result["stderr"]
    assert session.cwd == shell_workspace.resolve()
    assert session.command_history == []


def test_failed_command_before_cd_does_not_create_a_false_cwd_update(shell_workspace):
    session = ShellSession(shell_workspace)
    failing_command = _python_command("import sys; sys.exit(7)")

    result = session.execute(f"{failing_command} && cd src")

    assert result["success"] is False
    assert result["exit_code"] == 7
    assert session.cwd == shell_workspace.resolve()


def test_cd_and_command_runs_in_child_directory_without_persisting_cwd(shell_workspace):
    session = ShellSession(shell_workspace)
    command = f"cd src && {_python_command('import os; print(os.getcwd())')}"

    result = session.execute(command)

    assert result["success"] is True
    assert str((shell_workspace / "src").resolve()) in result["stdout"]
    assert session.cwd == shell_workspace.resolve()


def test_composite_failure_after_cd_does_not_guess_final_cwd(shell_workspace):
    session = ShellSession(shell_workspace)
    command = f"cd src && {_python_command('import sys; sys.exit(9)')}"

    result = session.execute(command)

    assert result["success"] is False
    assert result["exit_code"] == 9
    assert session.cwd == shell_workspace.resolve()


def test_timeout_during_composite_keeps_logical_cwd(shell_workspace, monkeypatch):
    session = ShellSession(shell_workspace)
    observed = {}

    def timeout(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("core.runtime_context.shell_session.subprocess.run", timeout)
    result = session.execute("cd src && echo after", timeout=3)

    assert result["timed_out"] is True
    assert observed["cwd"] == str(shell_workspace.resolve())
    assert session.cwd == shell_workspace.resolve()


def test_cancel_during_composite_keeps_logical_cwd(shell_workspace, monkeypatch):
    session = ShellSession(shell_workspace)
    observed = {}

    def cancel(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        raise KeyboardInterrupt()

    monkeypatch.setattr("core.runtime_context.shell_session.subprocess.run", cancel)
    result = session.execute("cd src && echo after")

    assert result["cancelled"] is True
    assert observed["cwd"] == str(shell_workspace.resolve())
    assert session.cwd == shell_workspace.resolve()


def test_cwd_override_runs_standalone_cd_without_changing_session_cwd(shell_workspace):
    session = ShellSession(shell_workspace)
    override = shell_workspace / "override"

    result = session.execute("cd child", cwd_override=override)

    assert result["success"] is True
    assert session.cwd == shell_workspace.resolve()


def test_cwd_override_runs_composite_from_override_without_changing_session_cwd(shell_workspace):
    session = ShellSession(shell_workspace)
    override = shell_workspace / "override"

    result = session.execute("cd child && echo override-only", cwd_override=override)

    assert result["success"] is True
    assert "override-only" in result["stdout"]
    assert session.cwd == shell_workspace.resolve()


def test_compound_fallback_cd_executes_but_does_not_persist(shell_workspace):
    session = ShellSession(shell_workspace)

    result = session.execute("cd missing || cd src && echo fallback-ran")

    assert result["success"] is True
    assert "fallback-ran" in result["stdout"]
    assert session.cwd == shell_workspace.resolve()


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX shell list semantics")
def test_posix_semicolon_cd_is_child_only(shell_workspace):
    session = ShellSession(shell_workspace)
    command = f"cd src; {_python_command('import os; print(os.getcwd())')}"

    result = session.execute(command)

    assert result["success"] is True
    assert str((shell_workspace / "src").resolve()) in result["stdout"]
    assert session.cwd == shell_workspace.resolve()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows CMD ampersand semantics")
def test_windows_ampersand_cd_chain_is_child_only(shell_workspace):
    session = ShellSession(shell_workspace)

    result = session.execute("cd src & echo after-ampersand")

    assert result["success"] is True
    assert "after-ampersand" in result["stdout"]
    assert session.cwd == shell_workspace.resolve()


def test_subprocess_inherits_live_parent_environment_not_session_snapshot(
    shell_workspace, monkeypatch,
):
    session = ShellSession(shell_workspace)
    variable = "MINI_CLAUDE_SHELL_SESSION_ENV_TEST"

    monkeypatch.setenv(variable, "set-after-session-creation")
    first = session.execute(
        _python_command(f"import os; print(os.environ.get('{variable}', 'missing'))")
    )
    monkeypatch.setenv(variable, "changed-before-next-call")
    second = session.execute(
        _python_command(f"import os; print(os.environ.get('{variable}', 'missing'))")
    )

    assert first["success"] is True
    assert "set-after-session-creation" in first["stdout"]
    assert second["success"] is True
    assert "changed-before-next-call" in second["stdout"]
    assert not hasattr(session, "env")


def test_reset_restores_configured_workspace_root_after_cd(shell_workspace):
    session = ShellSession(shell_workspace)
    session.execute("cd src")

    session.reset()

    assert session.workspace_root == shell_workspace.resolve()
    assert session.cwd == shell_workspace.resolve()
    assert session.command_history == []

@pytest.mark.skipif(os.name != "nt", reason="requires Windows CMD operator parsing")
def test_windows_and_and_chain_is_not_rewritten_as_a_single_ampersand():
    command = "cd src && echo after"

    assert ShellSession._instrument_composite(command) == command


@pytest.mark.skipif(os.name != "nt", reason="requires Windows CMD environment expansion")
def test_windows_expanded_cd_is_executed_but_not_persisted(shell_workspace, monkeypatch):
    session = ShellSession(shell_workspace)
    target = (shell_workspace / "src").resolve()
    monkeypatch.setenv("MINI_CLAUDE_CWD_TARGET", str(target))

    result = session.execute('cd /d "%MINI_CLAUDE_CWD_TARGET%"')

    assert result["success"] is True
    assert session.cwd == shell_workspace.resolve()


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX environment expansion")
def test_posix_expanded_cd_is_executed_but_not_persisted(shell_workspace, monkeypatch):
    session = ShellSession(shell_workspace)
    target = (shell_workspace / "src").resolve()
    monkeypatch.setenv("MINI_CLAUDE_CWD_TARGET", str(target))

    result = session.execute('cd "$MINI_CLAUDE_CWD_TARGET"')

    assert result["success"] is True
    assert session.cwd == shell_workspace.resolve()


@pytest.mark.parametrize("command", ["cd #dir", "cd src # comment", "cd src#dir"])
def test_posix_unquoted_hash_is_delegated_without_guessing_cwd(shell_workspace, monkeypatch, command):
    monkeypatch.setattr("core.runtime_context.shell_session.sys.platform", "linux")
    (shell_workspace / "#dir").mkdir()
    (shell_workspace / "src#dir").mkdir()
    session = ShellSession(shell_workspace)
    received = []
    def execute_child(command, **kwargs):
        received.append(command)
        return subprocess.CompletedProcess(command, 0, b"shell-result", b"")
    monkeypatch.setattr("core.runtime_context.shell_session.subprocess.run", execute_child)
    result = session.execute(command)
    assert received == [command]
    assert result["stdout"] == "shell-result"
    assert session.cwd == shell_workspace.resolve()


@pytest.mark.parametrize("argument", ['"#dir"', "'#dir'", r"\#dir", "src"])
def test_posix_quoted_hash_and_normal_cd_remain_literal(shell_workspace, monkeypatch, argument):
    monkeypatch.setattr("core.runtime_context.shell_session.sys.platform", "linux")
    (shell_workspace / "#dir").mkdir()
    session = ShellSession(shell_workspace)
    def unexpected_spawn(*_args, **_kwargs):
        raise AssertionError("literal cd should not spawn")
    monkeypatch.setattr("core.runtime_context.shell_session.subprocess.run", unexpected_spawn)
    result = session.execute(f"cd {argument}")
    assert result["success"] is True
    assert session.cwd == (shell_workspace / ("src" if argument == "src" else "#dir")).resolve()


@pytest.mark.parametrize("command,child_directory,persistent_directory", [
    ("cd #dir", "home", None), ("cd src # comment", "src", None),
    ("cd src#dir", "src#dir", None), ('cd "#dir"', "#dir", "#dir"),
])
def test_hash_cd_matches_real_posix_shell(shell_workspace, monkeypatch, command,
                                        child_directory, persistent_directory):
    if os.name == "nt":
        shell = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if not shell.is_file():
            pytest.skip("requires Git Bash for real POSIX shell on Windows")
        prefix = [str(shell), "--noprofile", "--norc", "-c"]
    else:
        prefix = ["/bin/sh", "-c"]
    for name in ("#dir", "src#dir", "home"):
        (shell_workspace / name).mkdir()
    env = dict(os.environ, HOME=str(shell_workspace / "home"))
    child = subprocess.run(prefix + [command + "\npwd"], cwd=shell_workspace,
                           env=env, capture_output=True, check=True)
    assert child.stdout.decode().strip().replace("\\", "/").endswith("/" + child_directory)
    original_run = subprocess.run
    def run_in_posix(command, **kwargs):
        return original_run(prefix + [command], env=env, **kwargs)
    monkeypatch.setattr("core.runtime_context.shell_session.sys.platform", "linux")
    monkeypatch.setattr("core.runtime_context.shell_session.subprocess.run", run_in_posix)
    session = ShellSession(shell_workspace)
    result = session.execute(command)
    assert result["success"] is True
    expected = shell_workspace / persistent_directory if persistent_directory else shell_workspace
    assert session.cwd == expected.resolve()
