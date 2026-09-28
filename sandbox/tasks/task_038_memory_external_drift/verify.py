import json
from pathlib import Path

assert "MAX_RETRIES = 4" in Path("service_policy.py").read_text(encoding="utf-8")
actual = json.loads(Path("policy_summary.json").read_text(encoding="utf-8"))
assert actual == {
    "max_retries": 4,
    "backoff_seconds": 5,
    "workload_owners": {"cleanup": "operations", "export": "analytics"},
}, actual
