"""
Teammate data model for autonomous AI team members.
"""
from __future__ import annotations
import json
import time
import uuid
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from pathlib import Path


class TeammateStatus(Enum):
    IDLE = "idle"
    WORKING = "working"
    BUSY = "busy"
    ERROR = "error"
    SHUTDOWN = "shutdown"


class TeammateRole(Enum):
    WORKER = "worker"
    RESEARCHER = "researcher"
    CODER = "coder"
    REVIEWER = "reviewer"
    SPECIALIST = "specialist"


@dataclass
class Teammate:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    role: TeammateRole = TeammateRole.WORKER
    status: TeammateStatus = TeammateStatus.IDLE
    system_prompt: str = ""
    model: str = ""
    current_task_id: str = ""
    skills: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)

    @property
    def idle_time(self) -> float:
        return time.time() - self.last_active_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'role': self.role.value,
            'status': self.status.value,
            'system_prompt': self.system_prompt,
            'model': self.model,
            'current_task_id': self.current_task_id,
            'skills': self.skills,
            'created_at': self.created_at,
            'last_active_at': self.last_active_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Teammate':
        data = data.copy()
        data['role'] = TeammateRole(data.get('role', 'worker'))
        data['status'] = TeammateStatus(data.get('status', 'idle'))
        return cls(**data)

    def to_short_string(self) -> str:
        icons = {
            TeammateStatus.IDLE: '💤',
            TeammateStatus.WORKING: '⚙️',
            TeammateStatus.BUSY: '🔥',
            TeammateStatus.ERROR: '💥',
            TeammateStatus.SHUTDOWN: '💀',
        }
        icon = icons.get(self.status, '❓')
        task_info = f" on [{self.current_task_id}]" if self.current_task_id else ""
        return f"{icon} {self.name} ({self.role.value}) - {self.status.value}{task_info}"
