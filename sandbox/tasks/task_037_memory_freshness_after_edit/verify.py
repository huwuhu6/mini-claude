import json
from pathlib import Path

actual = json.loads(Path("audit_summary.json").read_text(encoding="utf-8"))
assert actual == {
    "scope": "regional",
    "minimum_approvers": 2,
    "catalog_owners": {"accounts": "identity", "orders": "commerce", "search": "discovery"},
}, actual
