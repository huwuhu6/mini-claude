from pathlib import Path

assert "DEFAULT_DISCOUNT = 0.05" in Path("active_policy.py").read_text(encoding="utf-8")
assert "discount=0.15" in Path("legacy_notes.md").read_text(encoding="utf-8")
actual = dict(line.split("=", 1) for line in Path("policy_summary.txt").read_text(encoding="utf-8").splitlines() if "=" in line)
assert actual == {"default_discount": "0.05"}, actual
