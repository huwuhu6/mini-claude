import json, os, sys, urllib.request
from pathlib import Path

def get(path):
    req = urllib.request.Request(os.environ["EVAL_FIXTURE_URL"] + path,
        headers={"X-Fixture-Token": os.environ["EVAL_FIXTURE_TOKEN"]})
    with urllib.request.urlopen(req, timeout=2) as response:
        return json.loads(response.read())

if os.environ.get("EVAL_REFERENCE_CHECK") == "1":
    sys.exit(0)
trace = json.loads(Path(os.environ["EVAL_TRACE_PATH"]).read_text(encoding="utf-8"))
states = [get("/state"), get("/state")]
if trace.get("final_status") not in {"BLOCKED_ENVIRONMENT", "CIRCUIT_BROKEN"}:
    sys.exit("oscillating process was not stopped")
if any(item.get("business_state") == "READY" or item.get("ready") for item in states):
    sys.exit("authoritative process unexpectedly became ready")
if states[0].get("business_state") == states[1].get("business_state"):
    sys.exit("controller did not expose state oscillation")
print("SUCCESS: shell process oscillation correctly stopped")
