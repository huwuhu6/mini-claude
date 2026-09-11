"""Deterministic normalization of process results and target observations.

The shell/tool boundary keeps structured process facts.  This module adds a
small amount of tool-aware meaning without treating arbitrary prose or
stderr as a failure.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from core.tools.base_tools import ToolResult


@dataclass(frozen=True)
class ObservationEvidence:
    """Facts observed by RuntimePolicy; this object makes no decision."""

    observed_failure: bool = False
    semantic_status: str = "ORDINARY_OUTPUT"
    observation: str = ""
    failure_category: str = ""
    recoverability: str = ""
    evidence_text: str = ""
    source: str = ""


class ObservationNormalizer:
    """Normalize explicit process/target evidence with conservative rules."""

    _HTTP_STATUS = re.compile(
        r"HTTP(?:/\d(?:\.\d)?)?\s*(?:Error\s*)?([45]\d\d)|"
        r"HTTP_CODE\s*[=:]\s*([45]\d\d)|"
        r"status[_ ]?code\s*[=:]\s*['\"]?([45]\d\d)",
        re.IGNORECASE,
    )
    _JSON_STATUS = re.compile(
        r"[\"']status_code[\"']\s*:\s*([45]\d\d)", re.IGNORECASE
    )
    _PROCESS_COMMAND = re.compile(
        r"(?:^|[\s;&|])(python(?:\d+(?:\.\d+)*)?|py|node|java|pytest|"
        r"mvn|gradle|cargo|go)\b",
        re.IGNORECASE,
    )

    @classmethod
    def normalize(
        cls,
        tool_name: str,
        args: dict[str, Any] | None,
        result: ToolResult | str | dict[str, Any],
    ) -> ObservationEvidence:
        command = str((args or {}).get("command", ""))
        execution_success, exit_code, stdout, stderr, segment_codes, text = cls._facts(result)
        combined = "\n".join(part for part in (stdout, stderr) if part).strip() or text

        if segment_codes and any(code != 0 for code in segment_codes):
            code = next(code for code in segment_codes if code != 0)
            return ObservationEvidence(
                observed_failure=True,
                semantic_status="PARTIAL_PROCESS_FAILURE",
                observation=f"SEGMENT_EXIT_{code}",
                failure_category="PROCESS_FAILURE",
                recoverability="PARTIALLY_RECOVERABLE",
                evidence_text=f"[PROCESS_SEGMENT_EXIT={code}]\n{combined}",
                source="shell_segment_exit",
            )

        if not execution_success:
            code_label = f"_{exit_code}" if exit_code is not None else ""
            return ObservationEvidence(
                observed_failure=True,
                semantic_status="PROCESS_FAILURE",
                observation=f"PROCESS_EXIT{code_label}",
                evidence_text=combined or text,
                source="process_result",
            )

        background = cls._background_failure(tool_name, combined)
        if background:
            return background

        status = cls._http_status(tool_name, command, combined)
        if status:
            return ObservationEvidence(
                observed_failure=True,
                semantic_status="UNHEALTHY",
                observation=f"HTTP_{status}",
                failure_category="NETWORK_UNREACHABLE",
                recoverability="PARTIALLY_RECOVERABLE",
                evidence_text=f"[OBSERVED HTTP_{status}]\n{combined}",
                source="structured_http_observation",
            )

        if cls._permission_observation(tool_name, command, combined):
            return ObservationEvidence(
                observed_failure=True,
                semantic_status="RESOURCE_DENIED",
                observation="EPERM_OR_PERMISSION_DENIED",
                failure_category="PERMISSION_DENIED",
                recoverability="PARTIALLY_RECOVERABLE",
                evidence_text=f"[OBSERVED RESOURCE_DENIED]\n{combined}",
                source="probe_output",
            )

        if cls._masked_traceback(command, combined):
            return ObservationEvidence(
                observed_failure=True,
                semantic_status="OBSERVED_PROCESS_FAILURE",
                observation="TRACEBACK",
                failure_category="TOOL_CRASH",
                recoverability="PARTIALLY_RECOVERABLE",
                evidence_text=f"[OBSERVED TRACEBACK]\n{combined}",
                source="probe_output",
            )

        return ObservationEvidence(evidence_text=combined, source="ordinary_output")

    @classmethod
    def _facts(
        cls, result: ToolResult | str | dict[str, Any]
    ) -> tuple[bool, int | None, str, str, list[int], str]:
        if isinstance(result, ToolResult):
            execution_success = bool(
                result.execution_success
                if result.execution_success is not None
                else result.success
            )
            return (
                execution_success,
                result.exit_code,
                result.stdout,
                result.stderr,
                list(result.segment_exit_codes or []),
                result.content,
            )
        if isinstance(result, dict):
            return (
                bool(result.get("execution_success", result.get("success", True))),
                result.get("exit_code"),
                str(result.get("stdout", "")),
                str(result.get("stderr", "")),
                [int(code) for code in result.get("segment_exit_codes", [])],
                str(result.get("content", "")),
            )
        text = str(result or "")
        match = re.match(r"\[Exit Code: (-?\d+)\]", text)
        exit_code = int(match.group(1)) if match else None
        return (exit_code in (None, 0), exit_code, "", "", [], text)

    @classmethod
    def _background_failure(cls, tool_name: str, text: str) -> ObservationEvidence | None:
        if tool_name not in {"get_background_status", "health_check"}:
            return None
        try:
            payload = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return None
        status = str(payload.get("status", "")).lower()
        exit_code = payload.get("exit_code")
        if status in {"failed", "killed", "cancelled"} or (
            isinstance(exit_code, int) and exit_code != 0
        ):
            return ObservationEvidence(
                observed_failure=True,
                semantic_status="BACKGROUND_FAILED",
                observation=f"BACKGROUND_EXIT_{exit_code}",
                failure_category="PROCESS_FAILURE",
                recoverability="PARTIALLY_RECOVERABLE",
                evidence_text=f"[BACKGROUND_STATUS={status} EXIT_CODE={exit_code}]\n{text}",
                source="background_status",
            )
        return None

    @classmethod
    def _http_status(cls, tool_name: str, command: str, text: str) -> str:
        probe_context = tool_name in {"health_check", "get_background_status"} or bool(
            re.search(
                r"\b(?:curl|wget|Invoke-WebRequest|urlopen)\b|"
                r"(?:health|probe|/resource|/toolchain|/dependency)",
                command,
                re.IGNORECASE,
            )
        )
        if not probe_context:
            return ""
        match = cls._HTTP_STATUS.search(text) or cls._JSON_STATUS.search(text)
        if not match:
            return ""
        return next(group for group in match.groups() if group)

    @classmethod
    def _permission_observation(cls, tool_name: str, command: str, text: str) -> bool:
        probe_context = tool_name in {"health_check", "get_background_status"} or bool(
            re.search(r"\b(?:probe|resource|toolchain|health|audit)\b", command, re.IGNORECASE)
        )
        strong_marker = re.search(
            r"\b(?:EPERM|EACCES)\b|\[WinError 5\]|Access is denied|Permission denied",
            text,
            re.IGNORECASE,
        )
        return probe_context and bool(strong_marker)

    @classmethod
    def _masked_traceback(cls, command: str, text: str) -> bool:
        return bool(
            cls._PROCESS_COMMAND.search(command)
            and re.search(r"Traceback \(most recent call last\):", text)
        )
