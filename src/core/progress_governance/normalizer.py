"""Deterministic, language-neutral observation normalisation."""

from __future__ import annotations

import hashlib
import re


class ObservationNormalizer:
    """Remove unstable presentation noise while preserving semantic numbers."""

    _ANSI = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    _ISO_TIMESTAMP = re.compile(
        r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
    )
    _TIME_FIELD = re.compile(
        r"\b(?:timestamp|time|duration|elapsed|latency|took|started_at|finished_at)\s*[:=]\s*"
        r"[^,;\s]+",
        re.IGNORECASE,
    )
    _UUID = re.compile(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        re.IGNORECASE,
    )
    _TEMP_PATH = re.compile(
        r"(?:(?:[A-Za-z]:)?[/\\](?:Users|user|tmp|temp|AppData[/\\]Local[/\\]Temp)"
        r"[/\\][^\s'\"]+)",
        re.IGNORECASE,
    )
    _PYTHON_PATH = re.compile(r"(?:[A-Za-z]:)?[/\\][^\s'\"]+[/\\](?:python|site-packages)[^\s'\"]*", re.I)
    _HEX_ADDRESS = re.compile(r"\b0x[0-9a-f]{8,}\b", re.IGNORECASE)

    @classmethod
    def normalize(cls, text: str, workspace_root: str = "") -> str:
        value = cls._ANSI.sub("", str(text or ""))
        if workspace_root:
            root = re.escape(workspace_root.rstrip("/\\"))
            value = re.sub(root, "<workspace>", value, flags=re.IGNORECASE)
            value = value.replace(workspace_root.rstrip("/\\").replace("\\", "/"), "<workspace>")
        value = cls._ISO_TIMESTAMP.sub("<timestamp>", value)
        value = cls._TIME_FIELD.sub(
            lambda m: re.match(r"[^:=]+[:=]", m.group(0)).group(0) + "<noise>",
            value,
        )
        value = cls._UUID.sub("<id>", value)
        value = cls._TEMP_PATH.sub("<temp-path>", value)
        value = cls._PYTHON_PATH.sub("<runtime-path>", value)
        value = cls._HEX_ADDRESS.sub("<address>", value)
        # Keep digits such as ``5 failed -> 3 failed``; only presentation
        # noise above is removed.  Case-folding makes tool output stable.
        value = re.sub(r"\r\n?", "\n", value)
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip().lower()

    @classmethod
    def fingerprint(cls, text: str, workspace_root: str = "") -> str:
        normalized = cls.normalize(text, workspace_root)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
