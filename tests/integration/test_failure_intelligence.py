"""
test_failure_intelligence.py — Regression tests for the Failure Intelligence Layer.

Verifies:
  - Failure classification (pip install failures → NETWORK_UNREACHABLE / PACKAGE_NOT_FOUND)
  - Strategy fingerprint inference
  - Escalation policy triggers after N same-category failures
  - ToolTrace extension carries failure fields
  - End-to-end: agent with failure intelligence stops retrying after escalation
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
    FailureAnalyzer, FailureMemory, FailureEscalationPolicy,
    infer_strategy_fingerprint, build_escalation_message,
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
# 3. Escalation Policy Tests
# ═════════════════════════════════════════════════════════════════

class TestEscalationPolicy:
    """Verify escalation triggers correctly."""

    def setup_method(self):
        self.policy = FailureEscalationPolicy()
        self.memory = FailureMemory()
        self.memory.set_task("test_esc")
        self.analyzer = FailureAnalyzer()

    def _make_net_sig(self):
        return self.analyzer.analyze(
            "bash", {"command": "pip install pygame"},
            "[Exit Code: 1]\nFailed to establish a new connection",
        )

    def test_no_escalation_first_failure(self):
        """Single failure → no escalation."""
        sig = self._make_net_sig()
        should, _ = self.policy.should_escalate(sig, 1, 1)
        assert not should

    def test_escalation_after_3_network_failures(self):
        """3 same-category failures, 1 strategy → ESCALATE."""
        sig = self._make_net_sig()
        should, reason = self.policy.should_escalate(sig, 3, 1)
        assert should
        assert "用户干预" in reason or "持续" in reason

    def test_no_escalation_diverse_strategies(self):
        """3 same-category failures but 2 strategies → NO escalation."""
        sig = self._make_net_sig()
        should, _ = self.policy.should_escalate(sig, 3, 2)
        assert not should

    def test_high_water_escalates_regardless(self):
        """5 same-category failures → escalate regardless of diversity."""
        sig = self._make_net_sig()
        should, _ = self.policy.should_escalate(sig, 5, 10)
        assert should

    def test_scalable_failure_no_escalation(self):
        """SELF_HEALABLE failure, even with 3 counts → NO escalation."""
        sig = self.analyzer.analyze(
            "bash", {"command": "cat ghost.md"},
            "cat: ghost.md: No such file or directory",
        )
        # FILE_NOT_FOUND is SELF_HEALABLE, not in USER_INTERVENTION_CATEGORIES
        should, _ = self.policy.should_escalate(sig, 3, 1)
        assert not should


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


# ═════════════════════════════════════════════════════════════════
# 6. Escalation Message Tests
# ═════════════════════════════════════════════════════════════════

class TestEscalationMessage:
    """Verify escalation message format and content."""

    def test_message_contains_key_info(self):
        sig = FailureSignature(
            category=FailureCategory.NETWORK_UNREACHABLE,
            recoverability=Recoverability.USER_INTERVENTION_REQUIRED,
            root_cause_hint="网络不可达",
            tool_name="bash",
            strategy_fingerprint="NETWORK_PACKAGE_INSTALL",
        )
        msg = build_escalation_message(sig, "网络不可达，继续重试无效")
        assert "NETWORK_UNREACHABLE" in msg
        assert "USER_INTERVENTION_REQUIRED" in msg
        assert "Escalation" in msg
        assert "Failure Intelligence" in msg


# ═════════════════════════════════════════════════════════════════
# 7. Pygame Regression Scenario (E2E Simulation)
# ═════════════════════════════════════════════════════════════════

class TestPygameRegression:
    """Simulate the 'pip install pygame' failure scenario.

    Before failure intelligence: agent would retry ~34 turns with different
    pip flags, all hitting the same network error.

    After failure intelligence: escalation triggers after 3 same-category
    failures with the same strategy, terminating the retry loop early.
    """

    def test_escalation_happens_within_3_failures(self):
        """Core assertion: escalation fires by the 3rd same-category failure."""
        memory = FailureMemory()
        memory.set_task("pygame_test")
        policy = FailureEscalationPolicy()

        # Simulate 3 pip install attempts, all hitting network unreachable
        sig = FailureSignature(
            category=FailureCategory.NETWORK_UNREACHABLE,
            recoverability=Recoverability.USER_INTERVENTION_REQUIRED,
            root_cause_hint="网络不可达",
            fingerprint="NETWORK_UNREACHABLE::NETWORK_PACKAGE_INSTALL",
            strategy_fingerprint="NETWORK_PACKAGE_INSTALL",
        )

        # Attempt 1: no escalation
        should, _ = policy.should_escalate(sig, 1, 1)
        assert not should, "First failure should NOT escalate"

        # Attempt 2: no escalation
        should, _ = policy.should_escalate(sig, 2, 1)
        assert not should, "Second failure should NOT escalate"

        # Attempt 3: escalation!
        should, reason = policy.should_escalate(sig, 3, 1)
        assert should, f"Third failure SHOULD escalate, got: {reason}"

    def test_escalation_message_stops_retry_loop(self):
        """Verify escalation message is a termination signal, not a retry prompt."""
        sig = FailureSignature(
            category=FailureCategory.NETWORK_UNREACHABLE,
            recoverability=Recoverability.USER_INTERVENTION_REQUIRED,
            root_cause_hint="网络不可达",
            strategy_fingerprint="NETWORK_PACKAGE_INSTALL",
        )
        msg = build_escalation_message(sig, "网络不可达，继续重试无效")

        # The message should tell the agent to STOP, not to retry
        assert "Escalation" in msg or "escalation" in msg
        # It should reference the user, suggesting external intervention
        assert "用户" in msg or "user" in msg or "User" in msg

    def test_lexically_different_but_same_strategy(self):
        """Verify that all pip install variants produce the SAME strategy fingerprint.

        This is the key regression: without failure intelligence, LoopGuard
        sees different args_hash and doesn't block. With FI, we detect the
        same strategy and escalate.
        """
        variants = [
            "pip install pygame",
            "pip install pygame --timeout 120",
            "pip install pygame -i https://pypi.tuna.tsinghua.edu.cn/simple",
            "pip install pygame --default-timeout=300",
            "pip install pygame==2.6.0",
            "pip install 'pygame>=2.0'",
        ]
        fps = [infer_strategy_fingerprint("bash", {"command": c}) for c in variants]
        unique = set(fps)
        assert len(unique) == 1, \
            f"All pip install variants should have same strategy, got: {unique}"
        assert unique.pop() == "NETWORK_PACKAGE_INSTALL"
