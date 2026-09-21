import json
from pathlib import Path

actual = json.loads(Path("release_manifest.json").read_text(encoding="utf-8"))
assert actual.get("channel") == "canary", actual
assert actual.get("rollback_limit") == 2, actual
components = actual.get("components", {})
owners = {
    name: value.get("owner") if isinstance(value, dict) else value
    for name, value in components.items()
}
assert owners == {
    "api": "platform", "billing": "payments", "worker": "operations",
    "events": "platform", "ledger": "payments", "mailer": "operations", "search": "discovery",
}, actual
