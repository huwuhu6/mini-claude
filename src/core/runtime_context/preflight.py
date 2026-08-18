"""Fast, language-neutral environment probes for a workspace session."""

from __future__ import annotations

import os
import socket
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse


ProbeClock = Callable[[], float]


@dataclass(frozen=True)
class PreflightResult:
    """Facts collected before an Agent session starts."""

    network_access: str
    detected_toolchains: Dict[str, str] = field(default_factory=dict)
    workspace_root: str = ""
    elapsed_ms: float = 0.0
    workspace_read_write: bool = False

    def to_dict(self) -> dict:
        return {
            "network_access": self.network_access,
            "detected_toolchains": dict(self.detected_toolchains),
            "workspace_root": self.workspace_root,
            "elapsed_ms": self.elapsed_ms,
            "workspace_read_write": self.workspace_read_write,
        }

    def to_context(self) -> str:
        toolchains = ", ".join(
            f"{name} {version}" for name, version in self.detected_toolchains.items()
        ) or "none detected"
        constraint = (
            "External dependency downloads (npm install, pip install, cargo add, "
            "go get, and similar commands) are unavailable. Use only pre-installed "
            "or local modules and report the blocker immediately."
            if self.network_access == "OFFLINE"
            else "Network is reachable, but dependency commands still require normal tool and permission checks."
        )
        return (
            "[Environment Probes Context]\n"
            f"- Network Access: {self.network_access}\n"
            f"- Detected Toolchains: {toolchains}\n"
            f"- Workspace Root: {self.workspace_root} "
            f"(Read-Write: {'Enabled' if self.workspace_read_write else 'Unavailable'})\n"
            "[Execution Constraint]\n"
            f"{constraint}\n"
            "Answer in the same language as the user prompt."
        )


_TOOLCHAIN_SPECS: Tuple[Tuple[str, Tuple[str, ...], Tuple[str, ...]], ...] = (
    ("Node", ("package.json",), ("node", "npm", "pnpm", "yarn")),
    ("Rust", ("Cargo.toml",), ("rustc", "cargo")),
    ("Go", ("go.mod",), ("go",)),
    ("Java", ("pom.xml", "build.gradle"), ("java", "mvn", "gradle")),
    ("Python", ("pyproject.toml", "requirements.txt"), ("python", "python3", "pip", "pip3")),
)

_VERSION_ARGS = {
    "java": ("-version",),
    "rustc": ("--version",),
    "cargo": ("--version",),
    "go": ("version",),
}


def _remaining(deadline: float, clock: ProbeClock) -> float:
    return max(0.0, deadline - clock())


def _probe_network(deadline: float, clock: ProbeClock) -> str:
    """Return ONLINE when a proxy or direct transport target is reachable."""
    endpoints = list(_proxy_endpoints()) + [("1.1.1.1", 53), ("8.8.8.8", 53)]
    for host, port in endpoints:
        remaining = _remaining(deadline, clock)
        if remaining <= 0:
            break
        try:
            with socket.create_connection((host, port), timeout=min(0.2, remaining)):
                return "ONLINE"
        except OSError:
            continue
    return "OFFLINE"


def _proxy_endpoints() -> Tuple[Tuple[str, int], ...]:
    """Resolve configured proxy addresses without performing DNS work twice."""
    endpoints: List[Tuple[str, int]] = []
    seen = set()
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        value = os.environ.get(key)
        if not value:
            continue
        try:
            parsed = urlparse(value if "://" in value else f"http://{value}")
            host = parsed.hostname
            port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        except ValueError:
            continue
        if host and (host, port) not in seen:
            seen.add((host, port))
            endpoints.append((host, port))
    return tuple(endpoints)


def _version_command(path: str, args: Sequence[str], *, platform_name: str = os.name) -> List[str]:
    """Run Windows command wrappers through cmd.exe instead of CreateProcess."""
    suffix = Path(path).suffix.lower()
    if platform_name == "nt" and suffix in {".cmd", ".bat"}:
        return ["cmd.exe", "/d", "/c", path, *args]
    return [path, *args]


def _version_for(executable: str, deadline: float, clock: ProbeClock) -> Optional[str]:
    path = shutil.which(executable)
    if not path:
        return None
    remaining = _remaining(deadline, clock)
    if remaining <= 0:
        return None
    args: Sequence[str] = _VERSION_ARGS.get(executable, ("--version",))
    try:
        result = subprocess.run(
            _version_command(path, args),
            capture_output=True,
            text=True,
            timeout=min(0.1, remaining),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (result.stdout or result.stderr).strip().splitlines()
    if not output:
        return None
    return output[0][:120]


def run_preflight(
    workspace_root: Path,
    *,
    budget_ms: int = 300,
    clock: ProbeClock = time.monotonic,
) -> PreflightResult:
    """Collect network and relevant toolchain facts within one time budget."""
    started = clock()
    deadline = started + budget_ms / 1000.0
    workspace = Path(workspace_root).resolve()
    network_access = _probe_network(deadline, clock)
    detected: Dict[str, str] = {}

    for _ecosystem, markers, executables in _TOOLCHAIN_SPECS:
        if not any((workspace / marker).is_file() for marker in markers):
            continue
        for executable in executables:
            version = _version_for(executable, deadline, clock)
            if version:
                detected[executable] = version

    elapsed_ms = round((clock() - started) * 1000, 1)
    return PreflightResult(
        network_access=network_access,
        detected_toolchains=detected,
        workspace_root=str(workspace),
        elapsed_ms=elapsed_ms,
        workspace_read_write=os.access(str(workspace), os.W_OK),
    )
