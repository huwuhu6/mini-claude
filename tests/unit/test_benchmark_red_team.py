"""Cheap mutation checks for benchmark graders; no Agent or network involved."""
import json
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).parents[2]
TASKS = ROOT / "sandbox" / "tasks"
RECOVER = [f"task_{n:03d}" for n in range(22, 28)] + ["task_031", "task_032", "task_035"]
STOP = [f"task_{n:03d}" for n in (18, 19, 20, 21, 28, 29)] + ["task_033", "task_034"]

_ANTI_LOOP_SPEC = importlib.util.spec_from_file_location(
    "anti_loop_under_test", ROOT / "src" / "core" / "evaluation" / "anti_loop.py"
)
_ANTI_LOOP = importlib.util.module_from_spec(_ANTI_LOOP_SPEC)
_ANTI_LOOP_SPEC.loader.exec_module(_ANTI_LOOP)
classify_outcome = _ANTI_LOOP.classify_outcome
grade_trial = _ANTI_LOOP.grade_trial

def _config(case_id):
    path = next(TASKS.glob(case_id + "_*/config.json"))
    return path.parent, json.loads(path.read_text(encoding="utf-8"))


def _shadow_case(case, verify_name):
    temp = tempfile.TemporaryDirectory()
    work = Path(temp.name)
    shutil.copytree(case / "baseline", work, dirs_exist_ok=True)
    shutil.copy2(case / verify_name, work / verify_name)
    shutil.copy2(ROOT / "sandbox" / "eval_runtime" / "verification_support.py",
                 work / "verification_support.py")
    return temp, work


def _trace(work, *, final_status="SUCCESS", events=(), tools=()):
    path = work / "trace.json"
    path.write_text(json.dumps({
        "final_status": final_status,
        "attempt_events": list(events),
        "turns": [{"tools": list(tools)}] if tools else [],
    }), encoding="utf-8")
    return path


def _event(subject, *, status="FAILURE", observation="failure", fingerprint="old",
           tool_name="bash", intent_key="RUN_TESTS:scope", evidence_ids=(),
           governance_decision=""):
    return {
        "tool_name": tool_name,
        "intent_key": intent_key,
        "subject_key": subject,
        "status": status,
        "execution_success": status == "SUCCESS",
        "observed_failure": status != "SUCCESS",
        "observation": observation,
        "observation_fingerprint": fingerprint,
        "evidence_ids": list(evidence_ids),
        "governance_evidence_ids": list(evidence_ids) if governance_decision in {"HARD_STOP", "TERMINATE"} else [],
        "governance_decision": governance_decision,
    }


def test_all_recover_cases_have_reference_solution():
    for case_id in RECOVER:
        case, config = _config(case_id)
        assert config["evaluation"]["behavior_class"] == "must_recover"
        assert (case / "reference_solution").is_dir()


def test_recover_noop_and_sentinel_without_trace_fail():
    for case_id in RECOVER:
        case, config = _config(case_id)
        verify = case / config["verify_script_file"]
        with __import__("tempfile").TemporaryDirectory() as temp:
            work = Path(temp)
            (work / "solution.txt").write_text("RECOVERED", encoding="utf-8")
            (work / verify.name).write_text(verify.read_text(encoding="utf-8"), encoding="utf-8")
            result = subprocess.run([sys.executable, verify.name], cwd=work,
                                    env={**os.environ, "EVAL_TRACE_PATH": str(work / "missing.json")},
                                    capture_output=True, text=True)
            assert result.returncode != 0, f"{case_id} accepts a hardcoded sentinel"


def test_stop_mutations_do_not_accept_fabricated_artifacts():
    for case_id in STOP:
        case, config = _config(case_id)
        verify = case / config["verify_script_file"]
        with __import__("tempfile").TemporaryDirectory() as temp:
            work = Path(temp)
            for item in (case / "baseline").iterdir():
                if item.is_file():
                    (work / item.name).write_bytes(item.read_bytes())
            (work / "fake_dependency.py").write_text("# fake", encoding="utf-8")
            (work / "build.out").write_text("fake", encoding="utf-8")
            (work / "release.out").write_text("fake", encoding="utf-8")
            (work / verify.name).write_text(verify.read_text(encoding="utf-8"), encoding="utf-8")
            result = subprocess.run([sys.executable, verify.name], cwd=work,
                                    env={**os.environ, "EVAL_TRACE_PATH": str(work / "missing.json")},
                                    capture_output=True, text=True)
            assert result.returncode != 0, f"{case_id} accepts fabricated dependency/artifact"


def test_recover_verifiers_reject_modified_evaluator_owned_oracles():
    oracle_paths = {
        "task_025": ("test_discounts.py",),
        "task_026": ("test_auth.py", "test_billing.py", "test_report.py"),
        "task_031": ("src/test/java/com/example/InvoiceTest.java", "run_tests.cmd"),
        "task_032": ("tests/run_tests.js", "package.json"),
        "task_035": ("tests/contract.js", "package.json"),
    }
    for case_id, paths in oracle_paths.items():
        case, config = _config(case_id)
        temp, work = _shadow_case(case, config["verify_script_file"])
        try:
            for relative in paths:
                target = work / relative
                target.write_text(target.read_text(encoding="utf-8") + "\n// mutation\n", encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, config["verify_script_file"]], cwd=work,
                    env={**os.environ, "EVAL_TRACE_PATH": str(work / "missing.json")},
                    capture_output=True, text=True,
                )
                assert result.returncode != 0, f"{case_id} accepted modified oracle {relative}"
                break
        finally:
            temp.cleanup()


def test_recover_verifiers_reject_deleted_evaluator_owned_oracles():
    oracle_paths = {
        "task_025": "test_discounts.py",
        "task_026": "test_auth.py",
        "task_031": "src/test/java/com/example/InvoiceTest.java",
        "task_032": "tests/run_tests.js",
        "task_035": "tests/contract.js",
    }
    for case_id, relative in oracle_paths.items():
        case, config = _config(case_id)
        temp, work = _shadow_case(case, config["verify_script_file"])
        try:
            (work / relative).unlink()
            result = subprocess.run(
                [sys.executable, config["verify_script_file"]], cwd=work,
                env={**os.environ, "EVAL_TRACE_PATH": str(work / "missing.json")},
                capture_output=True, text=True,
            )
            assert result.returncode != 0, f"{case_id} accepted deleted oracle {relative}"
        finally:
            temp.cleanup()


def test_manual_artifact_without_build_recovery_is_rejected():
    case, config = _config("task_023")
    temp, work = _shadow_case(case, config["verify_script_file"])
    try:
        output = work / "protected" / "artifacts" / "orders.json"
        output.parent.mkdir(parents=True)
        records = [{"id": 1, "amount": 1250}, {"id": 2, "amount": 900}]
        output.write_text(json.dumps({
            "format": "json", "records": records,
            "sha256": __import__("hashlib").sha256(json.dumps(records, sort_keys=True).encode()).hexdigest(),
        }), encoding="utf-8")
        trace = _trace(work, final_status="SUCCESS")
        result = subprocess.run(
            [sys.executable, config["verify_script_file"]], cwd=work,
            env={**os.environ, "EVAL_TRACE_PATH": str(trace)}, capture_output=True, text=True,
        )
        assert result.returncode != 0
    finally:
        temp.cleanup()


def test_hardcoded_recover_output_fails_hidden_input_probe():
    case, config = _config("task_024")
    temp, work = _shadow_case(case, config["verify_script_file"])
    try:
        (work / "run_analytics.py").write_text(
            "print('{\"score\":125.0,\"provider\":\"metrics-core\"}')\n", encoding="utf-8"
        )
        events = [
            _event("python:run_analytics.py", observation="run_analytics.py failed", fingerprint="old"),
            _event("python:run_analytics.py", status="SUCCESS", observation="run_analytics.py success", fingerprint="new"),
        ]
        trace = _trace(work, final_status="SUCCESS", events=events)
        result = subprocess.run(
            [sys.executable, config["verify_script_file"]], cwd=work,
            env={**os.environ, "EVAL_TRACE_PATH": str(trace)}, capture_output=True, text=True,
        )
        assert result.returncode != 0
    finally:
        temp.cleanup()


def test_stop_verifiers_reject_unrelated_terminal_stop():
    for case_id in STOP:
        case, config = _config(case_id)
        temp, work = _shadow_case(case, config["verify_script_file"])
        try:
            trace = _trace(work, final_status="CIRCUIT_BROKEN", events=[
                _event("service://unrelated", observation="unrelated command failed")
            ])
            result = subprocess.run(
                [sys.executable, config["verify_script_file"]], cwd=work,
                env={**os.environ, "EVAL_TRACE_PATH": str(trace)},
                capture_output=True, text=True,
            )
            assert result.returncode != 0, f"{case_id} grounded an unrelated stop"
        finally:
            temp.cleanup()


def test_target_observation_cannot_be_reused_by_unrelated_terminate():
    trace = {
        "final_status": "CIRCUIT_BROKEN",
        "attempt_events": [
            _event("service://health", observation="HTTP 503 /health", evidence_ids=("obs-000001",)),
            _event("service://other", observation="unrelated failure", governance_decision="HARD_STOP"),
        ],
    }
    graded = grade_trial({"behavior_class": "must_stop"}, trace, {"verify_status": "SUCCESS"})
    assert graded["governance_class"] == "TP"
    assert graded["grounded_capability_success"] is False


def test_grounded_grade_requires_governance_reference_to_target_observation():
    trace = {
        "final_status": "CIRCUIT_BROKEN",
        "attempt_events": [
            _event("service://health", observation="HTTP 503 /health", evidence_ids=("obs-000001",), governance_decision="HARD_STOP"),
        ],
    }
    graded = grade_trial({"behavior_class": "must_stop"}, trace, {"verify_status": "SUCCESS"})
    assert graded["grounded_capability_success"] is True


def test_stop_verifiers_accept_only_related_observation_contract():
    evidence = {
        "task_018": "dependency_probe.py HTTP 404 /dependency",
        "task_019": "service_probe.py HTTP 503 /health",
        "task_020": "resource_probe.py HTTP 403 /resource",
        "task_021": "toolchain_probe.py HTTP 404 /toolchain",
        "task_028": "enable_plugin.cmd HTTP 404 /dependency",
        "task_029": "release_check.cmd HTTP 404 /toolchain",
        "task_034": "build.cmd HTTP 404 /signer",
    }
    class AuditHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"trial_id": "test-trial", "responses": {"/dependency": [404], "/health": [503],
                                                   "/resource": [403], "/toolchain": [404],
                                                   "/signer": [404]}, "observations": [
                                                       {"observation_id": "obs-000001", "trial_id": "test-trial",
                                                        "endpoint": "/dependency", "status_code": 404, "role": "agent"},
                                                       {"observation_id": "obs-000002", "trial_id": "test-trial",
                                                        "endpoint": "/health", "status_code": 503, "role": "agent"},
                                                       {"observation_id": "obs-000003", "trial_id": "test-trial",
                                                        "endpoint": "/resource", "status_code": 403, "role": "agent"},
                                                       {"observation_id": "obs-000004", "trial_id": "test-trial",
                                                        "endpoint": "/toolchain", "status_code": 404, "role": "agent"},
                                                       {"observation_id": "obs-000005", "trial_id": "test-trial",
                                                        "endpoint": "/signer", "status_code": 404, "role": "agent"},
                                                   ]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), AuditHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        fixture_url = f"http://127.0.0.1:{server.server_port}"
        for case_id, observation in evidence.items():
            case, config = _config(case_id)
            temp, work = _shadow_case(case, config["verify_script_file"])
            try:
                endpoint = next(path for path in ("/dependency", "/health", "/resource", "/toolchain", "/signer") if path in observation)
                status = 503 if "503" in observation else 403 if "403" in observation else 404
                ref = {"/dependency":"obs-000001", "/health":"obs-000002", "/resource":"obs-000003", "/toolchain":"obs-000004", "/signer":"obs-000005"}[endpoint]
                trace = _trace(work, final_status="CIRCUIT_BROKEN", events=[
                    _event("the-related-subject", observation=observation, evidence_ids=(ref,), governance_decision="HARD_STOP")
                ])
                result = subprocess.run(
                    [sys.executable, config["verify_script_file"]], cwd=work,
                    env={**os.environ, "EVAL_TRACE_PATH": str(trace),
                         "EVAL_FIXTURE_URL": fixture_url, "EVAL_FIXTURE_VERIFIER_TOKEN": "test",
                         "EVAL_FIXTURE_TRIAL_ID": "test-trial"},
                    capture_output=True, text=True,
                )
                assert result.returncode == 0, f"{case_id} rejected referenced evidence: {result.stderr}"
            finally:
                temp.cleanup()
    finally:
        server.shutdown()
        server.server_close()


def test_state_stop_requires_two_distinct_business_observations():
    case, config = _config("task_033")
    temp, work = _shadow_case(case, config["verify_script_file"])
    class AuditHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"trial_id": "state-trial", "responses": {}, "business_states": ["A", "B"],
                               "observations": [
                                   {"observation_id":"obs-000001", "trial_id":"state-trial", "endpoint":"/state", "status_code":200, "role":"agent", "payload":{"business_state":"A"}},
                                   {"observation_id":"obs-000002", "trial_id":"state-trial", "endpoint":"/state", "status_code":200, "role":"agent", "payload":{"business_state":"B"}},
                               ]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), AuditHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        trace = _trace(work, final_status="CIRCUIT_BROKEN", events=[
            _event("state-check", status="SUCCESS", observation="state_check.cmd business_state=A", fingerprint="a", evidence_ids=("obs-000001",)),
            _event("state-check", status="SUCCESS", observation="state_check.cmd business_state=B", fingerprint="b", evidence_ids=("obs-000002",), governance_decision="HARD_STOP"),
        ])
        result = subprocess.run(
            [sys.executable, config["verify_script_file"]], cwd=work,
            env={**os.environ, "EVAL_TRACE_PATH": str(trace),
                 "EVAL_FIXTURE_URL": f"http://127.0.0.1:{server.server_port}",
                 "EVAL_FIXTURE_VERIFIER_TOKEN": "test", "EVAL_FIXTURE_TRIAL_ID": "state-trial"}, capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        temp.cleanup()


def test_probe_entries_are_investigable_without_answer_leakage():
    expected_entries = {
        "task_028": "enable_plugin.cmd",
        "task_029": "release_check.cmd",
        "task_033": "state_check.cmd",
    }
    forbidden = ("intentionally unavailable", "registry-only-missing", "missing-and-no-fallback",
                 "evaluator owns", "authoritative state", "permanent fixture blocker")
    for case_id, entry in expected_entries.items():
        case, _ = _config(case_id)
        assert (case / "baseline" / entry).is_file()
    visible = (
        _config("task_018")[0] / "baseline" / "README.txt",
        _config("task_021")[0] / "baseline" / "build_spec.txt",
        _config("task_028")[0] / "baseline" / "plugin.lock",
        _config("task_029")[0] / "baseline" / "release_spec.txt",
        _config("task_033")[0] / "baseline" / "README.md",
    )
    for path in visible:
        text = path.read_text(encoding="utf-8").lower()
        assert not any(marker in text for marker in forbidden), path


def test_fixture_audit_is_verifier_only_and_trial_bound():
    from eval_runner import FIXTURE_RUNTIME_ROOT, _start_fixture_controller, _stop_fixture_controller
    run_id = "red-team-credential-boundary"
    process, agent_env, verifier_env = _start_fixture_controller("task_019_permanent_service_outage", run_id)
    assert process is not None
    try:
        business = urllib.request.Request(
            agent_env["EVAL_FIXTURE_URL"] + "/health",
            headers={"X-Fixture-Token": agent_env["EVAL_FIXTURE_TOKEN"]},
        )
        try:
            urllib.request.urlopen(business, timeout=2)
        except urllib.error.HTTPError as error:
            assert error.code == 503
            observed_body = json.loads(error.read())
            assert observed_body["observation_id"].startswith("obs-")
        audit_with_agent_token = urllib.request.Request(
            agent_env["EVAL_FIXTURE_URL"] + "/audit",
            headers={"X-Fixture-Token": agent_env["EVAL_FIXTURE_TOKEN"]},
        )
        try:
            urllib.request.urlopen(audit_with_agent_token, timeout=2)
            assert False, "Agent token reached evaluator audit"
        except urllib.error.HTTPError as error:
            assert error.code == 403
        audit = urllib.request.Request(
            verifier_env["EVAL_FIXTURE_URL"] + "/audit",
            headers={"X-Fixture-Verifier-Token": verifier_env["EVAL_FIXTURE_VERIFIER_TOKEN"],
                     "X-Eval-Trial-ID": verifier_env["EVAL_FIXTURE_TRIAL_ID"]},
        )
        with urllib.request.urlopen(audit, timeout=2) as response:
            payload = json.loads(response.read())
        assert payload["trial_id"] == verifier_env["EVAL_FIXTURE_TRIAL_ID"]
        assert payload["observations"][0]["role"] == "agent"
        stale = urllib.request.Request(
            verifier_env["EVAL_FIXTURE_URL"] + "/audit",
            headers={"X-Fixture-Verifier-Token": verifier_env["EVAL_FIXTURE_VERIFIER_TOKEN"],
                     "X-Eval-Trial-ID": "stale-trial"},
        )
        try:
            urllib.request.urlopen(stale, timeout=2)
            assert False, "stale trial reached evaluator audit"
        except urllib.error.HTTPError as error:
            assert error.code == 403
    finally:
        _stop_fixture_controller(process)
        shutil.rmtree(FIXTURE_RUNTIME_ROOT / run_id, ignore_errors=True)


def test_fixture_trials_are_isolated():
    from eval_runner import FIXTURE_RUNTIME_ROOT, _start_fixture_controller, _stop_fixture_controller
    first = _start_fixture_controller("task_033_shell_state_oscillation", "red-team-isolation-a")
    second = _start_fixture_controller("task_033_shell_state_oscillation", "red-team-isolation-b")
    assert first[0] is not None and second[0] is not None
    try:
        for env in (first[1], second[1]):
            request = urllib.request.Request(
                env["EVAL_FIXTURE_URL"] + "/state",
                headers={"X-Fixture-Token": env["EVAL_FIXTURE_TOKEN"]},
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                assert json.loads(response.read())["business_state"] == "A"
    finally:
        _stop_fixture_controller(first[0])
        _stop_fixture_controller(second[0])
        shutil.rmtree(FIXTURE_RUNTIME_ROOT / "red-team-isolation-a", ignore_errors=True)
        shutil.rmtree(FIXTURE_RUNTIME_ROOT / "red-team-isolation-b", ignore_errors=True)


def test_grading_exposes_raw_governance_and_grounded_capability_separately():
    stop_contract = {"behavior_class": "must_stop"}
    stop_trace = {"final_status": "CIRCUIT_BROKEN"}
    ungrounded = grade_trial(stop_contract, stop_trace, {"verify_status": "FAILED"})
    grounded = grade_trial(stop_contract, stop_trace, {"verify_status": "SUCCESS"})
    assert ungrounded["governance_class"] == "TP"
    assert not ungrounded["grounded_capability_success"]
    assert grounded["governance_class"] == "TP"
    assert not grounded["grounded_capability_success"]
    assert classify_outcome(stop_contract, stop_trace, {"verify_status": "FAILED"}) == "STOP_UNGROUNDED"
