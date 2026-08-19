"""Validate the authentication deployment stage before a local health check."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


EXPECTED_STAGE = "staging"
RECEIPT_FILE = Path(".auth_stage_check.json")


def main() -> int:
    stage = os.environ.get("AUTH_STAGE")
    if stage != EXPECTED_STAGE:
        print(
            f"Authentication stage is invalid: expected {EXPECTED_STAGE!r}, got {stage!r}.",
            file=sys.stderr,
        )
        return 1

    RECEIPT_FILE.write_text(
        json.dumps({"auth_stage": stage}, sort_keys=True), encoding="utf-8"
    )
    print(f"Authentication stage validated: {stage}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
