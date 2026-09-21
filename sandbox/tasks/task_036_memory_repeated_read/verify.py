import json
from pathlib import Path

actual = json.loads(Path("release_manifest.json").read_text(encoding="utf-8"))
assert actual == {
    "channel": "canary",
    "rollback_limit": 2,
    "components": {"api": "platform", "billing": "payments", "worker": "operations"},
}, actual
