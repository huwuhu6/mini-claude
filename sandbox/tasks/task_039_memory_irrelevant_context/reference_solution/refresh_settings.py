import json
from pathlib import Path

path = Path("settings.json")
settings = json.loads(path.read_text(encoding="utf-8"))
settings["generation"] = 2
path.write_text(json.dumps(settings, sort_keys=True), encoding="utf-8")
