"""Bounded, session-scoped notes kept outside the user's workspace."""

from __future__ import annotations

import uuid
from pathlib import Path


class AgentNote:
    MAX_CHARS = 6000

    def __init__(self, data_root: Path):
        self.path = Path(data_root) / "notes" / f"{uuid.uuid4().hex}.md"

    def read(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8")[:self.MAX_CHARS]
        except FileNotFoundError:
            return ""

    def replace(self, content: str) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return "已更新 Agent 笔记。"
