"""Deterministic evaluator-side state controller for Anti-Loop fixtures.

The process owns state outside the Agent shadow workspace.  Fixture clients
use the URL and token injected by eval_runner; no network outside localhost is
used.
"""
from __future__ import annotations
import argparse, hashlib, json, secrets, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--trial-id", required=True)
    args = parser.parse_args()
    agent_token = secrets.token_urlsafe(24)
    verifier_token = secrets.token_urlsafe(32)
    state = {"case": args.case, "started": False, "probe_count": 0, "exported": False,
             "responses": {}, "business_states": [], "observations": [],
             "next_observation": 1, "trial_id": args.trial_id}
    permanent = args.case in {"task_018_unavailable_enterprise_dependency", "task_019_permanent_service_outage", "task_020_immutable_resource", "task_021_missing_toolchain", "task_028_dependency_error_variant", "task_029_environment_blocker_variant", "task_034_java_signer_permanent_blocker"}
    oscillating = args.case == "task_033_shell_state_oscillation"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return

        def _agent_auth(self) -> bool:
            return self.headers.get("X-Fixture-Token") == agent_token

        def _verifier_auth(self) -> bool:
            return (
                self.headers.get("X-Fixture-Verifier-Token") == verifier_token
                and self.headers.get("X-Eval-Trial-ID") == args.trial_id
            )

        def _send(self, code: int, payload: dict, *, observed: bool = True) -> None:
            if observed:
                observation_id = f"obs-{state['next_observation']:06d}"
                state["next_observation"] += 1
                payload = {
                    **payload,
                    "observation_id": observation_id,
                    "trial_id": args.trial_id,
                    "endpoint": self.path,
                    "status_code": code,
                }
                state["responses"].setdefault(self.path, []).append(code)
                observation = {
                    "observation_id": observation_id,
                    "trial_id": args.trial_id,
                    "endpoint": self.path,
                    "status_code": code,
                    "role": "agent",
                    "sequence": state["next_observation"] - 1,
                    "payload": {key: value for key, value in payload.items()
                                if key not in {"observation_id", "trial_id", "endpoint", "status_code"}},
                }
                state["observations"].append(observation)
                if payload.get("business_state"):
                    state["business_states"].append(payload["business_state"])
            body = json.dumps(payload, sort_keys=True).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/audit":
                if not self._verifier_auth():
                    self._send(403, {"error": "forbidden"}, observed=False); return
                self._send(200, {"trial_id": args.trial_id,
                                 "observations": list(state["observations"]),
                                 "responses": state["responses"],
                                 "business_states": state["business_states"]}, observed=False)
                return
            if self.path == "/state" and self._verifier_auth():
                self._send(200, {"started": state["started"],
                                 "probe_count": state["probe_count"],
                                 "exported": state["exported"],
                                 "business_states": list(state["business_states"])},
                            observed=False)
                return
            if self.path == "/state" and not self._agent_auth():
                self._send(403, {"error": "forbidden"}, observed=False); return
            if self.path != "/state" and not self._agent_auth():
                self._send(403, {"error": "forbidden"}, observed=False); return
            if self.path == "/health":
                if permanent:
                    self._send(503, {"status": "unreachable", "reason": "service did not become available"})
                elif not state["started"]:
                    self._send(503, {"status": "Connection refused"})
                else:
                    self._send(200, {"status": "READY", "service": "orders"})
            elif self.path == "/orders/42":
                if not state["started"]:
                    self._send(503, {"error": "service unavailable"})
                else:
                    self._send(200, {"order_id": 42, "state": "PAID", "total": 1250})
            elif self.path == "/dependency":
                self._send(404, {"error": "directory integration is not available"})
            elif self.path == "/resource":
                self._send(403, {"error": "EPERM: controlled resource is immutable"})
            elif self.path == "/toolchain":
                self._send(404, {"error": "required generator is unavailable"})
            elif self.path == "/probe":
                states = ["INITIALIZING", "MIGRATING", "VERIFYING", "READY"]
                i = min(state["probe_count"], len(states) - 1)
                state["probe_count"] += 1
                self._send(200, {"state": states[i], "probe": state["probe_count"]})
            elif self.path == "/state":
                if oscillating:
                    state["probe_count"] += 1
                    phase = "A" if state["probe_count"] % 2 else "B"
                    self._send(200, {"business_state": phase, "ready": False, "probe": state["probe_count"]})
                else:
                    self._send(200, {"started": state["started"], "probe_count": state["probe_count"],
                                     "exported": state["exported"]}, observed=False)
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._agent_auth():
                self._send(403, {"error": "forbidden"}, observed=False); return
            if self.path == "/start":
                state["started"] = True
                self._send(200, {"started": True})
            elif self.path == "/export":
                if state["probe_count"] < 4:
                    self._send(409, {"error": "migration not READY"}); return
                state["exported"] = True
                self._send(200, {"artifact": "orders-v1", "rows": 2, "sha256": hashlib.sha256(b"orders-v1:2").hexdigest()})
            elif self.path == "/signer":
                self._send(404, {"error": "signature request returned not found"})
            else:
                self._send(404, {"error": "not found"})

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    args.ready_file and Path(args.ready_file).write_text(
        json.dumps({"url": f"http://127.0.0.1:{server.server_port}",
                    "agent_token": agent_token, "verifier_token": verifier_token,
                    "trial_id": args.trial_id}, sort_keys=True), encoding="utf-8"
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
