"""Best-effort display redaction, never an operation or authorization transform."""
from __future__ import annotations
import logging
import re
import unicodedata

_SECRET = r"[\w-]*(?:api[_-]?key|token|password|passwd|secret|credential|authorization|private[_-]?key)[\w-]*"
_KEY = re.compile(_SECRET, re.I)
_METRICS = {"prompt_tokens", "completion_tokens", "total_tokens", "max_tokens", "cached_tokens", "reasoning_tokens"}
_VALUE = r"""(?:(?:"[^"\n]*"|'[^'\n]*')|[^\s,;}\]]+)"""
_ASSIGN = re.compile(r"([\"']?" + _SECRET + r"[\"']?\s*[=:]\s*)" + _VALUE, re.I)
_OPTION = re.compile(r"(--" + _SECRET + r"\s+)" + _VALUE, re.I)
_BEARER = re.compile(r"(Bearer\s+)[\w.~/+\-=]+", re.I)
_URL = re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@", re.I)


def safe_text(value):
    text = str(value)
    text = _ASSIGN.sub(lambda m: m[1] + "[已脱敏]", text)
    text = _OPTION.sub(lambda m: m[1] + "[已脱敏]", text)
    text = _BEARER.sub(lambda m: m[1] + "[已脱敏]", text)
    text = _URL.sub(lambda m: m[1] + "[已脱敏]@", text)
    return ''.join(c if c in '\n\t' or unicodedata.category(c) not in {"Cc", "Cf"} else f"\\u{ord(c):04x}" for c in text)


def redact(value):
    if isinstance(value, dict):
        return {safe_text(k): "[已脱敏]" if str(k) not in _METRICS and _KEY.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return safe_text(value) if isinstance(value, str) else value


class DisplayLogFilter(logging.Filter):
    def filter(self, record):
        record.msg = safe_text(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = safe_text(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True
