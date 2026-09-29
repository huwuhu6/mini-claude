"""Harbor carries task instruction and secrets without changing Agent semantics."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("harbor")

from benchmark.harbor.agent import MiniClaudeHarborAgent


def test_harbor_run_uploads_instruction_and_uses_task_workspace(monkeypatch, tmp_path):
    secret = "provider-key-must-not-appear-in-commands"
    monkeypatch.setenv("DASHSCOPE_API_KEY", secret)
    agent = MiniClaudeHarborAgent(logs_dir=tmp_path)
    uploaded = {}

    async def upload(source, target):
        uploaded[target] = Path(source).read_text(encoding="utf-8")

    environment = SimpleNamespace(
        task_env_config=SimpleNamespace(workdir="/app"),
        default_user=None,
        upload_file=upload,
    )
    agent.exec_as_agent = AsyncMock()
    asyncio.run(agent.run("Fix the issue.\nRead /app/src first.", environment, None))

    assert uploaded["/installed-agent/instruction.txt"] == "Fix the issue.\nRead /app/src first."
    command = agent.exec_as_agent.await_args.kwargs["command"]
    assert command.startswith("/installed-agent/mini-claude-venv/bin/mini-claude-headless ")
    assert "--workspace /app" in command
    assert "--data-root /logs/agent/mini-claude" in command
    assert secret not in command
    assert agent.exec_as_agent.await_args.kwargs["cwd"] == "/app"
    assert agent._extra_env["DASHSCOPE_API_KEY"] == secret


def test_harbor_missing_key_fails_before_execution(monkeypatch, tmp_path):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    agent = MiniClaudeHarborAgent(logs_dir=tmp_path)
    agent.exec_as_agent = AsyncMock()
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        asyncio.run(agent.run("task", SimpleNamespace(), None))
    agent.exec_as_agent.assert_not_awaited()


@pytest.mark.parametrize("python_ok", [True, False])
def test_install_uploads_current_wheel_and_config(monkeypatch, tmp_path, python_ok):
    source_root = tmp_path / "source"
    config_dir = source_root / "configs"
    config_dir.mkdir(parents=True)
    (config_dir / "default.yaml").write_text("llm:\n  provider: dashscope\n", encoding="utf-8")
    agent = MiniClaudeHarborAgent(logs_dir=tmp_path, source_root=source_root)
    uploaded = {}

    def build_wheel(command, **kwargs):
        assert command[-1] == str(source_root)
        Path(command[command.index("--wheel-dir") + 1], "mini_claude-1.0.0-py3-none-any.whl").write_bytes(b"wheel")

    async def upload(source, target):
        uploaded[target] = Path(source).read_bytes()

    monkeypatch.setattr("benchmark.harbor.agent.subprocess.run", build_wheel)
    environment = SimpleNamespace(
        upload_file=upload,
        exec=AsyncMock(return_value=SimpleNamespace(return_code=0 if python_ok else 1)),
        default_user="agent",
    )
    agent.exec_as_root = AsyncMock()
    agent.ensure_system_dependencies = AsyncMock()
    asyncio.run(agent.install(environment))

    assert uploaded["/installed-agent/mini_claude-1.0.0-py3-none-any.whl"] == b"wheel"
    assert uploaded["/installed-agent/mini-claude.yaml"].startswith(b"llm:")
    commands = [call.kwargs["command"] for call in agent.exec_as_root.await_args_list]
    if python_ok:
        assert "python3 -m venv" in commands[0]
        agent.ensure_system_dependencies.assert_not_awaited()
    else:
        assert "uv python install 3.12" in commands[0]
        agent.ensure_system_dependencies.assert_awaited_once()
    assert "chown -R agent /logs/agent/mini-claude" in commands[-1]
