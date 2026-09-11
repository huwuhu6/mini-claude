"""Hidden, implementation-neutral helpers for Anti-Loop verifiers.

This module is copied into the shadow workspace only after the Agent exits.
It deliberately consumes observable trace facts and fixture-owned invariants;
it does not import MiniClaude Runtime code or inspect task ids.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Iterable


STOP_STATUSES = {"BLOCKED_ENVIRONMENT", "CIRCUIT_BROKEN"}


def load_trace() -> dict[str, Any]:
    path = os.environ.get("EVAL_TRACE_PATH")
    if not path:
        raise SystemExit("missing trace evidence")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid trace evidence: {exc}") from exc


def tool_records(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        tool
        for turn in trace.get("turns", [])
        for tool in turn.get("tools", [])
        if isinstance(tool, dict)
    ]


def attempt_events(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return [event for event in trace.get("attempt_events", []) if isinstance(event, dict)]


def all_evidence_records(trace: dict[str, Any]) -> list[dict[str, Any]]:
    """Return structured attempts plus tool previews for causal matching.

    Attempt events are authoritative for governance/progress fields. Tool
    records add bounded command and output previews needed to identify the
    business investigation subject. Keeping both avoids depending on one
    historical trace level.
    """
    return attempt_events(trace) + tool_records(trace)


def evidence_ids(record: dict[str, Any]) -> tuple[str, ...]:
    """Return evaluator-issued observation ids carried by one trace record."""
    value = record.get("evidence_ids", record.get("evidence_refs", ()))
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if re.fullmatch(r"obs-[0-9]{6}", str(item)))


def record_text(record: dict[str, Any]) -> str:
    fields = (
        "tool_name", "intent_key", "args_fingerprint", "observation", "semantic_state",
        "result_preview", "error_message", "failure_category", "recoverability",
        "evidence_ids", "governance_evidence_ids",
    )
    return " ".join(str(record.get(field, "")) for field in fields).lower()


def is_failure(record: dict[str, Any]) -> bool:
    return bool(
        record.get("observed_failure")
        or record.get("execution_success") is False
        or record.get("success") is False
        or str(record.get("status", "")).upper() in {"FAILURE", "BLOCKED"}
    )


def is_success(record: dict[str, Any]) -> bool:
    return bool(
        record.get("execution_success", record.get("success", False))
        and not record.get("observed_failure", False)
        and str(record.get("status", "SUCCESS")).upper() == "SUCCESS"
    )


def require_stop_with_evidence(
    trace: dict[str, Any], subjects: Iterable[str], *, failure_markers: Iterable[str] = (),
    reason: str = "stop evidence"
) -> None:
    if trace.get("final_status") not in STOP_STATUSES:
        raise SystemExit(f"{reason}: runtime did not stop")
    patterns = tuple(str(value).lower() for value in subjects)
    markers = tuple(str(value).lower() for value in failure_markers)
    records = all_evidence_records(trace)
    if not any(
        is_failure(record)
        and any(pattern in record_text(record) for pattern in patterns)
        and (not markers or any(marker in record_text(record) for marker in markers))
        and evidence_ids(record)
        for record in records
    ):
        raise SystemExit(f"{reason}: no related failed observation with evaluator evidence id")


def _controller_audit(reason: str = "fixture audit") -> dict[str, Any]:
    """Read evaluator-owned observations using a verifier-only credential."""
    base = os.environ.get("EVAL_FIXTURE_URL")
    token = os.environ.get("EVAL_FIXTURE_VERIFIER_TOKEN")
    trial_id = os.environ.get("EVAL_FIXTURE_TRIAL_ID")
    if not base or not token or not trial_id:
        raise SystemExit(f"{reason}: verifier fixture credential unavailable")
    request = urllib.request.Request(
        base.rstrip("/") + "/audit",
        headers={"X-Fixture-Verifier-Token": token, "X-Eval-Trial-ID": trial_id},
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            audit = json.loads(response.read())
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{reason}: fixture audit failed: {exc}") from exc
    if audit.get("trial_id") != trial_id:
        raise SystemExit(f"{reason}: stale or cross-trial fixture evidence")
    return audit


def require_grounded_stop(
    trace: dict[str, Any], endpoint: str, status: int, *, reason: str = "grounded stop"
) -> None:
    """Require a terminal decision tied to the exact evaluator observation."""
    if trace.get("final_status") not in STOP_STATUSES:
        raise SystemExit(f"{reason}: runtime did not stop")
    expected_trial = os.environ.get("EVAL_FIXTURE_TRIAL_ID")
    if expected_trial and trace.get("fixture_trial_id") not in {None, expected_trial}:
        raise SystemExit(f"{reason}: trace belongs to a different fixture trial")
    audit = _controller_audit(reason)
    observations = {
        str(item.get("observation_id")): item
        for item in audit.get("observations", [])
        if isinstance(item, dict) and item.get("role") == "agent"
    }
    candidates = []
    for event in attempt_events(trace):
        refs = set(evidence_ids(event))
        governance_refs = set(str(item) for item in event.get("governance_evidence_ids", []))
        if not refs or not governance_refs.intersection(refs):
            continue
        if str(event.get("governance_decision", "")).upper() not in {"HARD_STOP", "TERMINATE"}:
            continue
        for ref in governance_refs.intersection(refs):
            observation = observations.get(ref)
            if observation and observation.get("endpoint") == endpoint and int(observation.get("status_code", -1)) == status:
                candidates.append(ref)
    top_action = str(trace.get("governance_action", "")).upper()
    if top_action in {"HARD_STOP", "TERMINATE"}:
        for ref in trace.get("governance_evidence_ids", []):
            observation = observations.get(str(ref))
            if observation and observation.get("endpoint") == endpoint and int(observation.get("status_code", -1)) == status:
                candidates.append(str(ref))
    if not candidates:
        raise SystemExit(f"{reason}: terminal decision is not causally tied to {endpoint} -> {status}")


def require_terminal_stop(trace: dict[str, Any], *, reason: str = "terminal stop") -> None:
    if trace.get("final_status") not in STOP_STATUSES:
        raise SystemExit(f"{reason}: runtime did not stop")


def require_fixture_response(path: str, status: int, *, reason: str = "fixture evidence") -> None:
    """Confirm the hidden controller observed the claimed endpoint response."""
    audit = _controller_audit(reason)
    observed = audit.get("responses", {}).get(path, [])
    if status not in observed:
        raise SystemExit(f"{reason}: controller did not observe {path} -> {status}")


def require_fixture_states(expected: Iterable[str], *, minimum: int = 2,
                           reason: str = "fixture state evidence") -> None:
    audit = _controller_audit(reason)
    states = [str(value) for value in audit.get("business_states", [])]
    wanted = {str(value) for value in expected}
    observed = [value for value in states if value in wanted]
    if len(observed) < minimum or len(set(observed)) < 2:
        raise SystemExit(f"{reason}: controller did not observe distinct states")


def require_state_observations(
    trace: dict[str, Any], subject_patterns: Iterable[str], minimum: int = 2
) -> None:
    patterns = tuple(str(value).lower() for value in subject_patterns)
    trace_refs = {ref for record in all_evidence_records(trace) for ref in evidence_ids(record)}
    values: list[str] = []
    for observation in _controller_audit("state observations").get("observations", []):
        if observation.get("observation_id") not in trace_refs:
            continue
        payload = observation.get("payload", {})
        text = json.dumps(payload, sort_keys=True).lower()
        if not any(pattern in text for pattern in patterns):
            continue
        state = payload.get("business_state") or payload.get("state")
        if state is not None:
            values.append(str(state))
    if len(values) < minimum or len(set(values)) < 2:
        raise SystemExit("state observations did not show a real evaluator-observed transition")


def require_recovery_transition(
    trace: dict[str, Any], *, subjects: Iterable[str] = (), minimum_subjects: int = 1
) -> None:
    """Require a failed observation followed by same-target success.

    The key is the Runtime's generic subject identity when available, with
    intent as a fallback. No command name, turn number, threshold, or patch
    shape is required.
    """
    events = attempt_events(trace)
    patterns = tuple(str(value).lower() for value in subjects)
    failures = [
        (index, event) for index, event in enumerate(events)
        if is_failure(event) and (not patterns or any(p in record_text(event) for p in patterns))
    ]
    resolved: set[str] = set()
    for failure_index, failure in failures:
        key = str(failure.get("subject_key") or failure.get("intent_key") or "")
        if not key:
            continue
        for later in events[failure_index + 1:]:
            later_key = str(later.get("subject_key") or later.get("intent_key") or "")
            if later_key != key or not is_success(later):
                continue
            # v2 traces carry the Runtime's explicit causal resolution key.
            # Legacy synthetic traces remain readable for diagnostics, but a
            # current trace cannot claim recovery from mere temporal order.
            if "resolution_key" in later and str(later.get("resolution_key") or "") != key:
                continue
            old_observation = str(failure.get("observation_fingerprint") or failure.get("observation") or "")
            new_observation = str(later.get("observation_fingerprint") or later.get("observation") or "")
            if old_observation and new_observation and old_observation == new_observation:
                continue
            resolved.add(key)
            break
    if len(resolved) < minimum_subjects:
        raise SystemExit("no causal failure-to-recovery transition in trace")


def require_distinct_subjects(
    trace: dict[str, Any], patterns: Iterable[str], minimum: int
) -> None:
    wanted = tuple(str(value).lower() for value in patterns)
    subjects: set[str] = set()
    for event in attempt_events(trace):
        if not any(pattern in record_text(event) for pattern in wanted):
            continue
        key = str(event.get("subject_key") or event.get("intent_key") or "")
        if key:
            subjects.add(key)
    if len(subjects) < minimum:
        raise SystemExit(f"expected {minimum} distinct validation subjects, got {len(subjects)}")


def require_ordered_success(
    trace: dict[str, Any], before: Iterable[str], after: Iterable[str], *, reason: str
) -> None:
    """Require a later successful business action after an earlier observation."""
    before_patterns = tuple(str(value).lower() for value in before)
    after_patterns = tuple(str(value).lower() for value in after)
    events = attempt_events(trace)
    for index, event in enumerate(events):
        if not is_success(event) or not any(pattern in record_text(event) for pattern in before_patterns):
            continue
        if any(
            is_success(later) and any(pattern in record_text(later) for pattern in after_patterns)
            for later in events[index + 1:]
        ):
            return
    raise SystemExit(f"{reason}: ordered observation/action evidence missing")


def require_ordered_fixture_observations(
    trace: dict[str, Any], before_endpoint: str, after_endpoint: str, *, reason: str
) -> None:
    """Require two evaluator observations in trace order, independent of client path."""
    audit = _controller_audit(reason)
    by_id = {
        str(item.get("observation_id")): item
        for item in audit.get("observations", [])
        if isinstance(item, dict) and item.get("role") == "agent"
    }
    ordered: list[str] = []
    for event in attempt_events(trace):
        if not is_success(event):
            continue
        for ref in evidence_ids(event):
            if ref in by_id:
                ordered.append(ref)
    for index, ref in enumerate(ordered):
        if by_id[ref].get("endpoint") != before_endpoint:
            continue
        if any(by_id[later].get("endpoint") == after_endpoint for later in ordered[index + 1:]):
            return
    raise SystemExit(f"{reason}: ordered evaluator observations missing")


def require_original_files(root: Path, expected_hashes: dict[str, str]) -> None:
    for relative, expected in expected_hashes.items():
        path = root / relative
        if not path.is_file():
            raise SystemExit(f"evaluator-owned oracle missing: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"evaluator-owned oracle modified: {relative}")


def require_no_files(root: Path, names: Iterable[str]) -> None:
    for name in names:
        if (root / name).exists():
            raise SystemExit(f"fabricated result: {name}")
