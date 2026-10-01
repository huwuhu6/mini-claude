from types import SimpleNamespace

from agent.mini_claude_agent import MiniClaudeAgent


def test_system_prompt_prioritizes_small_implementation_and_focused_feedback():
    agent = SimpleNamespace(
        feature_manager=SimpleNamespace(
            get_enabled_features=lambda: [],
            is_enabled=lambda _name: False,
        ),
        config=SimpleNamespace(agent=SimpleNamespace(name="MiniClaude", version="test")),
        workdir="/workspace",
        preflight=SimpleNamespace(to_context=lambda: ""),
        _get_platform_prompt=lambda: "",
    )

    MiniClaudeAgent._load_system_prompt(agent)
    prompt = agent.system_prompt

    planning = prompt.index("PLANNING RULE:")
    implementation = prompt.index("IMPLEMENTATION STRATEGY:")
    verification = prompt.index("VERIFICATION STRATEGY (MUST FOLLOW):")
    assert planning < implementation < verification
    assert "Does the missing information block the next implementation step?" in prompt
    assert "implement, verify, and use concrete failures" in prompt
    assert "use focused runtime verification when feasible" in prompt
    assert "For purely structural changes, runtime execution is usually unnecessary" in prompt
    assert "RUNTIME VERIFICATION IS ALLOWED ONLY WHEN" not in prompt
