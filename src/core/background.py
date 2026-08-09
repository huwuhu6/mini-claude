"""
Background Processing System - Async command execution with notifications.
"""
from __future__ import annotations
import copy
import logging
import os
import queue
import signal
import tempfile
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
    KILLED = "killed"


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
    pid: Optional[int] = None
    started_at: Optional[float] = None
    exit_code: Optional[int] = None
    stdout_file: Optional[str] = None
    stderr_file: Optional[str] = None
    stop_requested: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


class BackgroundProcessor:
    """Manages asynchronous command execution."""

    def __init__(self, max_concurrent: int = 5, notification_queue_size: int = 1000,
                 output_dir: Optional[Path] = None):
        self.max_concurrent = max_concurrent
        self._tasks: Dict[str, BackgroundTask] = {}
        self._notification_queue = queue.Queue(maxsize=notification_queue_size)
        self._lock = threading.Lock()
        self._running = False
        self._processes: Dict[str, subprocess.Popen] = {}
        self._completion_callbacks: List[Callable[[str], None]] = []
        self.output_dir = Path(output_dir) if output_dir else (
            Path(tempfile.gettempdir()) / "mini-claude-background"
        )

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.output_dir.mkdir(parents=True, exist_ok=True)
        logger.info("后台处理器已启动")

    # ── Task Submission ───────────────────────────────────────

    def run(self, command: str, description: str = "", timeout: int = 120,
            cwd: Optional[str] = None, **metadata) -> str:
        return self.launch(
            command, description=description, timeout=timeout,
            cwd=cwd, **metadata,
        ).id

    def launch(self, command: str, description: str = "", timeout: int = 120,
               cwd: Optional[str] = None, **metadata) -> BackgroundTask:
        """Start a detached process and return its initial task snapshot."""
        task_id = str(uuid.uuid4())[:8]
        task = BackgroundTask(
            id=task_id,
            description=description or command[:50],
            command=command,
            timeout=timeout,
            cwd=cwd,
            metadata=metadata,
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stdout_file = self.output_dir / f"{task_id}.stdout.log"
        stderr_file = self.output_dir / f"{task_id}.stderr.log"
        task.stdout_file = str(stdout_file)
        task.stderr_file = str(stderr_file)

        try:
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            with stdout_file.open("ab") as stdout, stderr_file.open("ab") as stderr:
                process = subprocess.Popen(
                    command,
                    shell=True,
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    creationflags=creationflags,
                    start_new_session=(os.name != "nt"),
                )
        except Exception as exc:
            task.status = BackgroundTaskStatus.FAILED
            task.error = str(exc)
            task.completed_at = time.time()
            with self._lock:
                self._tasks[task_id] = task
            return task

        task.status = BackgroundTaskStatus.RUNNING
        task.pid = process.pid
        task.started_at = time.time()
        with self._lock:
            self._tasks[task_id] = task
            self._processes[task_id] = process
        monitor = threading.Thread(
            target=self._monitor_process,
            args=(task_id, process),
            name=f"bg-monitor-{task_id}",
            daemon=True,
        )
        monitor.start()
        logger.info("后台任务 %s 已启动 pid=%s: %s", task_id, process.pid, description)
        return copy.deepcopy(task)

    def get(self, task_id: str) -> Optional[BackgroundTask]:
        with self._lock:
            task = self._tasks.get(task_id)
            return copy.deepcopy(task) if task else None

    def cancel(self, task_id: str) -> bool:
        """Cancel a queued task. Running tasks use stop()."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == BackgroundTaskStatus.PENDING:
                task.status = BackgroundTaskStatus.CANCELLED
                return True
            return False

    def stop(self, task_id: Optional[str] = None) -> bool:
        """Stop accepting work, or explicitly stop one process tree.

        Calling without a task id only stops the manager. It deliberately
        does not terminate existing child processes.
        """
        if task_id is None:
            self._running = False
            logger.info("后台处理器已停止（保留已启动的外部进程）")
            return True
        with self._lock:
            task = self._tasks.get(task_id)
            process = self._processes.get(task_id)
            if not task or task.status != BackgroundTaskStatus.RUNNING or not process:
                return False
            task.stop_requested = True

        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                )
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except Exception as exc:
            logger.warning("停止后台任务失败 %s: %s", task_id, exc)
            return False
        return True

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

    # ── Process monitoring ───────────────────────────────────

    def _monitor_process(self, task_id: str, process: subprocess.Popen) -> None:
        try:
            exit_code = process.wait()
            with self._lock:
                task = self._tasks.get(task_id)
                if not task:
                    return
                task.exit_code = exit_code
                task.completed_at = time.time()
                if task.stop_requested:
                    task.status = BackgroundTaskStatus.KILLED
                elif exit_code == 0:
                    task.status = BackgroundTaskStatus.COMPLETED
                else:
                    task.status = BackgroundTaskStatus.FAILED
                    task.error = self._read_tail(task.stderr_file, 50)
                task.result = self._read_tail(task.stdout_file, 50)
                self._processes.pop(task_id, None)
            try:
                self._notification_queue.put_nowait(task_id)
            except queue.Full:
                logger.warning("后台任务通知队列已满，丢弃通知: %s", task_id)
            for callback in self._completion_callbacks:
                try:
                    callback(task_id)
                except Exception as exc:
                    logger.error("完成回调失败: %s", exc)
            self._prune_old_tasks()
        except Exception:
            logger.exception("后台任务监控失败: %s", task_id)

    @staticmethod
    def _read_tail(path: Optional[str], lines: int) -> str:
        if not path:
            return ""
        try:
            raw = Path(path).read_bytes()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("gbk", errors="replace")
            return "\n".join(text.splitlines()[-max(1, lines):])[-10000:]
        except OSError:
            return ""

    def logs(self, task_id: str, tail: int = 50) -> Optional[Dict[str, Any]]:
        task = self.get(task_id)
        if not task:
            return None
        return {
            "job_id": task.id,
            "stdout": self._read_tail(task.stdout_file, tail),
            "stderr": self._read_tail(task.stderr_file, tail),
            "tail": tail,
        }

    def _prune_old_tasks(self, max_retained: int = 1000) -> int:
        """Remove oldest terminal tasks beyond *max_retained* to prevent memory leak."""
        terminal = (BackgroundTaskStatus.COMPLETED, BackgroundTaskStatus.FAILED,
                    BackgroundTaskStatus.CANCELLED, BackgroundTaskStatus.KILLED)
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
            'queued': sum(1 for task in tasks if task.status == BackgroundTaskStatus.PENDING),
            'by_status': {
                s.value: sum(1 for t in tasks if t.status == s)
                for s in BackgroundTaskStatus
            },
        }
