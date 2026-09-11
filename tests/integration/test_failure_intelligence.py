"""
test_failure_intelligence.py — Regression tests for the Failure Intelligence Layer.

Verifies:
  - Failure classification (pip install failures → NETWORK_UNREACHABLE / PACKAGE_NOT_FOUND)
  - Strategy fingerprint inference
  - ToolTrace extension carries failure fields
  - RuntimePolicy owns recurrence decisions outside this stateless analyzer
"""
from __future__ import annotations
import sys
import time
import json
from pathlib import Path
from unittest import mock

# ── Ensure src is importable ─────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_src = str(_PROJECT_ROOT / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

import pytest
from core.failure_intelligence import (
    FailureCategory, Recoverability, FailureSignature,
    FailureAnalyzer, FailureMemory,
    infer_strategy_fingerprint,
)
from core.tracing import ToolTrace, TraceManager


# ═════════════════════════════════════════════════════════════════
# 1. Classification Tests
# ═════════════════════════════════════════════════════════════════

class TestFailureClassification:
    """Verify rule-based error classification."""

    def setup_method(self):
        self.analyzer = FailureAnalyzer()

    def test_pip_not_found(self):
        """pip install pygame → package not found."""
        sig = self.analyzer.analyze(
            "bash", {"command": "pip install pygame"},
            "[Exit Code: 1]\nERROR: Could not find a version that satisfies "
            "the requirement pygame",
        )
        assert sig.category == FailureCategory.PACKAGE_NOT_FOUND
        assert sig.recoverability == Recoverability.USER_INTERVENTION_REQUIRED
        assert sig.strategy_fingerprint == "NETWORK_PACKAGE_INSTALL"
        assert not sig.escalated

    def test_network_unreachable(self):
        """Network failure → NETWORK_UNREACHABLE."""
        sig = self.analyzer.analyze(
            "bash", {"command": "pip install pygame"},
            "[Exit Code: 1]\nFailed to establish a new connection to pypi.org",
        )
        assert sig.category == FailureCategory.NETWORK_UNREACHABLE
        assert sig.recoverability == Recoverability.USER_INTERVENTION_REQUIRED

    def test_pip_timeout(self):
        """Timeout → TIMEOUT / PARTIALLY_RECOVERABLE."""
        sig = self.analyzer.analyze(
            "bash", {"command": "pip install pygame"},
            "[Exit Code: 1]\nRead timed out after 60 seconds",
        )
        assert sig.category == FailureCategory.TIMEOUT
        assert sig.recoverability == Recoverability.PARTIALLY_RECOVERABLE

    def test_permission_denied(self):
        """Permission denied → PERMISSION_DENIED."""
        sig = self.analyzer.analyze(
            "bash", {"command": "cat /etc/sudoers"},
            "cat: /etc/sudoers: Permission denied",
        )
        assert sig.category == FailureCategory.PERMISSION_DENIED
        assert sig.recoverability == Recoverability.USER_INTERVENTION_REQUIRED

    def test_file_not_found(self):
        """File not found → FILE_NOT_FOUND / SELF_HEALABLE."""
        sig = self.analyzer.analyze(
            "bash", {"command": "cat ghost.md"},
            "cat: ghost.md: No such file or directory",
        )
        assert sig.category == FailureCategory.FILE_NOT_FOUND
        assert sig.recoverability == Recoverability.SELF_HEALABLE

    def test_syntax_error(self):
        """Python syntax error → SYNTAX_ERROR / SELF_HEALABLE."""
        sig = self.analyzer.analyze(
            "bash", {"command": "python -c 'print broken'"},
            "SyntaxError: invalid syntax",
        )
        assert sig.category == FailureCategory.SYNTAX_ERROR
        assert sig.recoverability == Recoverability.SELF_HEALABLE

    def test_unknown_error(self):
        """Unknown error → UNKNOWN / UNKNOWN."""
        sig = self.analyzer.analyze(
            "bash", {"command": "some_weird_command"},
            "[Exit Code: 1]\nSomething completely unexpected happened",
        )
        assert sig.category == FailureCategory.UNKNOWN
        assert sig.recoverability == Recoverability.UNKNOWN


# ═════════════════════════════════════════════════════════════════
# 2. Strategy Fingerprint Tests
# ═════════════════════════════════════════════════════════════════

class TestStrategyFingerprint:
    """Verify strategy diversity detection."""

    def test_pip_install_strategy(self):
        """pip install → NETWORK_PACKAGE_INSTALL."""
        assert infer_strategy_fingerprint("bash", {"command": "pip install pygame"}) \
            == "NETWORK_PACKAGE_INSTALL"

    def test_pip_install_mirror_same_strategy(self):
        """pip install with mirror flag → SAME strategy (NETWORK_PACKAGE_INSTALL)."""
        assert infer_strategy_fingerprint("bash", {"command": "pip install pygame -i https://mirror"}) \
            == "NETWORK_PACKAGE_INSTALL"

    def test_npm_install_same_strategy(self):
        """npm install → NETWORK_PACKAGE_INSTALL."""
        assert infer_strategy_fingerprint("bash", {"command": "npm install express"}) \
            == "NETWORK_PACKAGE_INSTALL"

    def test_file_io_strategy(self):
        """read_file → LOCAL_FILE_IO."""
        assert infer_strategy_fingerprint("read_file", {"path": "test.txt"}) \
            == "LOCAL_FILE_IO"

    def test_different_strategies(self):
        """ls → SHELL_NAVIGATION, cat → FILE_READ."""
        assert infer_strategy_fingerprint("bash", {"command": "ls -la"}) \
            == "SHELL_NAVIGATION"
        assert infer_strategy_fingerprint("bash", {"command": "cat /etc/hosts"}) \
            == "FILE_READ"

    def test_lexical_diversity_same_strategy(self):
        """Verify the key insight: different args, same strategy."""
        attempts = [
            "pip install pygame",
            "pip install pygame -i https://pypi.tuna.tsinghua.edu.cn/simple",
            "pip install pygame --timeout 120",
            "pip install pygame --default-timeout=300",
            "pip install pygame --no-cache-dir",
            "pip install pygame==2.5.0",
        ]
        for cmd in attempts:
            fp = infer_strategy_fingerprint("bash", {"command": cmd})
            assert fp == "NETWORK_PACKAGE_INSTALL", \
                f"'{cmd}' → {fp}, expected NETWORK_PACKAGE_INSTALL"

# ═════════════════════════════════════════════════════════════════
# 4. FailureMemory Tests
# ═════════════════════════════════════════════════════════════════

class TestFailureMemory:
    """Verify per-task failure tracking."""

    def test_category_counting(self):
        mem = FailureMemory()
        mem.set_task("t1")
        mem.record("NETWORK_UNREACHABLE", "NETWORK_PACKAGE_INSTALL")
        mem.record("NETWORK_UNREACHABLE", "NETWORK_PACKAGE_INSTALL")
        mem.record("NETWORK_UNREACHABLE", "NETWORK_PACKAGE_INSTALL")
        assert mem.get_category_count("NETWORK_UNREACHABLE") == 3
        assert mem.get_category_count("PERMISSION_DENIED") == 0

    def test_strategy_diversity(self):
        mem = FailureMemory()
        mem.set_task("t2")
        mem.record("NETWORK_UNREACHABLE", "NETWORK_PACKAGE_INSTALL")
        mem.record("NETWORK_UNREACHABLE", "NETWORK_PACKAGE_INSTALL")
        assert mem.get_strategy_diversity("NETWORK_UNREACHABLE") == 1

        # Try a new strategy
        mem.record("NETWORK_UNREACHABLE", "NETWORK_DOWNLOAD")
        assert mem.get_strategy_diversity("NETWORK_UNREACHABLE") == 2

    def test_task_isolation(self):
        mem = FailureMemory()
        mem.set_task("task_a")
        mem.record("NETWORK_UNREACHABLE", "NP")
        mem.set_task("task_b")
        mem.record("PERMISSION_DENIED", "P")
        assert mem.get_category_count("NETWORK_UNREACHABLE") == 0
        mem.set_task("task_a")
        assert mem.get_category_count("NETWORK_UNREACHABLE") == 1


# ═════════════════════════════════════════════════════════════════
# 5. ToolTrace Extension Tests
# ═════════════════════════════════════════════════════════════════

class TestToolTraceFailureFields:
    """Verify ToolTrace carries failure intelligence fields."""

    def test_new_fields_present(self):
        tt = ToolTrace(
            tool_name="bash",
            failure_category="NETWORK_UNREACHABLE",
            recoverability="USER_INTERVENTION_REQUIRED",
            strategy_fingerprint="NETWORK_PACKAGE_INSTALL",
            escalated=True,
        )
        d = tt.to_dict()
        assert d["failure_category"] == "NETWORK_UNREACHABLE"
        assert d["recoverability"] == "USER_INTERVENTION_REQUIRED"
        assert d["strategy_fingerprint"] == "NETWORK_PACKAGE_INSTALL"
        assert d["escalated"] is True

    def test_default_empty(self):
        """By default, failure fields should be empty/false."""
        tt = ToolTrace(tool_name="bash")
        d = tt.to_dict()
        assert d["failure_category"] == ""
        assert d["recoverability"] == ""
        assert d["strategy_fingerprint"] == ""
        assert d["escalated"] is False

    def test_trace_manager_records_fields(self):
        """Verify TraceManager.record_tool_call accepts and stores failure fields."""
        tm = TraceManager(trace_dir=None)
        tm.start_task("fi_test")
        tm.start_turn(0)
        tm.record_tool_call(
            tool_name="bash", args_hash="abc",
            success=False, error_message="network error",
            failure_category="NETWORK_UNREACHABLE",
            recoverability="USER_INTERVENTION_REQUIRED",
            strategy_fingerprint="NETWORK_PACKAGE_INSTALL",
            escalated=True,
        )

        # Check that the tool trace was saved correctly
        task = tm.current_task
        assert task is not None
        assert len(task.turns) == 0  # turn not yet closed
        assert len(tm.current_turn.tools) == 1
        tt = tm.current_turn.tools[0]
        assert tt.failure_category == "NETWORK_UNREACHABLE"
        assert tt.recoverability == "USER_INTERVENTION_REQUIRED"
        assert tt.strategy_fingerprint == "NETWORK_PACKAGE_INSTALL"
        assert tt.escalated is True

        tm.end_task("SUCCESS")


def test_lexically_different_package_commands_share_strategy_evidence():
    """Strategy inference is evidence; RuntimePolicy owns escalation."""
    variants = [
        "pip install pygame",
        "pip install pygame --timeout 120",
        "pip install pygame -i https://pypi.tuna.tsinghua.edu.cn/simple",
        "pip install pygame --default-timeout=300",
        "pip install pygame==2.6.0",
        "pip install 'pygame>=2.0'",
    ]
    fps = [infer_strategy_fingerprint("bash", {"command": c}) for c in variants]
    assert set(fps) == {"NETWORK_PACKAGE_INSTALL"}

def test_http_5xx_is_recoverable_service_evidence():
    """A swallowed HTTP 5xx must enter history without becoming an invariant."""
    from core.failure_intelligence import FailureAnalyzer

    signature = FailureAnalyzer().analyze(
        "bash", {"command": "python health_check.py"},
        "[Exit Code: 0]\nHTTP/1.0 503 Service Unavailable",
    )
    assert signature.category is FailureCategory.NETWORK_UNREACHABLE
    assert signature.recoverability is Recoverability.PARTIALLY_RECOVERABLE
