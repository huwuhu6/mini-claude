"""One-shot execution keeps the normal Agent lifecycle and workspace boundary."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent import headless


def test_headless_binds_workspace_and_shutdown(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_root = tmp_path / "runtime"
    config = tmp_path / "custom.yaml"
    config.write_text("llm: {}", encoding="utf-8")
    captured = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def chat(self, instruction):
            captured["instruction"] = instruction
            trace_dir = data_root / "traces"
            trace_dir.mkdir(parents=True)
            (trace_dir / "task_abc.json").write_text(
                json.dumps({"task_id": "abc", "final_status": "SUCCESS"}), encoding="utf-8"
            )
            return "finished"

        def shutdown(self):
            captured["shutdown"] = True

    monkeypatch.setattr(headless, "MiniClaudeAgent", FakeAgent)
    result = headless.run_agent_once(
        workspace_root=workspace,
        instruction="Investigate the project",
        config_path=config,
        runtime_data_root=data_root,
    )
    assert captured == {
        "config_path": config,
        "workspace_root": workspace.resolve(),
        "workspace_confirmed": True,
        "runtime_data_root": data_root,
        "instruction": "Investigate the project",
        "shutdown": True,
    }
    assert result.final_response == "finished"
    assert result.final_status == "SUCCESS"
    assert result.task_id == "abc"
    assert result.trace_path == data_root / "traces" / "task_abc.json"


def test_headless_shutdown_on_error(monkeypatch, tmp_path):
    agent = MagicMock()
    agent.chat.side_effect = RuntimeError("provider unavailable")
    monkeypatch.setattr(headless, "MiniClaudeAgent", lambda **kwargs: agent)
    with pytest.raises(RuntimeError, match="provider unavailable"):
        headless.run_agent_once(workspace_root=tmp_path, instruction="task")
    agent.shutdown.assert_called_once_with()


def test_headless_requires_explicit_workspace_and_instruction(tmp_path):
    with pytest.raises(TypeError):
        headless.run_agent_once(instruction="task")
    with pytest.raises(ValueError, match="instruction"):
        headless.run_agent_once(workspace_root=tmp_path, instruction=" ")
    with pytest.raises(FileNotFoundError):
        headless.run_agent_once(workspace_root=Path(tmp_path / "missing"), instruction="task")
