"""
TodoManager — Lightweight in-memory task tracking for the agent.

Used by the ``TodoWrite`` tool and the Nag reminder system.
Max 20 items, only one ``in_progress`` at a time.
"""

from __future__ import annotations
from typing import List, Dict, Any


class TodoManager:
    def __init__(self):
        self.items: List[Dict[str, str]] = []

    # ── Update & Validation ────────────────────────────────────

    def update(self, items: list) -> str:
        validated: List[Dict[str, str]] = []
        in_progress_count = 0

        for i, item in enumerate(items):
            content = str(item.get("content", "")).strip()
            status = str(item.get("status", "pending")).lower()
            af = str(item.get("activeForm", "")).strip()

            if not content:
                raise ValueError(f"Item {i}: content required")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Item {i}: invalid status '{status}'")
            if not af:
                raise ValueError(f"Item {i}: activeForm required")
            if status == "in_progress":
                in_progress_count += 1

            validated.append({
                "content": content,
                "status": status,
                "activeForm": af,
            })

        if len(validated) > 20:
            raise ValueError("Max 20 todos")
        if in_progress_count > 1:
            raise ValueError("Only one in_progress allowed")

        self.items = validated
        return self.render()

    # ── Rendering ───────────────────────────────────────────────

    def render(self) -> str:
        if not self.items:
            return "No todos."

        icons = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]"}
        lines: List[str] = []
        for item in self.items:
            icon = icons.get(item["status"], "[?]")
            suffix = ""
            if item["status"] == "in_progress":
                suffix = f" <- {item['activeForm']}"
            lines.append(f"{icon} {item['content']}{suffix}")

        done = sum(1 for t in self.items if t["status"] == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)

    # ── Queries ─────────────────────────────────────────────────

    def has_open_items(self) -> bool:
        return any(item.get("status") != "completed" for item in self.items)
