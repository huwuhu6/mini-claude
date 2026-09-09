"""Local health endpoint used by the daemon lifecycle benchmark."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer


HOST = "127.0.0.1"
PORT = 8765
HEALTH_BODY = b"MINI_CLAUDE_TASK018_OK"


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(HEALTH_BODY)))
            self.end_headers()
            self.wfile.write(HEALTH_BODY)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


if __name__ == "__main__":
    print(f"Task 018 server listening on {HOST}:{PORT}", flush=True)
    HTTPServer((HOST, PORT), HealthHandler).serve_forever()
