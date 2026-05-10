"""
Message Bus System - Inter-agent communication with broadcast support.
"""
from __future__ import annotations
import logging
import json
import time
import uuid
import threading
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable
from pathlib import Path

logger = logging.getLogger(__name__)


class MessagePriority(Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


class MessageType(Enum):
    DIRECT = "direct"
    BROADCAST = "broadcast"
    SYSTEM = "system"
    TASK_UPDATE = "task_update"
    TEAMMATE_STATUS = "teammate_status"


@dataclass
class Message:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    msg_type: MessageType = MessageType.DIRECT
    sender: str = ""
    recipient: str = ""  # empty for broadcast
    content: str = ""
    priority: MessagePriority = MessagePriority.NORMAL
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'msg_type': self.msg_type.value,
            'sender': self.sender,
            'recipient': self.recipient,
            'content': self.content,
            'priority': self.priority.value,
            'timestamp': self.timestamp,
            'metadata': self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Message':
        return cls(
            id=data.get('id', str(uuid.uuid4())),
            msg_type=MessageType(data.get('msg_type', 'direct')),
            sender=data.get('sender', ''),
            recipient=data.get('recipient', ''),
            content=data.get('content', ''),
            priority=MessagePriority(data.get('priority', 1)),
            timestamp=data.get('timestamp', time.time()),
            metadata=data.get('metadata', {}),
        )


class MessageBus:
    """Inter-agent message bus with persistence and broadcast."""

    def __init__(self, storage_dir: Optional[Path] = None):
        self._inboxes: Dict[str, List[Message]] = {}
        self._subscribers: Dict[str, List[Callable[[Message], None]]] = {}
        self._storage_dir = storage_dir
        self._lock = threading.Lock()
        if storage_dir:
            self._storage_dir.mkdir(parents=True, exist_ok=True)

    # ── Sending ───────────────────────────────────────────────

    def send(self, message: Message) -> str:
        """Send a direct message to a recipient's inbox."""
        with self._lock:
            if message.recipient not in self._inboxes:
                self._inboxes[message.recipient] = []
            self._inboxes[message.recipient].append(message)
            self._notify_subscribers(message.recipient, message)
            self._persist_message(message)
        logger.debug(f"消息已发送: {message.id} -> {message.recipient}")
        return message.id

    def broadcast(self, message: Message, targets: Optional[List[str]] = None) -> List[str]:
        """Broadcast a message to all or specified recipients."""
        message.msg_type = MessageType.BROADCAST
        with self._lock:
            recipients = targets or list(self._inboxes.keys())
        sent_ids = []
        for recipient in recipients:
            msg = Message(
                msg_type=MessageType.BROADCAST,
                sender=message.sender,
                recipient=recipient,
                content=message.content,
                priority=message.priority,
                metadata=message.metadata,
            )
            sent_ids.append(self.send(msg))
        return sent_ids

    # ── Receiving ─────────────────────────────────────────────

    def read_inbox(self, recipient: str, mark_read: bool = True) -> List[Message]:
        """Read all messages in a recipient's inbox and delete persisted copies."""
        with self._lock:
            messages = list(self._inboxes.get(recipient, []))
            if mark_read and messages:
                self._inboxes[recipient] = []
                # Delete persisted JSON files to prevent disk leak
                if self._storage_dir:
                    for msg in messages:
                        fpath = self._storage_dir / f"{msg.id}.json"
                        try:
                            fpath.unlink(missing_ok=True)
                        except OSError:
                            pass
        return messages

    def has_messages(self, recipient: str) -> bool:
        return len(self._inboxes.get(recipient, [])) > 0

    def get_inbox_size(self, recipient: str) -> int:
        return len(self._inboxes.get(recipient, []))

    def clear_inbox(self, recipient: str) -> None:
        self._inboxes[recipient] = []

    # ── Subscriptions ─────────────────────────────────────────

    def subscribe(self, recipient: str, callback: Callable[[Message], None]) -> None:
        with self._lock:
            if recipient not in self._subscribers:
                self._subscribers[recipient] = []
            self._subscribers[recipient].append(callback)

    def unsubscribe(self, recipient: str, callback: Callable) -> None:
        if recipient in self._subscribers:
            self._subscribers[recipient] = [
                cb for cb in self._subscribers[recipient] if cb != callback
            ]

    def _notify_subscribers(self, recipient: str, message: Message) -> None:
        for cb in self._subscribers.get(recipient, []):
            try:
                cb(message)
            except Exception as e:
                logger.error(f"订阅者通知失败: {e}")

    # ── Persistence ───────────────────────────────────────────

    def _persist_message(self, message: Message) -> None:
        if not self._storage_dir:
            return
        try:
            file_path = self._storage_dir / f"{message.id}.json"
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(message.to_dict(), f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"持久化消息失败: {e}")

    def load_persisted_messages(self, recipient: str) -> int:
        """Load persisted messages into inbox. Returns count loaded."""
        if not self._storage_dir or not self._storage_dir.exists():
            return 0
        count = 0
        for fpath in self._storage_dir.glob("*.json"):
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                msg = Message.from_dict(data)
                if msg.recipient == recipient or not msg.recipient:
                    if recipient not in self._inboxes:
                        self._inboxes[recipient] = []
                    self._inboxes[recipient].append(msg)
                    count += 1
            except Exception as e:
                logger.error(f"加载消息 {fpath} 失败: {e}")
        return count

    # ── System Messages ───────────────────────────────────────

    def send_system_message(self, content: str, targets: Optional[List[str]] = None) -> None:
        msg = Message(
            msg_type=MessageType.SYSTEM,
            sender="system",
            content=content,
            priority=MessagePriority.HIGH,
        )
        if targets:
            for t in targets:
                msg.recipient = t
                self.send(msg)
        else:
            self.broadcast(msg)

    # ── Stats ─────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        return {
            'total_inboxes': len(self._inboxes),
            'total_messages': sum(len(v) for v in self._inboxes.values()),
            'active_subscribers': sum(len(v) for v in self._subscribers.values()),
            'storage_dir': str(self._storage_dir) if self._storage_dir else None,
        }
