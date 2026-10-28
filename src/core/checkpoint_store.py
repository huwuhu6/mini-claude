"""SQLite checkpoints and execution facts; external effects are not transactional."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from core.run_snapshot import (BOUND_SNAPSHOT_VERSIONS, HITL_SNAPSHOT_VERSIONS,
    READABLE_SNAPSHOT_VERSIONS, SNAPSHOT_FIELDS, decode_messages, decode_run,
    decode_tool_result, same_tool_result, fields, json_copy, require, call_ids, PAUSED_RESULT_OUTCOMES)
from core.tool_outcome import validate_non_execution

DB_VERSION = 5
TERMINAL = {"completed", "terminated", "failed", "cancelled", "unknown"}


class CheckpointError(RuntimeError):
    """Persistence/control failure: never convert into ordinary tool feedback."""


class CheckpointCommitError(CheckpointError):
    def __init__(self, message, *, committed):
        super().__init__(message)
        self.committed = committed  # True / False / None, verified on a fresh connection.


class RunBusyError(CheckpointError):
    pass


class RunLock:
    """Kernel-held local lock, no time-based expiry. Never delete the lock file."""
    def __init__(self, path: Path):
        self.handle = path.open("a+b")
        self.thread_id = threading.get_ident()
        self.pid = os.getpid()
        try:
            if path.stat().st_size == 0:
                self.handle.write(b"0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.handle.close()
            raise

    def close(self):
        if self.handle.closed:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()


class SqliteCheckpointStore:
    """One current projection per run, CAS revisions and a small execution journal."""
    def __init__(self, path: Path, *, require_existing: bool = False):
        self.path = Path(path).expanduser().resolve()
        self._require_existing = require_existing
        if require_existing and not self.path.is_file():
            raise CheckpointError("Checkpoint 数据库不存在；恢复不能创建空数据库")
        if not require_existing:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_dir = self.path.parent / (self.path.name + ".locks")
        self.lock_dir.mkdir(exist_ok=True)
        self._locks: dict[str, RunLock] = {}
        self._closed = False
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            # Parent CHECK constraints require a transactional table rebuild.
            # Foreign keys are rechecked before commit; child rows are never rewritten.
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("BEGIN IMMEDIATE")
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if require_existing and version == 0:
                    raise CheckpointError("已有文件缺少 Checkpoint Schema；不能在恢复时初始化空库")
                if version not in {0, 1, 2, 3, 4, DB_VERSION}:
                    raise CheckpointError(f"Checkpoint 数据库版本不兼容: {version}")
                tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if version == 0 and tables:
                    raise CheckpointError("拒绝将未知数据库当作新的 Checkpoint Store")
                if version == 0:
                    connection.execute("""
                    CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, status TEXT NOT NULL CHECK(status IN
                    ('started','active','paused','completed','terminated','failed','cancelled','unknown')),
                    revision INTEGER NOT NULL CHECK(revision > 0),
                    payload TEXT, payload_hash TEXT, final_result TEXT,
                    last_operation TEXT NOT NULL, journal_hash TEXT NOT NULL DEFAULT '', boundary TEXT NOT NULL DEFAULT 'tools' CHECK(boundary IN ('tools','before_model','terminal')))
                    """)
                    connection.execute("""
                    CREATE TABLE IF NOT EXISTS calls (
                    run_id TEXT NOT NULL REFERENCES runs(run_id), turn INTEGER NOT NULL,
                    ordinal INTEGER NOT NULL, call_id TEXT NOT NULL, call_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN
                    ('PREPARED','RUNNING','COMPLETED','UNKNOWN','NOT_EXECUTED')),
                    result_json TEXT,
                    PRIMARY KEY(run_id,turn,ordinal), UNIQUE(run_id,turn,call_id))
                    """)
                expected = {"runs": {"run_id", "status", "revision", "payload", "payload_hash", "final_result", "last_operation", "boundary", "journal_hash"},
                            "calls": {"run_id", "turn", "ordinal", "call_id", "call_json", "state", "result_json"}}
                for table, columns in expected.items():
                    # Names are fixed schema constants, never user input.
                    actual = {r[1] for r in connection.execute("PRAGMA table_info(" + table + ")")}
                    if actual != columns:
                        raise CheckpointError("Checkpoint 数据库结构损坏或不兼容")
                if version < 2:
                    self._upgrade_approvals(connection)
                if version < 3:
                    self._upgrade_waiting(connection)
                if version < 4:
                    self._upgrade_conversations(connection)
                if version < 5:
                    connection.execute("""CREATE TABLE IF NOT EXISTS call_file_evidence (
                        run_id TEXT NOT NULL, turn INTEGER NOT NULL, ordinal INTEGER NOT NULL,
                        call_id TEXT NOT NULL, call_hash TEXT NOT NULL,
                        evidence_json TEXT NOT NULL, evidence_hash TEXT NOT NULL,
                        PRIMARY KEY(run_id,turn,ordinal),
                        FOREIGN KEY(run_id,turn,ordinal) REFERENCES calls(run_id,turn,ordinal))""")
                    connection.execute("PRAGMA user_version=5")
                if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise CheckpointError("Checkpoint 外键关系损坏")
                from core.approvals import APPROVAL_COLUMNS
                if {r[1] for r in connection.execute("PRAGMA table_info(approvals)")} != APPROVAL_COLUMNS:
                    raise CheckpointError("审批数据库结构损坏或不兼容")
                for table, columns in {
                    "conversations": {"conversation_id", "workspace", "title", "updated_at", "opened_at", "archived_at", "head_run_id"},
                    "conversation_runs": {"run_id", "conversation_id"},
                    "run_archives": {"run_id", "archived_at", "reason"},
                    "permission_grants": {"grant_id", "workspace", "fingerprint", "operation_json", "created_at"},
                    "approval_feedback": {"approval_id", "mode", "note"},
                    "call_file_evidence": {"run_id", "turn", "ordinal", "call_id", "call_hash", "evidence_json", "evidence_hash"},
                }.items():
                    if {r[1] for r in connection.execute("PRAGMA table_info(" + table + ")")} != columns:
                        raise CheckpointError("会话数据库结构损坏或不兼容")
                connection.commit()
                connection.execute("PRAGMA foreign_keys=ON")
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    @staticmethod
    def _upgrade_approvals(connection):
        from core.approvals import APPROVAL_DDL
        connection.execute(APPROVAL_DDL)
        connection.execute("PRAGMA user_version=2")

    @staticmethod
    def _upgrade_waiting(connection):
        connection.execute("""CREATE TABLE runs_upgrade (
            run_id TEXT PRIMARY KEY, status TEXT NOT NULL CHECK(status IN
            ('started','active','paused','waiting_approval','completed','terminated','failed','cancelled','unknown')),
            revision INTEGER NOT NULL CHECK(revision > 0), payload TEXT, payload_hash TEXT, final_result TEXT,
            last_operation TEXT NOT NULL, journal_hash TEXT NOT NULL DEFAULT '',
            boundary TEXT NOT NULL DEFAULT 'tools' CHECK(boundary IN ('tools','before_model','approval','terminal')))
        """)
        connection.execute("INSERT INTO runs_upgrade(run_id,status,revision,payload,payload_hash,final_result,last_operation,journal_hash,boundary) SELECT run_id,status,revision,payload,payload_hash,final_result,last_operation,journal_hash,boundary FROM runs")
        connection.execute("DROP TABLE runs")
        connection.execute("ALTER TABLE runs_upgrade RENAME TO runs")
        connection.execute("PRAGMA user_version=3")

    @contextmanager
    def _connection(self):
        if self._closed:
            raise CheckpointError("Checkpoint Store 已关闭")
        target = self.path.as_uri() + "?mode=rw" if self._require_existing else str(self.path)
        connection = sqlite3.connect(target, uri=self._require_existing, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _upgrade_conversations(connection):
        # History remains owned by the head Run snapshot. These tables contain
        # identity/index metadata only, never a second execution journal.
        connection.execute("""CREATE TABLE conversations (
            conversation_id TEXT PRIMARY KEY, workspace TEXT NOT NULL, title TEXT NOT NULL,
            updated_at REAL NOT NULL, opened_at REAL NOT NULL, archived_at REAL,
            head_run_id TEXT REFERENCES runs(run_id))""")
        connection.execute("""CREATE TABLE conversation_runs (
            run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
            conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id))""")
        connection.execute("""CREATE TABLE run_archives (
            run_id TEXT PRIMARY KEY REFERENCES runs(run_id), archived_at REAL NOT NULL, reason TEXT NOT NULL)""")
        connection.execute("""CREATE TABLE permission_grants (
            grant_id TEXT PRIMARY KEY, workspace TEXT NOT NULL, fingerprint TEXT NOT NULL,
            operation_json TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(workspace,fingerprint))""")
        connection.execute("""CREATE TABLE approval_feedback (
            approval_id TEXT PRIMARY KEY REFERENCES approvals(approval_id),
            mode TEXT NOT NULL CHECK(mode IN ('stop','replan')), note TEXT NOT NULL)""")
        # Legacy Runs remain inspectable through /runs; grouping them into a
        # conversation would invent an identity which their data never recorded.
        connection.execute("PRAGMA user_version=4")

    def conversation_for_run(self, run_id):
        with self._connection() as connection:
            row = connection.execute("SELECT conversation_id FROM conversation_runs WHERE run_id=?", (run_id,)).fetchone()
            return row[0] if row else None

    def get_conversation(self, conversation_id):
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM conversations WHERE conversation_id=?", (conversation_id,)).fetchone()
            if row is None:
                raise CheckpointError("会话不存在")
            return dict(row)

    def list_conversations(self, workspace, *, archived=False):
        with self._connection() as connection:
            return [dict(row) for row in connection.execute("""SELECT c.*, r.status FROM conversations c
                JOIN runs r ON r.run_id=c.head_run_id WHERE c.workspace=? AND
                (c.archived_at IS NOT NULL)=? ORDER BY MAX(c.updated_at,c.opened_at) DESC,c.rowid DESC""",
                (workspace, int(archived)))]

    def touch_conversation(self, conversation_id):
        self._owns("conversation:" + conversation_id)
        with self._connection() as connection:
            connection.execute("UPDATE conversations SET opened_at=? WHERE conversation_id=? AND archived_at IS NULL",
                               (time.time(), conversation_id))

    def archive_info(self, run_id):
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM run_archives WHERE run_id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def approval_feedback(self, approval_id):
        with self._connection() as connection:
            row = connection.execute("SELECT mode,note FROM approval_feedback WHERE approval_id=?", (approval_id,)).fetchone()
            return dict(row) if row else {"mode": "replan", "note": "用户拒绝了原操作"}

    def list_permission_grants(self, workspace):
        with self._connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM permission_grants WHERE workspace=? ORDER BY created_at", (workspace,))]

    def remember_permission(self, run_id, workspace, fingerprint, operation_json):
        def operation(connection, token):
            self._current(connection, run_id, connection.execute("SELECT revision FROM runs WHERE run_id=?", (run_id,)).fetchone()[0])
            connection.execute("INSERT OR IGNORE INTO permission_grants VALUES(?,?,?,?,?)",
                (uuid.uuid4().hex, workspace, fingerprint, operation_json, time.time()))
            connection.execute("UPDATE runs SET last_operation=? WHERE run_id=?", (token, run_id))
        self._write(run_id, operation)

    def revoke_permission(self, workspace, grant_id):
        with self._connection() as connection:
            return connection.execute("DELETE FROM permission_grants WHERE workspace=? AND grant_id=?",
                                      (workspace, grant_id)).rowcount == 1

    def archive_run(self, run_id, reason):
        require(type(reason) is str and bool(reason.strip()), "归档必须说明原因")
        record = self.load(run_id)
        def operation(connection, token):
            row = connection.execute("SELECT revision FROM runs WHERE run_id=?", (run_id,)).fetchone()
            require(row[0] == record["revision"], "归档期间执行状态已变化")
            connection.execute("INSERT OR IGNORE INTO run_archives VALUES(?,?,?)", (run_id, time.time(), reason))
            connection.execute("UPDATE conversations SET archived_at=? WHERE head_run_id=?", (time.time(), run_id))
            # Only metadata changes: UNKNOWN / PREPARED / approvals stay facts.
            connection.execute("UPDATE runs SET last_operation=? WHERE run_id=?", (token, run_id))
        self._write(run_id, operation)

    def acquire(self, run_id: str):
        if self._closed or run_id in self._locks:
            raise RunBusyError("本 Store 已持有该 Run 或已经关闭")
        name = hashlib.sha256(run_id.encode("utf-8")).hexdigest() + ".lock"
        try:
            lock = RunLock(self.lock_dir / name)
        except OSError as exc:
            raise RunBusyError("该 Run 已有有效执行者，不能并行继续") from exc
        self._locks[run_id] = lock

    def holds_lock(self, run_id: str) -> bool:
        lock = self._locks.get(run_id)
        return lock is not None and lock.thread_id == threading.get_ident() and lock.pid == os.getpid()

    def release(self, run_id: str):
        lock = self._locks.get(run_id)
        if lock is not None:
            if not self.holds_lock(run_id):
                raise RunBusyError("不能释放其他执行线程或进程的锁")
            self._locks.pop(run_id)
            lock.close()

    def close(self):
        for run_id in list(self._locks):
            self.release(run_id)
        self._closed = True

    def _owns(self, run_id):
        lock = self._locks.get(run_id)
        if lock is None or lock.thread_id != threading.get_ident() or lock.pid != os.getpid():
            raise RunBusyError("必须持有当前 Run 的进程锁才能推进状态")

    def _commit(self, connection):
        # A narrow seam for deterministic failure injection, including commit-then-error.
        connection.commit()

    def _write(self, run_id, operation):
        self._owns(run_id)
        token = uuid.uuid4().hex
        committing = False
        try:
            with self._connection() as connection:
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    result = operation(connection, token)
                    journal = [dict(r) for r in connection.execute(
                        "SELECT * FROM calls WHERE run_id=? ORDER BY turn,ordinal", (run_id,))]
                    journal_hash = hashlib.sha256(self._json(journal).encode("utf-8")).hexdigest()
                    connection.execute("UPDATE runs SET journal_hash=? WHERE run_id=?", (journal_hash, run_id))
                    connection.execute("UPDATE conversations SET updated_at=? WHERE head_run_id=?", (time.time(), run_id))
                    committing = True
                    self._commit(connection)
                    return result
                except BaseException:
                    if connection.in_transaction:
                        connection.rollback()
                    raise
        except Exception as exc:
            if not committing and not isinstance(exc, sqlite3.Error):
                if isinstance(exc, CheckpointError):
                    raise
                raise CheckpointError(str(exc)) from exc
            # COMMIT can have happened before an IO error was reported. Stop either
            # way; inspect durable facts using a different connection, never replay.
            committed = None
            try:
                with self._connection() as check:
                    row = check.execute("SELECT last_operation FROM runs WHERE run_id=?", (run_id,)).fetchone()
                    committed = row is not None and row[0] == token
            except sqlite3.Error:
                pass
            raise CheckpointCommitError(f"Checkpoint 提交失败，停止执行: {exc}", committed=committed) from exc

    @staticmethod
    def _json(data):
        return json.dumps(json_copy(data), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def validate_snapshot(data, run_id, *, terminal=False, boundary="tools"):
        linked = type(data) is dict and data.get("schema_version") in BOUND_SNAPSHOT_VERSIONS
        fields(data, SNAPSHOT_FIELDS | ({"checkpoint_binding"} if linked else set()), "Snapshot")
        require(type(data["schema_version"]) is int and data["schema_version"] in READABLE_SNAPSHOT_VERSIONS, "快照版本不兼容")
        if linked:
            fields(data["checkpoint_binding"], {"database", "run_id"}, "Checkpoint 归属")
            require(data["checkpoint_binding"]["run_id"] == run_id
                    and type(data["checkpoint_binding"]["database"]) is str, "Checkpoint 归属不一致")
        state = decode_run(data["run"], _legacy=data["schema_version"] < 7,
                           _legacy_effort=data["schema_version"] < 10, _terminal=terminal,
                           _pre_model=boundary == "before_model", _approval=boundary == "approval" and data["schema_version"] in HITL_SNAPSHOT_VERSIONS)
        require(state.run_id == run_id, "数据库 Run ID 与快照不一致")
        decode_messages(data["messages"], state, _pre_model=boundary == "before_model")
        return state

    def create_run(self, run_id, *, conversation_id=None, workspace=None, title=None, expected_head=None):
        def operation(connection, token):
            if conversation_id is not None:
                self._owns("conversation:" + conversation_id)
                prior = connection.execute("SELECT * FROM conversations WHERE conversation_id=?", (conversation_id,)).fetchone()
                if prior:
                    require(prior["workspace"] == workspace and prior["archived_at"] is None, "会话归属错误或已归档")
                    require(prior["head_run_id"] == expected_head, "会话已被其他进程推进，请重新打开")
                    last = connection.execute("SELECT status FROM runs WHERE run_id=?", (expected_head,)).fetchone()
                    require(last and last[0] in TERMINAL, "会话仍有未完成 Run")
                    require(not connection.execute("SELECT 1 FROM calls WHERE run_id=? AND state='UNKNOWN'", (expected_head,)).fetchone(), "会话存在 UNKNOWN；必须归档后开始新会话")
                else:
                    require(expected_head is None and isinstance(workspace, str) and isinstance(title, str), "新会话元数据无效")
                    connection.execute("INSERT INTO conversations VALUES(?,?,?,?,?,NULL,NULL)",
                                       (conversation_id, workspace, title, time.time(), time.time()))
            connection.execute("INSERT INTO runs(run_id,status,revision,payload,payload_hash,final_result,last_operation) VALUES(?, 'started', 1, NULL, NULL, NULL, ?)", (run_id, token))
            if conversation_id is not None:
                connection.execute("INSERT INTO conversation_runs VALUES(?,?)", (run_id, conversation_id))
                connection.execute("UPDATE conversations SET head_run_id=? WHERE conversation_id=?", (run_id, conversation_id))
            return 1
        return self._write(run_id, operation)

    @staticmethod
    def _current(connection, run_id, revision):
        if connection.execute("SELECT 1 FROM run_archives WHERE run_id=?", (run_id,)).fetchone():
            raise CheckpointError("Run 已归档，禁止继续原操作")
        row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None or row["revision"] != revision:
            raise CheckpointError("Checkpoint 版本已变化，拒绝旧执行者推进")
        if row["status"] in TERMINAL:
            raise CheckpointError("终结 Run 不允许回退或继续")
        return row

    def save_snapshot(self, run_id, revision, snapshot, *, paused=False, boundary="tools", _transaction=None):
        require(boundary in {"tools", "before_model", "approval"}, "Checkpoint 安全边界错误")
        state = self.validate_snapshot(json_copy(snapshot), run_id, boundary=boundary)
        require(snapshot.get("checkpoint_binding", {}).get("database", str(self.path)) == str(self.path), "Checkpoint 数据库归属冲突")
        payload = self._json(snapshot)
        digest = hashlib.sha256((boundary + "\n" + payload).encode("utf-8")).hexdigest()
        batch = state.tool_batch
        def operation(connection, token):
            row = self._current(connection, run_id, revision)
            prior = json.loads(row["payload"]) if row["payload"] else None
            if prior:
                old = prior["run"]["tool_batch"]
                require(state.model_turns_used >= prior["run"]["model_turns_used"], "模型轮次回退")
                for key in ("output_limit_retries", "output_limit_reserved_tokens"):
                    require(getattr(state, key) >= prior["run"].get(key, 0), "输出恢复预算回退")
                if old is not None and batch is not None and old["model_iteration"] == batch.model_iteration:
                    require(old["tool_calls"] == batch.tool_calls, "已提交调用批次不能修改")
                    require(batch.next_call_index >= old["next_call_index"], "批次游标回退")
                    require(all(k in snapshot["run"]["tool_batch"]["results"] and same_tool_result(snapshot["run"]["tool_batch"]["results"][k], v)
                                for k, v in old["results"].items()), "已提交结果不能修改")
                elif old is not None:
                    require(old["next_call_index"] == len(old["tool_calls"]) and old["finalized"], "旧批次未收尾")
            for ordinal, call in enumerate(batch.tool_calls if batch else []):
                existing = connection.execute("SELECT * FROM calls WHERE run_id=? AND turn=? AND ordinal=?",
                    (run_id, batch.model_iteration, ordinal)).fetchone()
                call_json = self._json(call)
                if existing is None:
                    require(ordinal >= batch.next_call_index, "不能凭快照导入未登记的已执行操作")
                    connection.execute("INSERT INTO calls VALUES(?,?,?,?,?,'PREPARED',NULL)",
                        (run_id, batch.model_iteration, ordinal, call["id"], call_json))
                    existing = {"state": "PREPARED", "result_json": None, "call_json": call_json}
                require(existing["call_json"] == call_json, "Journal 调用与 Snapshot 冲突")
                target = snapshot.get("workspace", {}).get("file_targets", {}).get(call["id"])
                if existing["state"] == "PREPARED" and target is not None:
                    # Refresh only while unstarted (e.g. after a preceding file
                    # edit). RUNNING/UNKNOWN evidence is immutable thereafter.
                    before = {k: v for k, v in target.items() if k not in {"expected_sha256", "verification_reason"}}
                    evidence = self._json({"version": 1, "before": before,
                        "expected_sha256": target.get("expected_sha256"),
                        "reason": target.get("verification_reason", "旧观察缺少预期内容"),
                        "tool_contract": snapshot["compatibility"].get("tools", {}).get(call["function"]["name"])})
                    connection.execute("""INSERT INTO call_file_evidence VALUES(?,?,?,?,?,?,?)
                        ON CONFLICT(run_id,turn,ordinal) DO UPDATE SET
                        evidence_json=excluded.evidence_json,evidence_hash=excluded.evidence_hash""",
                        (run_id, batch.model_iteration, ordinal, call["id"],
                         hashlib.sha256(call_json.encode("utf-8")).hexdigest(), evidence,
                         hashlib.sha256(evidence.encode("utf-8")).hexdigest()))
                result = batch.results.get(call["id"])
                if result is not None:
                    raw = snapshot["run"]["tool_batch"]["results"][call["id"]]
                    encoded = self._json(raw)
                    if existing["state"] in {"COMPLETED", "NOT_EXECUTED"}:
                        require(same_tool_result(json.loads(existing["result_json"]), raw), "Journal 已提交结果冲突")
                    else:
                        require(existing["state"] in {"RUNNING", "PREPARED"}, "未知操作不能自动完成")
                        not_executed = result.outcome not in {"EXECUTED_SUCCESS", "EXECUTED_FAILURE"}
                        if not_executed:
                            require(existing["state"] == "PREPARED", "已进入执行，不能记录 NOT_EXECUTED/NONE")
                            require(result.effect == "NONE", "未执行记录缺少正向证明")
                            validate_non_execution(result.outcome, result.non_execution_reason)
                        require(not_executed or existing["state"] == "RUNNING", "执行结果缺少 RUNNING 事实")
                        connection.execute("UPDATE calls SET state=?,result_json=? WHERE run_id=? AND turn=? AND ordinal=?",
                            ("NOT_EXECUTED" if not_executed else "COMPLETED", encoded, run_id, batch.model_iteration, ordinal))
                else:
                    require(existing["state"] == "PREPARED", "未提交结果的 RUNNING 不能写成安全暂停")
            if boundary == "before_model":
                require(not connection.execute("SELECT 1 FROM calls WHERE run_id=? AND state IN ('PREPARED','RUNNING','UNKNOWN')", (run_id,)).fetchone(), "模型边界仍有未终结执行")
            connection.execute("UPDATE runs SET status=?,revision=revision+1,payload=?,payload_hash=?,last_operation=?,boundary=? WHERE run_id=?",
                ("waiting_approval" if boundary == "approval" else "paused" if paused else "active", payload, digest, token, boundary, run_id))
            return revision + 1
        if _transaction is not None:
            self._owns(run_id)
            return operation(*_transaction)
        return self._write(run_id, operation)

    def _start_call(self, connection, token, run_id, revision, turn, ordinal, call_id, *, call=None, approval_id=None):
        row = self._current(connection, run_id, revision)
        require(row["payload"] is not None, "尚未提交完整工具批次")
        snapshot = json.loads(row["payload"])
        batch = snapshot["run"]["tool_batch"]
        require(batch is not None and batch["model_iteration"] == turn and batch["next_call_index"] == ordinal, "只能启动当前调用")
        require(call is None or batch["tool_calls"][ordinal] == call, "实际 dispatch 与已提交原调用冲突")
        approval = connection.execute("SELECT approval_id,state FROM approvals WHERE run_id=? AND turn=? AND ordinal=?", (run_id, turn, ordinal)).fetchone()
        require(approval is None or (approval["approval_id"] == approval_id and approval["state"] == "CONSUMED"), "存在审批记录，必须通过原子消费入口执行")
        changed = connection.execute("UPDATE calls SET state='RUNNING' WHERE run_id=? AND turn=? AND ordinal=? AND call_id=? AND state='PREPARED'",
            (run_id, turn, ordinal, call_id)).rowcount
        require(changed == 1, "调用不能重复启动")
        connection.execute("UPDATE runs SET status='active',revision=revision+1,last_operation=? WHERE run_id=?", (token, run_id))
        return revision + 1

    def mark_running(self, run_id, revision, turn, ordinal, call_id, *, call=None):
        return self._write(run_id, lambda connection, token: self._start_call(
            connection, token, run_id, revision, turn, ordinal, call_id, call=call))

    def list_run_ids(self):
        """Read-only discovery; callers still load/validate each selected Run."""
        with self._connection() as connection:
            return [row[0] for row in connection.execute("SELECT run_id FROM runs ORDER BY rowid DESC")]

    def load(self, run_id):
        with self._connection() as connection:
            connection.execute("BEGIN")  # Row and journal share one read snapshot under WAL.
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise CheckpointError("Checkpoint Run 不存在")
            result = dict(row)
            journal = [dict(r) for r in connection.execute("SELECT * FROM calls WHERE run_id=? ORDER BY turn,ordinal", (run_id,))]
            approval_rows = {r["approval_id"]: dict(r) for r in connection.execute("SELECT * FROM approvals WHERE run_id=?", (run_id,))}
            file_evidence = [dict(r) for r in connection.execute(
                "SELECT * FROM call_file_evidence WHERE run_id=? ORDER BY turn,ordinal", (run_id,))]
        try:
            require(hashlib.sha256(self._json(journal).encode("utf-8")).hexdigest() == result["journal_hash"], "执行日志校验摘要不符")
            journal_by_position = {(item["turn"], item["ordinal"]): item for item in journal}
            for entry in file_evidence:
                call = journal_by_position.get((entry["turn"], entry["ordinal"]))
                require(call is not None and call["call_id"] == entry["call_id"] and
                    hashlib.sha256(call["call_json"].encode("utf-8")).hexdigest() == entry["call_hash"], "文件核验证据调用归属冲突")
                require(hashlib.sha256(entry["evidence_json"].encode("utf-8")).hexdigest() == entry["evidence_hash"], "文件核验证据摘要不符")
                evidence = json.loads(entry["evidence_json"])
                fields(evidence, {"version", "before", "expected_sha256", "reason", "tool_contract"}, "文件核验证据")
                require(evidence["version"] == 1 and type(evidence["before"]) is dict and
                    type(evidence["reason"]) is str, "文件核验证据格式错误")
                expected = evidence["expected_sha256"]
                require(expected is None or (type(expected) is str and len(expected) == 64 and
                    all(c in "0123456789abcdef" for c in expected)), "文件预期摘要格式错误")
            snapshot = json.loads(result["payload"]) if result["payload"] else None
            if snapshot is not None:
                require(hashlib.sha256((result["boundary"] + "\n" + result["payload"]).encode("utf-8")).hexdigest() == result["payload_hash"], "快照校验摘要不符")
                require(snapshot.get("checkpoint_binding", {}).get("database", str(self.path)) == str(self.path), "Checkpoint 数据库归属冲突")
                state = self.validate_snapshot(snapshot, run_id, terminal=result["status"] in TERMINAL - {"unknown"}, boundary=result["boundary"])
                batch = state.tool_batch
                if batch is not None:
                    rows = [r for r in journal if r["turn"] == batch.model_iteration]
                    require(len(rows) == len(batch.tool_calls), "Journal 批次不完整")
                    for ordinal, item in enumerate(rows):
                        require(item["ordinal"] == ordinal and json.loads(item["call_json"]) == batch.tool_calls[ordinal]
                                and item["call_id"] == batch.tool_calls[ordinal]["id"], "Journal 调用身份冲突")
                        call_result = snapshot["run"]["tool_batch"]["results"].get(item["call_id"])
                        if ordinal < batch.next_call_index:
                            require(item["state"] in {"COMPLETED", "NOT_EXECUTED", "UNKNOWN"}
                                    and same_tool_result(json.loads(item["result_json"]), call_result), "结果、消息和游标版本冲突")
                        else:
                            require(item["result_json"] is None and item["state"] in {"PREPARED", "RUNNING", "UNKNOWN"}, "待执行状态冲突")
                            if item["state"] in {"RUNNING", "UNKNOWN"}:
                                require(ordinal == batch.next_call_index, "Journal 越过了批次游标")
            require((result["status"] in TERMINAL - {"unknown"}) == (result["boundary"] == "terminal"), "终结标记与 Checkpoint 边界冲突")
            if result["status"] in TERMINAL - {"unknown"}:
                require(snapshot["run"]["terminal_result"] == result["final_result"]
                        and snapshot["run"]["status"] == result["status"], "终结元数据与快照冲突")
            if result["boundary"] == "approval":
                require(snapshot["schema_version"] in HITL_SNAPSHOT_VERSIONS and state.status.value == "waiting_approval", "审批边界 / 快照状态冲突")
                opcall = batch.tool_calls[batch.next_call_index]
                approval_row = approval_rows.get(state.approval_id)
                from core.approvals import ApprovalRecord
                require(approval_row is not None, "等待 Run 缺少审批记录")
                approval = ApprovalRecord.from_dict(dict(approval_row))
                op = approval.decision.operation
                require((op.run_id, op.turn, op.ordinal, op.call) == (run_id, batch.model_iteration, batch.next_call_index, opcall), "审批与当前调用不一致")
                require(op.workspace == snapshot["workspace"]["root"] and op.cwd == snapshot["workspace"]["cwd"], "审批工作区冲突")
                context = json.loads(op.context_json)
                require(context["workspace_digest"] == snapshot["workspace"]["content_digest"], "审批工作区摘要冲突")
                from core.permissions import digest as permission_digest
                require(context["runtime_digest"] == permission_digest(snapshot["compatibility"]), "审批 Runtime 摘要冲突")
                current = journal[sum(r["turn"] < batch.model_iteration for r in journal) + batch.next_call_index]
                if approval.state.value == "CONSUMED":
                    require(current["state"] in {"RUNNING", "UNKNOWN"} and result["status"] in {"active", "unknown"}, "消费与执行事实冲突")
                else:
                    require(current["state"] == "PREPARED" and result["status"] == "waiting_approval", "等待审批调用已经执行")
            else:
                require(result["status"] != "waiting_approval", "等待状态缺少审批边界")
            require(result["revision"] > 0 and result["status"] in TERMINAL | {"started", "active", "paused", "waiting_approval"}, "Run 元数据错误")
            require(snapshot is not None or (result["status"] == "started" and not journal), "活动 Run 缺少一致快照")
            current_turn = snapshot["run"]["tool_batch"]["model_iteration"] if snapshot and snapshot["run"]["tool_batch"] else None
            for item in journal:
                call = json.loads(item["call_json"])
                require(call_ids([call]) == [item["call_id"]], "Journal 原调用 ID 损坏")
                require(item["state"] in {"PREPARED", "RUNNING", "COMPLETED", "UNKNOWN", "NOT_EXECUTED"}, "Journal 状态损坏")
                if item["result_json"] is not None:
                    fact = json.loads(item["result_json"])
                    decode_tool_result(fact)
                    require(fact["call_id"] == item["call_id"] and type(fact["content"]) is str, "Journal 结果关联损坏")
                    expected = ("COMPLETED" if fact["outcome"] in {"EXECUTED_SUCCESS", "EXECUTED_FAILURE", "RECORDED_BEFORE_TERMINAL"}
                                else "UNKNOWN" if fact["outcome"] in {"UNKNOWN", "CANCELLED"} else "NOT_EXECUTED")
                    require(type(fact["outcome"]) is str and fact["outcome"] in PAUSED_RESULT_OUTCOMES | {"UNKNOWN", "CANCELLED", "NOT_EXECUTED", "RECORDED_BEFORE_TERMINAL"}
                            and item["state"] == expected, "Journal 执行状态与结果冲突")
                else:
                    require(item["state"] in {"PREPARED", "RUNNING", "UNKNOWN"}, "Journal 已终结但缺少结果")
                require(type(item["turn"]) is int and item["turn"] >= 0 and item["ordinal"] >= 0, "Journal 索引损坏")
                if item["turn"] != current_turn:
                    require(item["state"] in {"COMPLETED", "NOT_EXECUTED"} and item["result_json"] is not None, "历史批次存在未终结调用")
                if result["status"] == "completed":
                    require(item["state"] in {"COMPLETED", "NOT_EXECUTED"}, "完成 Run 仍含 UNKNOWN")
            require(result["status"] != "unknown" or any(r["state"] == "UNKNOWN" for r in journal), "UNKNOWN Run 缺少执行事实")
        except (ValueError, TypeError, KeyError) as exc:
            raise CheckpointError(f"Checkpoint 数据损坏: {exc}") from exc
        result["snapshot"] = snapshot
        result["journal"] = journal
        result["file_evidence"] = file_evidence
        return result

    def recover(self, run_id):
        self._owns(run_id)
        record = self.load(run_id)
        if any(r["state"] == "RUNNING" for r in record["journal"]):
            def operation(connection, token):
                self._current(connection, run_id, record["revision"])
                connection.execute("UPDATE calls SET state='UNKNOWN' WHERE run_id=? AND state='RUNNING'", (run_id,))
                connection.execute("UPDATE runs SET status='unknown',revision=revision+1,last_operation=? WHERE run_id=?", (token, run_id))
            self._write(run_id, operation)
            record = self.load(run_id)
        return record

    def finish(self, run_id, revision, status, result, projection):
        require(status in TERMINAL - {"unknown"}, "Run 终结状态无效")
        state = self.validate_snapshot(projection, run_id, terminal=True)
        require(projection.get("checkpoint_binding", {}).get("database", str(self.path)) == str(self.path), "Checkpoint 数据库归属冲突")
        require(state.status.value == status and state.terminal_result == result, "终结结果与状态不一致")
        payload = self._json(projection)
        digest = hashlib.sha256(("terminal\n" + payload).encode("utf-8")).hexdigest()
        def operation(connection, token):
            self._current(connection, run_id, revision)
            if state.tool_batch is not None:
                batch = state.tool_batch
                for ordinal, call in enumerate(batch.tool_calls):
                    row = connection.execute("SELECT * FROM calls WHERE run_id=? AND turn=? AND ordinal=?",
                        (run_id, batch.model_iteration, ordinal)).fetchone()
                    require(row is not None and json.loads(row["call_json"]) == call, "终结批次未登记")
                    raw = projection["run"]["tool_batch"]["results"][call["id"]]
                    encoded = self._json(raw)
                    outcome = raw["outcome"]
                    if row["state"] in {"COMPLETED", "NOT_EXECUTED"}:
                        require(same_tool_result(json.loads(row["result_json"]), raw), "终结修改了已提交结果")
                        continue
                    known_execution = outcome in {"EXECUTED_SUCCESS", "EXECUTED_FAILURE", "RECORDED_BEFORE_TERMINAL"}
                    unknown = outcome in {"UNKNOWN", "CANCELLED"}
                    require(not known_execution or row["state"] == "RUNNING", "终结执行结果缺少 RUNNING")
                    require(not unknown or row["state"] == "RUNNING", "终结 UNKNOWN 缺少 RUNNING")
                    if not known_execution and not unknown:
                        fact = batch.results[call["id"]]
                        require(row["state"] == "PREPARED", "已进入执行，不能终结为 NOT_EXECUTED/NONE")
                        require(fact.effect == "NONE", "终结未执行记录缺少正向证明")
                        validate_non_execution(fact.outcome, fact.non_execution_reason)
                    connection.execute("UPDATE calls SET state=?,result_json=? WHERE run_id=? AND turn=? AND ordinal=?",
                        ("UNKNOWN" if unknown else "COMPLETED" if known_execution else "NOT_EXECUTED",
                         encoded, run_id, batch.model_iteration, ordinal))
            connection.execute("UPDATE runs SET status=?,revision=revision+1,payload=?,payload_hash=?,final_result=?,last_operation=?,boundary='terminal' WHERE run_id=?",
                (status, payload, digest, result, token, run_id))
            return revision + 1
        return self._write(run_id, operation)
