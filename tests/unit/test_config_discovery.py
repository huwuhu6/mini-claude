from pathlib import Path

import pytest

from models.config import ConfigManager
from providers.bootstrap import configure_primary_provider


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.delenv("MINI_CLAUDE_CONFIG", raising=False)


def test_default_config_outside_repository_keeps_bailian(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("DASHSCOPE_API_KEY", "DASHSCOPE_COMPATIBLE_BASE_URL", "AI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "mock-bailian-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "mock-official-key")
    config = ConfigManager().get_config()
    calls = []
    class Manager:
        def create_provider(self, name, config, is_primary=False):
            calls.append((name, config, is_primary))
    configure_primary_provider(Manager(), config.llm)
    assert calls[0][0] == "dashscope"
    assert calls[0][1]["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert calls[0][1]["api_key"] == "mock-bailian-key"
    assert calls[0][1]["model"] == config.llm.model


def test_current_directory_config_remains_authoritative(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/default.yaml").write_text(
        "llm:\n  provider: anthropic\n  model: custom-model\n", encoding="utf-8")
    assert ConfigManager().get_config().llm.model == "custom-model"


def test_explicit_missing_config_does_not_silently_switch_provider(tmp_path):
    with pytest.raises(FileNotFoundError):
        ConfigManager(tmp_path / "missing.yaml")


def test_missing_active_credential_fails_before_provider_creation(tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING_TEST_KEY", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("llm:\n  provider: dashscope\n  dashscope:\n    api_key: ${MISSING_TEST_KEY}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="MISSING_TEST_KEY"):
        ConfigManager(path)


def test_user_profile_and_explicit_selection_win_over_project_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    local = tmp_path / "configs/default.yaml"
    local.parent.mkdir()
    local.write_text("llm:\n  model: project\n", encoding="utf-8")
    user = Path.home() / ".mini-claude/config.yaml"
    user.parent.mkdir(parents=True)
    user.write_text("llm:\n  model: user\n", encoding="utf-8")
    assert ConfigManager().get_config().llm.model == "user"
    assert ConfigManager(local).get_config().llm.model == "project"
    monkeypatch.setenv("MINI_CLAUDE_CONFIG", str(local))
    assert ConfigManager().get_config().llm.model == "project"
    monkeypatch.setenv("MINI_CLAUDE_CONFIG", str(tmp_path / "missing.yaml"))
    with pytest.raises(FileNotFoundError): ConfigManager()
