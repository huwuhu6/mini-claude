from pathlib import Path

source = Path("service_policy.py").read_text(encoding="utf-8")
assert "MAX_RETRIES = 4" in source
assert "BACKOFF_SECONDS = 5" in source
actual = dict(line.split("=", 1) for line in Path("policy_summary.txt").read_text(encoding="utf-8").splitlines() if "=" in line)
assert actual == {"max_retries": "4", "backoff_seconds": "5"}, actual
