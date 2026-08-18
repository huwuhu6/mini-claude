"""Cross-ecosystem hard-error detection for external environment blockers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .preflight import PreflightResult


@dataclass(frozen=True)
class EnvironmentBlock:
    category: str
    root_cause: str

    @property
    def message(self) -> str:
        return (
            "[Circuit Breaker Triggered - Non-retryable Environment Blocker]\n"
            f"Root Cause: {self.root_cause}\n"
            "Instruction: Repeated download/install commands across any package "
            "manager will be blocked. Report this blocker to the user immediately "
            "or switch to standard libraries and locally available modules."
        )


class EnvironmentBlocker:
    """Recognize failures that cannot be fixed by changing retry syntax."""

    _PACKAGE_PATTERNS = (
        r"No matching distribution found",
        r"Could not find a version that satisfies the requirement",
        r"npm\s+ERR!\s+(?:code\s+)?E404",
        r"npm\s+ERR!\s+404\s+Not Found",
        r"yarn\s+error:.*Couldn't find package",
        r"no matching package named",
        r"failed to select a version for the requirement",
        r"cannot find module providing package",
        r"no matching versions for query",
        r"Could not resolve dependencies",
        r"Could not find artifact",
    )
    _NETWORK_PATTERNS = (
        r"ENOTFOUND",
        r"ETIMEDOUT",
        r"EHOSTUNREACH",
        r"ECONNREFUSED",
        r"getaddrinfo failed",
        r"Failed to establish a new connection",
        r"Could not resolve host",
        r"Network is unreachable",
        r"Temporary failure in name resolution",
        r"No route to host",
        r"Connection refused",
    )
    _PERMISSION_PATTERNS = (
        r"Permission denied",
        r"EACCES",
        r"EPERM",
        r"\[WinError 5\]",
        r"\[Errno 13\]",
        r"Access is denied",
    )
    _INSTALL_COMMAND = re.compile(
        r"(?:^|[;&|])\s*(?:python(?:\d+(?:\.\d+)*)?\s+-m\s+pip|"
        r"pip3?|pipenv|poetry|npm|pnpm|yarn|cargo|go|mvn|gradle)\b[^\r\n]*\b"
        r"(?:install|add|get|download|resolve|fetch|ci)\b",
        re.IGNORECASE,
    )

    def __init__(self, preflight: PreflightResult):
        self.preflight = preflight

    def check_command(self, tool_name: str, args: dict) -> Optional[EnvironmentBlock]:
        """Block package downloads before execution when the probe is offline."""
        if self.preflight.network_access != "OFFLINE":
            return None
        if tool_name not in {"bash", "run_background"}:
            return None
        command = args.get("command", "") if isinstance(args, dict) else ""
        if self._INSTALL_COMMAND.search(str(command)):
            return EnvironmentBlock(
                "NETWORK_UNREACHABLE",
                "Network access is OFFLINE; external dependency downloads are unavailable.",
            )
        return None

    @classmethod
    def classify_result(
        cls, result_text: str, *, failed: bool = True,
    ) -> Optional[EnvironmentBlock]:
        """Classify a known failed result, never ordinary successful output."""
        if not failed:
            return None
        text = str(result_text or "")
        for pattern in cls._PACKAGE_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return EnvironmentBlock(
                    "PACKAGE_NOT_FOUND",
                    "The requested package or artifact does not exist in the configured repositories.",
                )
        for pattern in cls._NETWORK_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return EnvironmentBlock(
                    "NETWORK_UNREACHABLE",
                    "External network access, DNS, or the target service is unavailable.",
                )
        for pattern in cls._PERMISSION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return EnvironmentBlock(
                    "PERMISSION_DENIED",
                    "The operating system denied access to the requested resource.",
                )
        return None
