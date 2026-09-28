import json
from pathlib import Path

settings = json.loads(Path("settings.json").read_text(encoding="utf-8"))
assert settings == {"environment": "staging", "generation": 2}, settings
actual = json.loads(Path("runtime_report.json").read_text(encoding="utf-8"))
assert actual == {
    "environment": "staging",
    "generation": 2,
    "owner_teams": {"api": "platform", "jobs": "operations"},
}, actual
