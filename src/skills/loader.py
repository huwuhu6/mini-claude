"""
Skills System - Loadable specialized knowledge modules.
"""
from __future__ import annotations
import logging
import os
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    name: str
    description: str = ""
    content: str = ""
    category: str = "general"
    trigger_keywords: List[str] = field(default_factory=list)
    file_path: Optional[str] = None
    version: str = "1.0"

    def matches(self, text: str) -> bool:
        """Check if this skill is relevant to the given text."""
        text_lower = text.lower()
        for kw in self.trigger_keywords:
            if kw.lower() in text_lower:
                return True
        return False


class SkillLoader:
    """Discovers, loads, and manages skill modules."""

    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self._skills: Dict[str, Skill] = {}
        self._cache: Dict[str, str] = {}

    def discover(self) -> List[str]:
        """Scan skills directory for both shallow .md and nested SKILL.md packages."""
        if not self.skills_dir.exists():
            logger.warning(f"Skills directory not found: {self.skills_dir}")
            return []

        names: List[str] = []
        seen: set = set()

        # Phase 1: Shallow — *.md directly in skills/ (name = stem)
        for fpath in self.skills_dir.glob("*.md"):
            name = fpath.stem
            if name not in self._skills:
                self._load_skill_file(fpath)
            if name not in seen:
                seen.add(name)
                names.append(name)

        # Phase 2: Deep — **/SKILL.md in subdirectories (name from frontmatter or parent dir)
        for fpath in self.skills_dir.rglob("SKILL.md"):
            if fpath.parent == self.skills_dir:
                continue  # root-level SKILL.md already handled by shallow glob
            skill = self._load_skill_file(fpath)
            if skill and skill.name not in seen:
                seen.add(skill.name)
                names.append(skill.name)

        # Phase 3: Shallow *.yaml
        for fpath in self.skills_dir.glob("*.yaml"):
            name = fpath.stem
            if name not in self._skills:
                self._load_skill_file(fpath)
            if name not in seen:
                seen.add(name)
                names.append(name)

        logger.info(f"Discovered {len(names)} skills in {self.skills_dir}")
        return names

    def load(self, name: str) -> Optional[Skill]:
        """Load a specific skill by name (searches shallow then deep)."""
        if name in self._skills:
            return self._skills[name]

        # Try shallow: skills/{name}.md / .yaml / .yml
        for ext in ['.md', '.yaml', '.yml']:
            fpath = self.skills_dir / f"{name}{ext}"
            if fpath.exists():
                return self._load_skill_file(fpath)

        # Try deep: skills/{name}/SKILL.md
        fpath = self.skills_dir / name / "SKILL.md"
        if fpath.exists():
            return self._load_skill_file(fpath)

        logger.warning(f"Skill not found: {name}")
        return None

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def get_all(self) -> List[Skill]:
        return list(self._skills.values())

    def descriptions(self) -> str:
        """Return a formatted list of available skills for the system prompt."""
        if not self._skills:
            return "(没有可用技能模块)"
        lines = []
        for name, skill in sorted(self._skills.items()):
            desc = skill.description or '-'
            lines.append(f"  - {name}: {desc}")
        return '\n'.join(lines)

    def find_matching(self, text: str) -> List[Skill]:
        """Find skills matching the given text."""
        return [s for s in self._skills.values() if s.matches(text)]

    def get_skill_content(self, name: str) -> Optional[str]:
        """Get the full content of a skill."""
        if name in self._cache:
            return self._cache[name]
        skill = self.load(name)
        if skill:
            self._cache[name] = skill.content
            return skill.content
        return None

    def refresh(self) -> int:
        """Reload all skills. Returns count loaded."""
        self._skills.clear()
        self._cache.clear()
        return len(self.discover())

    def _load_skill_file(self, fpath: Path) -> Optional[Skill]:
        """Parse a skill file, extracting frontmatter metadata and body content.

        Name resolution priority:
        1. Frontmatter ``name`` field
        2. For ``SKILL.md`` in a subdirectory: the parent folder name (e.g. ``pdf``)
        3. Fallback: file stem (e.g. ``git-cheatsheet``)
        """
        try:
            content = fpath.read_text(encoding='utf-8')

            # ── Parse YAML/Meta frontmatter ──────────────────────
            metadata: Dict[str, Any] = {}
            body = content

            if content.startswith('---'):
                parts = content.split('---', 2)
                if len(parts) >= 3:
                    frontmatter = parts[1].strip()
                    body = parts[2].strip()
                    for line in frontmatter.split('\n'):
                        line = line.strip()
                        if ':' in line:
                            key, _, value = line.partition(':')
                            key = key.strip().lower()
                            value = value.strip()
                            metadata[key] = value

            # ── Determine skill name ──────────────────────────────
            name = metadata.get('name', '')
            if not name:
                if fpath.name == 'SKILL.md' and fpath.parent != self.skills_dir:
                    name = fpath.parent.name  # nested package → folder name
                else:
                    name = fpath.stem  # shallow file → file stem

            # ── Parse trigger_keywords ────────────────────────────
            trigger_kws = metadata.get('triggers', metadata.get('trigger_keywords', ''))
            if isinstance(trigger_kws, str):
                trigger_kws = [kw.strip() for kw in trigger_kws.split(',') if kw.strip()]

            skill = Skill(
                name=name,
                description=metadata.get('description', ''),
                content=body,
                category=metadata.get('category', 'general'),
                trigger_keywords=trigger_kws if isinstance(trigger_kws, list) else [],
                file_path=str(fpath),
                version=metadata.get('version', '1.0'),
            )

            self._skills[name] = skill
            logger.debug(f"Loaded skill: {name} ({skill.category}) from {fpath}")
            return skill

        except Exception as e:
            logger.error(f"Failed to load skill file {fpath}: {e}")
            return None
