import json
from pathlib import Path

actual = json.loads(Path("audit_summary.json").read_text(encoding="utf-8"))
audit = actual.get("audit", actual)
assert audit.get("scope") == "regional", actual
assert audit.get("minimum_approvers") == 2, actual
owners = actual.get("catalog_owners")
if owners is None:
    owners = {
        item.get("catalog"): item.get("owner")
        for item in actual.get("catalogs", [])
        if isinstance(item, dict)
    }
assert owners == {
    "accounts": "identity", "orders": "commerce", "search": "discovery",
    "events": "platform", "ledger": "payments", "mail": "operations", "profiles": "identity",
}, actual
