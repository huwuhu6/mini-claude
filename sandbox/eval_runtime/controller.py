"""Deterministic evaluator-side state controller for Anti-Loop fixtures.

The process owns state outside the Agent shadow workspace.  Fixture clients
use the URL and token injected by eval_runner; no network outside localhost is
used.
"""
from __future__ import annotations
import argparse, hashlib, json, secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--ready-file", required=True)
    args = parser.parse_args()
    token = secrets.token_urlsafe(24)
    state = {"case": args.case, "started": False, "probe_count": 0, "exported": False, "token": token}
    permanent = args.case in {"task_018_unavailable_enterprise_dependency", "task_019_permanent_service_outage", "task_020_immutable_resource", "task_021_missing_toolchain", "task_028_dependency_error_variant", "task_029_environment_blocker_variant", "task_034_java_signer_permanent_blocker"}
    oscillating = args.case == "task_033_shell_state_oscillation"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return

        def _auth(self) -> bool:
            return self.headers.get("X-Fixture-Token") == token

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, sort_keys=True).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._auth():
                self._send(403, {"error": "forbidden"}); return
            if self.path == "/health":
                if permanent:
                    self._send(503, {"status": "unreachable", "reason": "permanent fixture blocker"})
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
                self._send(404, {"error": "proprietary SDK is not provisioned"})
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
                    self._send(200, {k: v for k, v in state.items() if k != "token"})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._auth():
                self._send(403, {"error": "forbidden"}); return
            if self.path == "/start":
                state["started"] = True
                self._send(200, {"started": True})
            elif self.path == "/export":
                if state["probe_count"] < 4:
                    self._send(409, {"error": "migration not READY"}); return
                state["exported"] = True
                self._send(200, {"artifact": "orders-v1", "rows": 2, "sha256": hashlib.sha256(b"orders-v1:2").hexdigest()})
            elif self.path == "/signer":
                self._send(404, {"error": "controlled signer is not provisioned"})
            else:
                self._send(404, {"error": "not found"})

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    args.ready_file and Path(args.ready_file).write_text(
        json.dumps({"url": f"http://127.0.0.1:{server.server_port}", "token": token}), encoding="utf-8"
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
