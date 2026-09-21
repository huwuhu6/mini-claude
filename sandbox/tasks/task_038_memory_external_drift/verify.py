import json
from pathlib import Path

settings = json.loads(Path("settings.json").read_text(encoding="utf-8"))
assert settings == {"environment": "staging", "generation": 2}, settings
actual = dict(line.split("=", 1) for line in Path("runtime_report.txt").read_text(encoding="utf-8").splitlines() if "=" in line)
assert actual == {"environment": "staging", "generation": "2"}, actual
assert 'settings["generation"] = 2' in Path("refresh_settings.py").read_text(encoding="utf-8")
