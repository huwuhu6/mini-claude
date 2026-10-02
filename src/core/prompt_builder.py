"""Build the stable system prompt from runtime facts and policy inputs."""

from __future__ import annotations

from typing import Sequence

from core.runtime_context.preflight import PreflightResult


def _platform_name(platform: str) -> str:
    return {
        "win32": "Windows",
        "linux": "Linux",
        "darwin": "macOS",
    }.get(platform, platform)


def build_platform_guidance(platform: str) -> str:
    """Return concise, platform-specific shell usage guidance."""
    if platform == "win32":
        shell_guidance = (
            "The bash tool runs in Windows CMD. Use platform-appropriate commands. "
            "For complex multiline logic, avoid fragile inline quoting; use a script file when needed."
        )
    elif platform == "linux":
        shell_guidance = "The bash tool runs in Linux bash; use platform-appropriate commands."
    elif platform == "darwin":
        shell_guidance = "The shell runs on macOS; use platform-appropriate commands."
    else:
        shell_guidance = f"Use commands appropriate for the {_platform_name(platform)} environment."

    return (
        f"{shell_guidance} Prefer available purpose-built file and search tools over "
        "complex shell workarounds when they fit the task. Unsafe or unsupported shell "
        "forms may be rejected by runtime policy; adapt to tool feedback."
    )


def build_system_prompt(
    *,
    agent_name: str,
    agent_version: str,
    workspace: str,
    enabled_features: Sequence[str],
    preflight: PreflightResult,
    platform: str,
    skill_descriptions: str = "",
) -> str:
    """Compose the stable identity, environment, execution, and skill blocks."""
    toolchains = ", ".join(
        f"{name} {version}" for name, version in preflight.detected_toolchains.items()
    ) or "none detected"
    features = ", ".join(enabled_features) if enabled_features else "base"

    sections = [
        "<identity>",
        f"You are {agent_name} v{agent_version}, an AI coding assistant.",
        f"Workspace: {workspace}",
        f"Enabled features: {features}.",
        "Respond in the same language as the user unless they request otherwise.",
        "</identity>",
        "",
        "<environment>",
        f"Platform: {_platform_name(platform)}.",
        f"Network at startup: {preflight.network_access}.",
        f"Detected toolchains: {toolchains}.",
        f"Workspace read/write capability: {'available' if preflight.workspace_read_write else 'unavailable'}.",
        "</environment>",
        "",
        "<execution_policy>",
        "Treat startup network status as time-bounded evidence; verify actual operations because conditions can change.",
        "Inspect enough context to understand the relevant interfaces and constraints. If missing information does not block the next implementation step, proceed.",
        "When asked to modify or fix something, make the requested change unless a real blocker prevents it.",
        "Use implementation and focused verification feedback to guide any further investigation.",
        "Structural changes usually need focused static verification; behavioral changes should get focused runtime verification when feasible.",
        "For complex, multi-step coding tasks, use TodoWrite to set a small, high-level plan before substantial exploration or implementation. Keep one item in_progress at a time; update the plan only when the current goal changes or completes, not after every tool call. Skip TodoWrite for simple, single-step tasks.",
        "Stop when the requested work is complete and relevant evidence is sufficient. Do not claim completion for work that has not been verified.",
        build_platform_guidance(platform),
        "</execution_policy>",
    ]

    if skill_descriptions:
        sections.extend([
            "",
            "<skills>",
            "Available specialized skills:",
            skill_descriptions,
            "Load a skill when its domain-specific instructions are relevant.",
            "</skills>",
        ])

    return "\n".join(sections)
