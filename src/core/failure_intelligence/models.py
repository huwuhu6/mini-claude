"""
Data Models — Failure categories, recoverability levels, and signatures.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class FailureCategory(Enum):
    """Semantic categories of tool execution failures."""
    NETWORK_UNREACHABLE = "NETWORK_UNREACHABLE"
    PACKAGE_NOT_FOUND = "PACKAGE_NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    SYNTAX_ERROR = "SYNTAX_ERROR"
    COMMAND_NOT_FOUND = "COMMAND_NOT_FOUND"
    OUT_OF_MEMORY = "OUT_OF_MEMORY"
    DISK_FULL = "DISK_FULL"
    TOOL_CRASH = "TOOL_CRASH"
    TOOL_PARAM_ERROR = "TOOL_PARAM_ERROR"
    UNKNOWN = "UNKNOWN"


class Recoverability(Enum):
    """How recoverable a failure is from the agent's perspective."""
    SELF_HEALABLE = "SELF_HEALABLE"
    PARTIALLY_RECOVERABLE = "PARTIALLY_RECOVERABLE"
    USER_INTERVENTION_REQUIRED = "USER_INTERVENTION_REQUIRED"
    NON_RECOVERABLE = "NON_RECOVERABLE"
    UNKNOWN = "UNKNOWN"


@dataclass
class FailureSignature:
    """Semantic fingerprint of a tool execution failure.

    Fields:
        category: High-level failure category.
        fingerprint: Unique string combining category + normalized error
            for exact deduplication within a category.
        recoverability: How recoverable this failure type is.
        retryable: Whether retrying the same operation could ever succeed.
        confidence: Rule-match confidence 0.0–1.0.
        root_cause_hint: Human-readable description of the likely root cause.
        tool_name: The tool that produced the failure.
        strategy_fingerprint: High-level strategy identifier for diversity
            detection (e.g. "NETWORK_PACKAGE_INSTALL", "LOCAL_FILE_IO").
        escalated: Whether this failure triggered an escalation.
    """
    category: FailureCategory = FailureCategory.UNKNOWN
    fingerprint: str = ""
    recoverability: Recoverability = Recoverability.UNKNOWN
    retryable: bool = True
    confidence: float = 0.0
    root_cause_hint: str = ""
    tool_name: str = ""
    strategy_fingerprint: str = ""
    escalated: bool = False
