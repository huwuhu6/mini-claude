from types import SimpleNamespace

import pytest

from core.features import FeatureDefinition, FeatureManager
from agent.mini_claude_agent import MiniClaudeAgent
from core.prompt_builder import build_platform_guidance
from core.runtime_context.preflight import PreflightResult


def _build_prompt(*, skills_enabled=True):
    features = FeatureManager()
    features.register_feature(FeatureDefinition("memory", enabled=True))
    features.register_feature(FeatureDefinition("background", enabled=False))
    features.register_feature(FeatureDefinition("skills", enabled=skills_enabled))
    preflight = PreflightResult(
        network_access="OFFLINE",
        detected_toolchains={"Python": "3.14"},
        workspace_root="/workspace/project",
        workspace_read_write=True,
    )
    descriptions = "pdf: inspect and create PDFs"
    skill_loader = SimpleNamespace(
        descriptions=(lambda: descriptions) if skills_enabled else
        (lambda: pytest.fail("disabled skills must not be injected"))
    )
    agent = SimpleNamespace(
        config=SimpleNamespace(agent=SimpleNamespace(name="MiniClaude", version="test")),
        workdir="/workspace/project",
        feature_manager=features,
        preflight=preflight,
        skill_loader=skill_loader,
    )
    MiniClaudeAgent.refresh_system_prompt(agent)
    return agent.system_prompt, preflight


def test_prompt_contract_has_clear_blocks_and_current_runtime_facts():
    prompt, _preflight = _build_prompt()

    identity = prompt.index("<identity>")
    environment = prompt.index("<environment>")
    policy = prompt.index("<execution_policy>")
    skills = prompt.index("<skills>")
    assert identity < environment < policy < skills
    assert prompt.count("/workspace/project") == 1
    assert "Enabled features: memory, skills." in prompt
    assert "Platform: Windows." in prompt
    assert "Network at startup: OFFLINE" in prompt
    assert "time-bounded evidence" in prompt
    assert "conditions can change" in prompt
    assert "Detected toolchains: Python 3.14." in prompt
    assert "Workspace read/write capability: available." in prompt
    assert "Respond in the same language as the user" in prompt
    assert "pdf: inspect and create PDFs" in prompt


def test_disabled_skills_are_not_injected_into_prompt():
    prompt, _preflight = _build_prompt(skills_enabled=False)
    assert "<skills>" not in prompt
    assert "pdf: inspect and create PDFs" not in prompt
    assert "Enabled features: memory." in prompt


def test_prompt_does_not_name_background_tool_when_feature_is_disabled():
    prompt, _preflight = _build_prompt()
    assert "run_background" not in prompt
    assert "purpose-built file and search tools" in prompt
    assert "shell workarounds" in prompt


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("win32", "Windows CMD"), ("linux", "Linux bash"), ("darwin", "macOS")],
)
def test_platform_guidance_matches_runtime_platform(platform, expected):
    assert expected in build_platform_guidance(platform)


def test_windows_guidance_keeps_only_high_value_shell_advice():
    guidance = build_platform_guidance("win32").lower()
    assert "windows cmd" in guidance
    assert "complex multiline" in guidance
    assert "fragile inline quoting" in guidance
    assert "script file" in guidance
    assert "purpose-built file and search tools" in guidance
    assert "shell workarounds" in guidance
    assert "runtime policy" in guidance
    assert "invoke-expression" not in guidance
    assert "trailing &" not in guidance


def test_execution_policy_covers_contract_without_old_sections():
    prompt, _preflight = _build_prompt()
    prompt = prompt.lower()

    assert "does not block the next implementation step" in prompt
    assert "when asked to modify or fix something, make the requested change" in prompt
    assert "implementation and focused verification feedback" in prompt
    assert "structural changes" in prompt and "static verification" in prompt
    assert "behavioral changes" in prompt and "runtime verification" in prompt
    assert "stop when the requested work is complete" in prompt
    assert "do not claim completion" in prompt and "verified" in prompt
    assert "planning rule:" not in prompt
    assert "implementation strategy:" not in prompt
    assert "verification strategy (must follow):" not in prompt
    assert "todowrite" not in prompt


def test_preflight_context_does_not_set_response_language():
    _prompt, preflight = _build_prompt()
    assert "Answer in the same language" not in preflight.to_context()
