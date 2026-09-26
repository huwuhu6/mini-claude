"""Installed Harbor agent that runs the normal MiniClaude loop in a task sandbox."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


_INSTALL_DIR = "/installed-agent"
_VENV = f"{_INSTALL_DIR}/mini-claude-venv"
_CONFIG = f"{_INSTALL_DIR}/mini-claude.yaml"
_INSTRUCTION = f"{_INSTALL_DIR}/instruction.txt"
_DATA_ROOT = "/logs/agent/mini-claude"
_RESULT = "/logs/agent/mini-claude-result.json"
_UV_VERSION = "0.12.17"


class MiniClaudeHarborAgent(BaseInstalledAgent):
    """Dataset-neutral bridge to MiniClaude's non-interactive execution API."""

    @staticmethod
    def name() -> str:
        return "mini-claude"

    def __init__(
        self,
        logs_dir: Path,
        source_root: str | Path | None = None,
        extra_env: dict[str, str] | None = None,
        **kwargs,
    ) -> None:
        self.source_root = (
            Path(source_root).resolve() if source_root is not None
            else Path(__file__).resolve().parents[2]
        )
        # Harbor scopes extra_env to the agent phase. No key is put in CLI args
        # or the uploaded MiniClaude config (which uses ${...} placeholders).
        scoped_env = dict(extra_env or {})
        for key in ("DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY"):
            if key not in scoped_env and os.environ.get(key):
                scoped_env[key] = os.environ[key]
        super().__init__(logs_dir=logs_dir, extra_env=scoped_env, **kwargs)

    async def install(self, environment: BaseEnvironment) -> None:
        config = self.source_root / "configs" / "default.yaml"
        if not config.is_file():
            raise FileNotFoundError("MiniClaude default config was not found in source_root")

        # Build from the selected checkout, not from an unrelated host Python
        # installation. Uploading a wheel prevents access to host workspace files.
        with tempfile.TemporaryDirectory(prefix="mini-claude-harbor-") as temp_dir:
            subprocess.run(
                [sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", temp_dir, str(self.source_root)],
                check=True, capture_output=True, text=True,
            )
            wheels = list(Path(temp_dir).glob("mini_claude-*.whl"))
            if len(wheels) != 1:
                raise RuntimeError("MiniClaude wheel build did not produce exactly one wheel")
            wheel_target = f"{_INSTALL_DIR}/{wheels[0].name}"
            await environment.upload_file(wheels[0], wheel_target)

        await environment.upload_file(config, _CONFIG)
        python_check = await environment.exec(
            command=(
                "python3 -c 'import sys; assert sys.version_info >= (3, 10)' "
                "&& python3 -m venv --help >/dev/null"
            ),
            user="root",
        )
        if python_check.return_code == 0:
            install_command = (
                f"python3 -m venv {_VENV} && "
                f"{_VENV}/bin/python -m pip install {shlex.quote(wheel_target)}"
            )
        else:
            # Some benchmark images have no suitable Python. Bootstrap one
            # outside the task workdir with Harbor's system dependency helper.
            await self.ensure_system_dependencies(environment, ("curl", "ca_certificates"))
            install_command = (
                f"curl -LsSf https://astral.sh/uv/{_UV_VERSION}/install.sh | "
                f"env UV_UNMANAGED_INSTALL={_INSTALL_DIR} sh && "
                f"{_INSTALL_DIR}/uv python install 3.12 && "
                f"{_INSTALL_DIR}/uv venv --python 3.12 {_VENV} && "
                f"{_INSTALL_DIR}/uv pip install --python {_VENV}/bin/python {shlex.quote(wheel_target)}"
            )
        await self.exec_as_root(environment, command=f"{install_command} && mkdir -p {_DATA_ROOT}")
        if environment.default_user is not None:
            await self.exec_as_root(
                environment,
                command=f"chown -R {shlex.quote(str(environment.default_user))} {_DATA_ROOT}",
            )

    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if not self._get_env("DASHSCOPE_API_KEY"):
            raise RuntimeError("缺少 DASHSCOPE_API_KEY（请通过 Harbor agent 环境传入）")
        with tempfile.TemporaryDirectory(prefix="mini-claude-instruction-") as temp_dir:
            source = Path(temp_dir) / "instruction.txt"
            source.write_text(instruction, encoding="utf-8")
            await self._upload_agent_owned_file(environment, source, _INSTRUCTION)

        configured_workdir = environment.task_env_config.workdir
        if configured_workdir:
            workspace = configured_workdir
        else:
            workspace = (await self.exec_as_agent(environment, "pwd")).stdout.strip()
        if not workspace or not workspace.startswith("/"):
            raise RuntimeError("Harbor task workspace must resolve to an absolute path")

        command = " ".join((
            f"{_VENV}/bin/mini-claude-headless",
            "--workspace", shlex.quote(workspace),
            "--instruction-file", shlex.quote(_INSTRUCTION),
            "--config", shlex.quote(_CONFIG),
            "--data-root", shlex.quote(_DATA_ROOT),
            "--result-file", shlex.quote(_RESULT),
        ))
        await self.exec_as_agent(environment, command=command, cwd=workspace)
