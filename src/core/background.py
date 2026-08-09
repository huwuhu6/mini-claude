"""
Background Processing System - Async command execution with notifications.
"""
from __future__ import annotations
import copy
import logging
import queue
import threading
import time
import subprocess
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable
from enum import Enum

logger = logging.getLogger(__name__)


class BackgroundTaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class BackgroundTask:
    id: str = ""
    description: str = ""
    status: BackgroundTaskStatus = BackgroundTaskStatus.PENDING
    command: str = ""
    result: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    timeout: int = 120
    cwd: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class BackgroundProcessor:
    """Manages asynchronous command execution."""

    def __init__(self, max_concurrent: int = 5, notification_queue_size: int = 1000):
        self.max_concurrent = max_concurrent
        self._tasks: Dict[str, BackgroundTask] = {}
        self._queue: queue.Queue = queue.Queue()
        self._notification_queue: queue.Queue = queue.Queue(maxsize=notification_queue_size)
        self._lock = threading.Lock()
        self._running = False
        self._workers: List[threading.Thread] = []
        self._notifier: Optional[threading.Thread] = None
        self._completion_callbacks: List[Callable[[str], None]] = []

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        for i in range(self.max_concurrent):
            t = threading.Thread(target=self._worker_loop, name=f"bg-worker-{i}", daemon=True)
            t.start()
            self._workers.append(t)
        self._notifier = threading.Thread(target=self._notifier_loop, name="bg-notifier", daemon=True)
        self._notifier.start()
        logger.info(f"后台处理器已启动（{self.max_concurrent} 个工作线程）")

    def stop(self) -> None:
        self._running = False
        logger.info("后台处理器已停止")

    # ── Task Submission ───────────────────────────────────────

    def run(self, command: str, description: str = "", timeout: int = 120,
            cwd: Optional[str] = None, **metadata) -> str:
        task_id = str(uuid.uuid4())[:8]
        task = BackgroundTask(
            id=task_id,
            description=description or command[:50],
            command=command,
            timeout=timeout,
            cwd=cwd,
            metadata=metadata,
        )
        with self._lock:
            self._tasks[task_id] = task
        self._queue.put(task_id)
        logger.info(f"后台任务 {task_id} 已加入队列: {description}")
        return task_id

    def get(self, task_id: str) -> Optional[BackgroundTask]:
        with self._lock:
            task = self._tasks.get(task_id)
            return copy.deepcopy(task) if task else None

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == BackgroundTaskStatus.PENDING:
                task.status = BackgroundTaskStatus.CANCELLED
                return True
            return False

    def list(self, status: Optional[BackgroundTaskStatus] = None) -> List[BackgroundTask]:
        with self._lock:
            tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks

    def on_complete(self, callback: Callable[[str], None]) -> None:
        self._completion_callbacks.append(callback)

    def check_notifications(self) -> List[str]:
        """Get and clear completed task notification IDs."""
        notifications = []
        while not self._notification_queue.empty():
            try:
                notifications.append(self._notification_queue.get_nowait())
            except queue.Empty:
                break
        return notifications

    # ── Worker ────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        while self._running:
            try:
                task_id = self._queue.get(timeout=1)
            except queue.Empty:
                continue

            # Get the *real* shared task object for status mutation
            with self._lock:
                task = self._tasks.get(task_id)
            if not task or task.status == BackgroundTaskStatus.CANCELLED:
                continue

            with self._lock:
                task.status = BackgroundTaskStatus.RUNNING
            try:
                # Binary capture — same encoding fix as base_tools.py run_bash
                r = subprocess.run(
                    task.command,
                    shell=True,
                    capture_output=True,
                    timeout=task.timeout,
                    cwd=task.cwd,
                )
                raw_output = r.stdout + r.stderr
                try:
                    output_str = raw_output.decode('utf-8')
                except UnicodeDecodeError:
                    output_str = raw_output.decode('gbk', errors='replace')
                output = output_str.strip()

                with self._lock:
                    if r.returncode == 0:
                        task.status = BackgroundTaskStatus.COMPLETED
                        task.result = output[:10000]
                    else:
                        task.status = BackgroundTaskStatus.FAILED
                        task.error = output[:5000]
                    task.completed_at = time.time()
            except subprocess.TimeoutExpired:
                with self._lock:
                    task.status = BackgroundTaskStatus.FAILED
                    task.error = f"Timeout after {task.timeout}s"
                    task.completed_at = time.time()
            except Exception as e:
                with self._lock:
                    task.status = BackgroundTaskStatus.FAILED
                    task.error = str(e)
                    task.completed_at = time.time()

            # Notify (lock-free — queue.Queue is thread-safe)
            self._notification_queue.put(task.id)
            for cb in self._completion_callbacks:
                try:
                    cb(task.id)
                except Exception as e:
                    logger.error(f"完成回调失败: {e}")

            # ── GC: prune old completed/failed tasks to prevent unbounded growth ──
            self._prune_old_tasks()

    def _notifier_loop(self) -> None:
        while self._running:
            time.sleep(0.5)
            # Notifications handled via check_notifications

    def _prune_old_tasks(self, max_retained: int = 1000) -> int:
        """Remove oldest terminal tasks beyond *max_retained* to prevent memory leak."""
        terminal = (BackgroundTaskStatus.COMPLETED, BackgroundTaskStatus.FAILED,
                     BackgroundTaskStatus.CANCELLED)
        with self._lock:
            terminal_ids = [
                tid for tid, t in self._tasks.items()
                if t.status in terminal
            ]
            excess = len(terminal_ids) - max_retained
            if excess > 0:
                # Sort by completed_at (oldest first); None sorts before float
                terminal_ids.sort(
                    key=lambda tid: self._tasks[tid].completed_at or 0
                )
                for tid in terminal_ids[:excess]:
                    del self._tasks[tid]
                logger.debug(f"后台 GC: 清理了 {excess} 条过期任务记录")
            return max(excess, 0)

    # ── Stats ─────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        tasks = self.list()
        return {
            'total': len(tasks),
            'queued': self._queue.qsize(),
            'by_status': {
                s.value: sum(1 for t in tasks if t.status == s)
                for s in BackgroundTaskStatus
            },
        }
