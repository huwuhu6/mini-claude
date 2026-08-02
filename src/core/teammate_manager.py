"""
Teammate Management System - Autonomous AI teammate lifecycle management.
"""
from __future__ import annotations
import logging
import json
import time
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field

from models.teammate import Teammate, TeammateStatus, TeammateRole

logger = logging.getLogger(__name__)


@dataclass
class TeammateConfig:
    directory: str = ".team"
    idle_timeout: int = 60
    auto_claim_tasks: bool = True
    poll_interval: int = 5


class TeammateManager:
    """Manages autonomous AI teammates with lifecycle control."""

    def __init__(self, config: TeammateConfig):
        self.config = config
        self._teammates: Dict[str, Teammate] = {}
        self._lock = threading.RLock()
        self._status_listeners: List[Callable[[str, TeammateStatus], None]] = []
        self._storage_dir = Path(config.directory)
        self._load_teammates()

    # ── CRUD ─────────────────────────────────────────────────

    def add(self, teammate: Teammate) -> Teammate:
        with self._lock:
            if teammate.id in self._teammates:
                raise ValueError(f"Teammate {teammate.id} already exists")
            self._teammates[teammate.id] = teammate
            self._save(teammate)
            logger.info(f"已添加队友: {teammate.name} ({teammate.id})")
            return teammate

    def create(self, name: str, role: TeammateRole = TeammateRole.WORKER,
               system_prompt: str = "", model: str = "",
               skills: Optional[List[str]] = None) -> Teammate:
        teammate = Teammate(
            name=name,
            role=role,
            system_prompt=system_prompt,
            model=model,
            skills=skills or [],
        )
        return self.add(teammate)

    def get(self, teammate_id: str) -> Optional[Teammate]:
        with self._lock:
            return self._teammates.get(teammate_id)

    def get_by_name(self, name: str) -> Optional[Teammate]:
        with self._lock:
            for t in self._teammates.values():
                if t.name == name:
                    return t
            return None

    def remove(self, teammate_id: str) -> bool:
        with self._lock:
            if teammate_id in self._teammates:
                del self._teammates[teammate_id]
                path = self._teammate_path(teammate_id)
                if path.exists():
                    path.unlink()
                logger.info(f"已移除队友: {teammate_id}")
                return True
            return False

    def list(self, status: Optional[TeammateStatus] = None) -> List[Teammate]:
        with self._lock:
            teammates = list(self._teammates.values())
        if status:
            teammates = [t for t in teammates if t.status == status]
        return sorted(teammates, key=lambda t: t.created_at)

    # ── Status Management ─────────────────────────────────────

    def set_status(self, teammate_id: str, status: TeammateStatus,
                   task_id: str = "") -> bool:
        with self._lock:
            teammate = self._teammates.get(teammate_id)
            if not teammate:
                return False
            old_status = teammate.status
            teammate.status = status
            teammate.last_active_at = time.time()
            if task_id:
                teammate.current_task_id = task_id
            elif status == TeammateStatus.IDLE:
                teammate.current_task_id = ""
            self._save(teammate)
        if old_status != status:
            self._notify_status(teammate_id, status)
        logger.debug(f"队友 {teammate_id} 状态: {old_status.value} -> {status.value}")
        return True

    def get_idle_teammates(self, max_idle_time: Optional[int] = None) -> List[Teammate]:
        timeout = max_idle_time or self.config.idle_timeout
        return [
            t for t in self.list()
            if t.status == TeammateStatus.IDLE and t.idle_time >= timeout
        ]

    # ── Task Claiming ─────────────────────────────────────────

    def claim_task(self, teammate_id: str, task_id: str) -> bool:
        """Assign a task to a teammate."""
        return self.set_status(teammate_id, TeammateStatus.WORKING, task_id)

    def release_task(self, teammate_id: str) -> bool:
        """Release teammate from their current task."""
        return self.set_status(teammate_id, TeammateStatus.IDLE)

    def get_available_worker(self, preferred_skills: Optional[List[str]] = None
                             ) -> Optional[Teammate]:
        """Find an idle teammate, optionally matching skills."""
        candidates = self.list(TeammateStatus.IDLE)
        if preferred_skills:
            for t in candidates:
                if any(s in t.skills for s in preferred_skills):
                    return t
        return candidates[0] if candidates else None

    # ── Lifecycle ─────────────────────────────────────────────

    def shutdown_all(self) -> None:
        with self._lock:
            for t in self._teammates.values():
                if t.status != TeammateStatus.SHUTDOWN:
                    t.status = TeammateStatus.SHUTDOWN
                    self._save(t)
            logger.info("所有队友已关闭")

    def cleanup_long_idle(self, max_idle_time: int = 300) -> int:
        """Shutdown teammates idle for too long. Returns count shut down."""
        count = 0
        for t in self.list(TeammateStatus.IDLE):
            if t.idle_time > max_idle_time:
                self.set_status(t.id, TeammateStatus.SHUTDOWN)
                count += 1
        return count

    # ── Listeners ─────────────────────────────────────────────

    def on_status_change(self, callback: Callable[[str, TeammateStatus], None]) -> None:
        self._status_listeners.append(callback)

    def _notify_status(self, teammate_id: str, status: TeammateStatus) -> None:
        for cb in self._status_listeners:
            try:
                cb(teammate_id, status)
            except Exception as e:
                logger.error(f"状态监听器失败: {e}")

    # ── Stats ─────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        teammates = self.list()
        return {
            'total': len(teammates),
            'by_status': {
                s.value: sum(1 for t in teammates if t.status == s)
                for s in TeammateStatus
            },
            'by_role': {
                r.value: sum(1 for t in teammates if t.role == r)
                for r in TeammateRole
            },
        }

    # ── Persistence ───────────────────────────────────────────

    def _teammate_path(self, teammate_id: str) -> Path:
        return self._storage_dir / f"teammate_{teammate_id}.json"

    def _save(self, teammate: Teammate) -> None:
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        path = self._teammate_path(teammate.id)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(teammate.to_dict(), f, indent=2, ensure_ascii=False)

    def _load_teammates(self) -> None:
        if not self._storage_dir.exists():
            return
        for path in self._storage_dir.glob("teammate_*.json"):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                teammate = Teammate.from_dict(data)
                self._teammates[teammate.id] = teammate
            except Exception as e:
                logger.error(f"从 {path} 加载队友失败: {e}")
