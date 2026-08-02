"""
Task data model with status management and dependency support.
"""
from __future__ import annotations
import json
import time
import uuid
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from pathlib import Path


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"

    def is_terminal(self) -> bool:
        return self in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)


@dataclass
class Task:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str = ""
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    assignee: str = ""
    priority: int = 1  # 1=low, 2=medium, 3=high
    blocked_by: List[str] = field(default_factory=list)  # task IDs this depends on
    depends_on: List[str] = field(default_factory=list)   # aliases for blocked_by
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Sync depends_on -> blocked_by
        if self.depends_on and not self.blocked_by:
            self.blocked_by = self.depends_on

    @property
    def age(self) -> float:
        return time.time() - self.created_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'title': self.title,
            'description': self.description,
            'status': self.status.value,
            'assignee': self.assignee,
            'priority': self.priority,
            'blocked_by': self.blocked_by,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'completed_at': self.completed_at,
            'metadata': self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Task':
        data = data.copy()
        data['status'] = TaskStatus(data.get('status', 'pending'))
        return cls(**data)

    def to_short_string(self) -> str:
        status_icon = {
            TaskStatus.PENDING: '⏳',
            TaskStatus.RUNNING: '▶️',
            TaskStatus.COMPLETED: '✅',
            TaskStatus.FAILED: '❌',
            TaskStatus.BLOCKED: '🔒',
            TaskStatus.CANCELLED: '🚫',
        }.get(self.status, '📋')
        blocks = f" (blocked by: {', '.join(self.blocked_by)})" if self.blocked_by else ""
        return f"{status_icon} [{self.id}] {self.title} - {self.status.value}{blocks}"


class TaskManager:
    """Persistent file-based task management with dependency resolution."""

    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir

    def _task_path(self, task_id: str) -> Path:
        return self.storage_dir / f"task_{task_id}.json"

    # ── CRUD ─────────────────────────────────────────────────

    def create(self, task: Task) -> Task:
        # Resolve blocking tasks
        for bid in task.blocked_by:
            blocker = self.get(bid)
            if blocker and blocker.status != TaskStatus.COMPLETED:
                task.status = TaskStatus.BLOCKED
                break
        self._save(task)
        return task

    def get(self, task_id: str) -> Optional[Task]:
        path = self._task_path(task_id)
        if not path.exists():
            return None
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return Task.from_dict(json.load(f))
        except Exception:
            return None

    def update(self, task: Task) -> bool:
        task.updated_at = time.time()
        if task.status == TaskStatus.COMPLETED and task.completed_at is None:
            task.completed_at = time.time()
        self._save(task)
        # Unblock dependent tasks if now completed
        if task.status == TaskStatus.COMPLETED:
            self._resolve_blocked_tasks(task.id)
        return True

    def delete(self, task_id: str) -> bool:
        path = self._task_path(task_id)
        if path.exists():
            path.unlink()
            return True
        return False

    # ── Listing ───────────────────────────────────────────────

    def list(self, status: Optional[TaskStatus] = None, assignee: Optional[str] = None) -> List[Task]:
        tasks = []
        for path in sorted(self.storage_dir.glob("task_*.json")):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    task = Task.from_dict(json.load(f))
                if status and task.status != status:
                    continue
                if assignee and task.assignee != assignee:
                    continue
                tasks.append(task)
            except Exception:
                continue
        return tasks

    def list_by_priority(self, limit: int = 10) -> List[Task]:
        tasks = self.list()
        tasks.sort(key=lambda t: (-t.priority, t.created_at))
        return tasks[:limit]

    def count(self) -> Dict[str, int]:
        counts = {}
        for task in self.list():
            key = task.status.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    # ── Dependency Management ─────────────────────────────────

    def get_blocked_tasks(self, task_id: str) -> List[Task]:
        return [t for t in self.list() if task_id in t.blocked_by]

    def add_blocked_by(self, task_id: str, blocker_id: str) -> bool:
        task = self.get(task_id)
        if not task:
            return False
        if blocker_id not in task.blocked_by:
            task.blocked_by.append(blocker_id)
        # Check if blocker is not completed -> set BLOCKED
        blocker = self.get(blocker_id)
        if blocker and blocker.status != TaskStatus.COMPLETED:
            if task.status not in (TaskStatus.BLOCKED, TaskStatus.COMPLETED):
                task.status = TaskStatus.BLOCKED
        self._save(task)
        return True

    def _resolve_blocked_tasks(self, completed_task_id: str) -> None:
        for task in self.list():
            if task.status == TaskStatus.BLOCKED and completed_task_id in task.blocked_by:
                # Check if all blockers are resolved
                all_done = True
                for bid in task.blocked_by:
                    blocker = self.get(bid)
                    if blocker and blocker.status != TaskStatus.COMPLETED:
                        all_done = False
                        break
                if all_done:
                    task.status = TaskStatus.PENDING
                    self._save(task)

    # ── Status transitions ────────────────────────────────────

    def start(self, task_id: str) -> bool:
        task = self.get(task_id)
        if not task or task.status != TaskStatus.PENDING:
            return False
        task.status = TaskStatus.RUNNING
        return self.update(task)

    def complete(self, task_id: str) -> bool:
        task = self.get(task_id)
        if not task:
            return False
        task.status = TaskStatus.COMPLETED
        return self.update(task)

    def fail(self, task_id: str) -> bool:
        task = self.get(task_id)
        if not task:
            return False
        task.status = TaskStatus.FAILED
        return self.update(task)

    # ── Persistence helper ────────────────────────────────────

    def _save(self, task: Task) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        path = self._task_path(task.id)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(task.to_dict(), f, indent=2, ensure_ascii=False)
