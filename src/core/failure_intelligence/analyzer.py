"""
FailureAnalyzer — Orchestrates failure classification, strategy inference,
and signature construction.

Single entry point (analyze) that returns a complete FailureSignature.
"""
from __future__ import annotations
import logging
from typing import Any, Dict, Optional

from .models import FailureCategory, FailureSignature
from .signatures import FailureSignatureMatcher, infer_strategy_fingerprint

logger = logging.getLogger(__name__)


class FailureAnalyzer:
    """Lightweight, stateless failure analyzer for the agent runtime.

    Usage:
        analyzer = FailureAnalyzer()
        sig = analyzer.analyze("bash", {"command": "pip install pygame"}, result_text)
    """

    def __init__(self):
        self._matcher = FailureSignatureMatcher()

    def analyze(
        self,
        tool_name: str,
        tool_args: Optional[Dict[str, Any]],
        result_text: str,
    ) -> FailureSignature:
        """Analyze a tool failure and produce a semantic signature.

        Args:
            tool_name: Name of the tool that failed (e.g. "bash", "read_file").
            tool_args: Arguments passed to the tool.
            result_text: The full result text (stdout + stderr, including error markers).

        Returns:
            A FailureSignature with category, recoverability, fingerprint, etc.
        """
        # Step 1: Classify error
        category, rec, confidence, hint = self._matcher.match(result_text)

        # Step 2: Infer strategy fingerprint
        strategy_fp = infer_strategy_fingerprint(tool_name, tool_args or {})

        # Step 3: Build fingerprint (category + strategy for dedup)
        fingerprint = f"{category.value}::{strategy_fp}"

        # Step 4: Determine retryability
        retryable = rec.value in (
            "SELF_HEALABLE",
            "PARTIALLY_RECOVERABLE",
            "UNKNOWN",
        )

        # Step 5: Build signature
        return FailureSignature(
            category=category,
            fingerprint=fingerprint,
            recoverability=rec,
            retryable=retryable,
            confidence=confidence,
            root_cause_hint=hint,
            tool_name=tool_name,
            strategy_fingerprint=strategy_fp,
            escalated=False,
        )
