"""Verify daemon startup evidence and port cleanup after Agent shutdown."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent
SERVER_PATH = WORKSPACE / "server.py"
PORT = 8765


def _port_is_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.5)
        return client.connect_ex(("127.0.0.1", PORT)) == 0


def _load_trace() -> dict | None:
    trace_path = os.environ.get("EVAL_TRACE_PATH")
    if not trace_path:
        print("FAILED: evaluator did not provide the execution trace.", file=sys.stderr)
        return None
    try:
        return json.loads(Path(trace_path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAILED: execution trace is unavailable: {exc}", file=sys.stderr)
        return None


def _trace_tools(trace: dict) -> list[tuple[dict, dict]]:
    tools: list[tuple[dict, dict]] = []
    for turn in trace.get("turns", []):
        for tool in turn.get("tools", []):
            try:
                args = json.loads(tool.get("args_hash", "{}"))
            except json.JSONDecodeError:
                continue
            if isinstance(args, dict):
                tools.append((tool, args))
    return tools


def _background_server_pid(trace: dict) -> int | None:
    tools = _trace_tools(trace)
    for tool, args in tools:
        if (
            tool.get("tool_name") != "run_background"
            or tool.get("success") is not True
            or "server.py" not in str(args.get("command", "")).lower()
        ):
            continue
        try:
            payload = json.loads(str(tool.get("result_preview", "{}")))
            pid = int(payload["pid"])
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
        if pid > 0:
            return pid
    return None


def _has_successful_health_check(trace: dict) -> bool:
    return any(
        tool.get("tool_name") == "health_check"
        and tool.get("success") is True
        and "8765" in str(args.get("url", ""))
        and '"healthy": true' in str(tool.get("result_preview", "")).lower()
        for tool, args in tools
    )


def _cleanup_fixture_daemon(background_pid: int | None) -> None:
    """Stop only the process tree identified by this task's trace."""
    if background_pid is None:
        return

    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(background_pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
            )
        else:
            os.killpg(background_pid, signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        pass


def main() -> int:
    background_pid: int | None = None
    try:
        if not SERVER_PATH.is_file():
            print("FAILED: server.py is missing.", file=sys.stderr)
            return 1

        source = SERVER_PATH.read_text(encoding="utf-8")
        required_fragments = (
            'PORT = 8765',
            'HEALTH_BODY = b"MINI_CLAUDE_TASK018_OK"',
            'HTTPServer((HOST, PORT), HealthHandler).serve_forever()',
        )
        if any(fragment not in source for fragment in required_fragments):
            print("FAILED: server.py was modified to bypass the daemon lifecycle check.", file=sys.stderr)
            return 1

        trace = _load_trace()
        if trace is None:
            return 1
        background_pid = _background_server_pid(trace)
        if background_pid is None or not _has_successful_health_check(trace):
            print(
                "FAILED: trace lacks successful run_background startup and HTTP health-check evidence.",
                file=sys.stderr,
            )
            return 1

        # Agent shutdown has already returned before eval_runner invokes verify.py.
        # Poll briefly for asynchronous process-tree cleanup in a corrected runtime.
        deadline = time.monotonic() + 2.0
        while _port_is_open() and time.monotonic() < deadline:
            time.sleep(0.1)

        if _port_is_open():
            print(
                "FAILED: port 8765 remains occupied after Agent shutdown; the daemon was orphaned.",
                file=sys.stderr,
            )
            return 1

        print("SUCCESS: daemon health check completed and port 8765 was released on shutdown.")
        return 0
    finally:
        # A failed baseline must not contaminate subsequent repeated runs.
        _cleanup_fixture_daemon(background_pid)


if __name__ == "__main__":
    raise SystemExit(main())
