"""Approval facts in the existing checkpoint DB; no handler or Agent integration."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
import time
import uuid

from core.checkpoint_store import CheckpointError
from core.permissions import PermissionDecision, PermissionKind, canonical, digest
from core.run_snapshot import fields, require

APPROVAL_COLUMNS = {"approval_id", "run_id", "turn", "ordinal", "binding_digest", "decision_json",
    "state", "created_at", "expires_at", "user_decision", "decided_by", "decided_at", "consumed_at", "invalidation_reason"}
APPROVAL_DDL = """CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, turn INTEGER NOT NULL, ordinal INTEGER NOT NULL,
    binding_digest TEXT NOT NULL, decision_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('PENDING','APPROVED','REJECTED','INVALIDATED','CONSUMED')),
    created_at REAL NOT NULL, expires_at REAL, user_decision TEXT CHECK(user_decision IN ('APPROVED','REJECTED')),
    decided_by TEXT, decided_at REAL, consumed_at REAL, invalidation_reason TEXT,
    UNIQUE(run_id,turn,ordinal),
    FOREIGN KEY(run_id,turn,ordinal) REFERENCES calls(run_id,turn,ordinal))"""


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    INVALIDATED = "INVALIDATED"
    CONSUMED = "CONSUMED"


def timestamp(value):
    require(type(value) in {float, int} and math.isfinite(value) and value >= 0, "审批时间无效")
    return float(value)


@dataclass(frozen=True)
class ApprovalRecord:
    data_json: str

    @classmethod
    def from_dict(cls, raw):
        data = json.loads(canonical(raw))
        fields(data, APPROVAL_COLUMNS, "审批记录")
        decision = PermissionDecision.from_dict(json.loads(data["decision_json"]))
        op = decision.operation
        require(decision.kind is PermissionKind.ASK, "审批只能来自 ASK 决策")
        require(data["binding_digest"] == op.binding_digest and
                (data["run_id"], data["turn"], data["ordinal"]) == (op.run_id, op.turn, op.ordinal), "审批操作归属冲突")
        require(type(data["approval_id"]) is str and bool(data["approval_id"]), "审批 ID 无效")
        state = ApprovalStatus(data["state"])
        created = timestamp(data["created_at"])
        require(data["expires_at"] is None or timestamp(data["expires_at"]) > created, "审批有效期无效")
        verdict = data["user_decision"]
        if verdict is None:
            require(data["decided_at"] is None and data["decided_by"] is None and state in {ApprovalStatus.PENDING, ApprovalStatus.INVALIDATED}, "审批缺少用户决定")
        else:
            require(verdict in {"APPROVED", "REJECTED"} and type(data["decided_by"]) is str and bool(data["decided_by"])
                    and timestamp(data["decided_at"]) >= created, "审批决定元数据错误")
            require((verdict == "APPROVED" and state in {ApprovalStatus.APPROVED, ApprovalStatus.INVALIDATED, ApprovalStatus.CONSUMED})
                    or (verdict == "REJECTED" and state is ApprovalStatus.REJECTED), "审批状态与用户决定冲突")
        require((data["consumed_at"] is not None) == (state is ApprovalStatus.CONSUMED), "消费标记冲突")
        if data["consumed_at"] is not None:
            require(timestamp(data["consumed_at"]) >= timestamp(data["decided_at"]), "消费时间回退")
        require((data["invalidation_reason"] is not None) == (state is ApprovalStatus.INVALIDATED), "失效原因冲突")
        return cls(canonical(data))

    def to_dict(self):
        return json.loads(self.data_json)

    @property
    def decision(self):
        return PermissionDecision.from_dict(json.loads(self.to_dict()["decision_json"]))

    @property
    def approval_id(self):
        return self.to_dict()["approval_id"]

    @property
    def state(self):
        return ApprovalStatus(self.to_dict()["state"])


class ApprovalStore:
    """All mutations require the checkpoint Run lock; decisions never dispatch.

    Changed/expired requests are terminal INVALIDATED. A fresh plan/call is
    needed; this first contract does not recycle approval IDs or grants.
    """
    def __init__(self, checkpoints, policy, *, clock=time.time):
        self.checkpoints = checkpoints
        self.policy = policy
        self.clock = clock

    def list_for_run(self, run_id):
        with self.checkpoints._connection() as connection:
            rows = connection.execute("SELECT * FROM approvals WHERE run_id=? ORDER BY turn,ordinal", (run_id,)).fetchall()
        return [ApprovalRecord.from_dict(dict(row)) for row in rows]

    def get(self, approval_id):
        with self.checkpoints._connection() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
        if row is None:
            raise CheckpointError("审批不存在")
        try:
            return ApprovalRecord.from_dict(dict(row))
        except (ValueError, TypeError, KeyError) as exc:
            raise CheckpointError("审批数据损坏") from exc

    @staticmethod
    def _touch(connection, run_id, token, *, changed=True):
        connection.execute("UPDATE runs SET revision=revision+?,last_operation=? WHERE run_id=?", (int(changed), token, run_id))

    def _prepared(self, connection, operation, revision):
        run = self.checkpoints._current(connection, operation.run_id, revision)
        row = connection.execute("SELECT * FROM calls WHERE run_id=? AND turn=? AND ordinal=?",
            (operation.run_id, operation.turn, operation.ordinal)).fetchone()
        require(row is not None and row["state"] == "PREPARED" and row["result_json"] is None, "审批只接受未执行 PREPARED，不能重试 RUNNING / UNKNOWN")
        require(row["call_json"] == operation.call_json and row["call_id"] == operation.provider_call_id, "审批原调用身份 / 参数冲突")
        snapshot = json.loads(run["payload"])
        batch = snapshot["run"]["tool_batch"]
        require(batch is not None and batch["model_iteration"] == operation.turn and batch["next_call_index"] == operation.ordinal, "审批必须指向当前调用")
        require(snapshot["workspace"]["root"] == operation.workspace and snapshot["workspace"]["cwd"] == operation.cwd, "审批工作区 / cwd 不匹配")
        context = json.loads(operation.context_json)
        require(context["workspace_digest"] == snapshot["workspace"]["content_digest"]
                and context["runtime_digest"] == digest(snapshot["compatibility"]), "审批执行上下文与 Checkpoint 不匹配")
        tools = snapshot["compatibility"].get("tools")
        require(tools is None or tools.get(operation.tool_name) == context["tool_contract"], "审批工具契约冲突")

    def create(self, decision, revision, *, expires_at=None, _transaction=None):
        require(isinstance(decision, PermissionDecision) and decision.kind is PermissionKind.ASK, "只为 ASK 创建审批")
        op = decision.operation
        require(self.policy.evaluate(op) == decision, "当前策略拒绝 / 决策已变化，不能创建审批")
        self.checkpoints.load(op.run_id)
        now = timestamp(self.clock())
        require(expires_at is None or timestamp(expires_at) > now, "审批过期时间必须在未来")
        def operation(connection, token):
            self._prepared(connection, op, revision)
            existing = connection.execute("SELECT * FROM approvals WHERE run_id=? AND turn=? AND ordinal=?", (op.run_id, op.turn, op.ordinal)).fetchone()
            if existing:
                require(existing["binding_digest"] == op.binding_digest and existing["decision_json"] == canonical(decision.to_dict())
                        and existing["expires_at"] == expires_at, "同一调用审批请求冲突")
                self._touch(connection, op.run_id, token, changed=False)
                return ApprovalRecord.from_dict(dict(existing))
            data = dict(approval_id=uuid.uuid4().hex, run_id=op.run_id, turn=op.turn, ordinal=op.ordinal,
                binding_digest=op.binding_digest, decision_json=canonical(decision.to_dict()), state="PENDING",
                created_at=now, expires_at=expires_at, user_decision=None, decided_by=None,
                decided_at=None, consumed_at=None, invalidation_reason=None)
            connection.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(data.values()))
            self._touch(connection, op.run_id, token)
            return ApprovalRecord.from_dict(data)
        if _transaction is not None:
            return operation(*_transaction)
        return self.checkpoints._write(op.run_id, operation)

    def request_wait(self, decision, revision, snapshot, *, expires_at=None):
        """One lock/transaction publishes request and its WAITING projection."""
        from core.run_snapshot import json_copy, HITL_SNAPSHOT_VERSIONS
        data = json_copy(snapshot)
        require(data["schema_version"] in HITL_SNAPSHOT_VERSIONS and data["run"]["status"] == "waiting_approval", "等待投影契约错误")
        op = decision.operation
        def operation(connection, token):
            approval = self.create(decision, revision, expires_at=expires_at, _transaction=(connection, token))
            data["run"]["approval_id"] = approval.approval_id
            # create advances the Run revision, within this same uncommitted transaction.
            current_revision = connection.execute("SELECT revision FROM runs WHERE run_id=?", (op.run_id,)).fetchone()[0]
            updated = self.checkpoints.save_snapshot(op.run_id, current_revision, data,
                boundary="approval", _transaction=(connection, token))
            return approval, updated
        return self.checkpoints._write(op.run_id, operation)

    def _invalidate(self, connection, data, token, reason):
        require(data["state"] in {"PENDING", "APPROVED"}, "终结审批不能失效或重新激活")
        connection.execute("UPDATE approvals SET state='INVALIDATED',invalidation_reason=? WHERE approval_id=? AND state IN ('PENDING','APPROVED')", (reason, data["approval_id"]))
        self._touch(connection, data["run_id"], token)

    def invalidate(self, approval_id, reason):
        require(type(reason) is str and bool(reason), "失效原因不能为空")
        record = self.get(approval_id)
        def operation(connection, token):
            data = dict(connection.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone())
            self.checkpoints._current(connection, data["run_id"], connection.execute("SELECT revision FROM runs WHERE run_id=?", (data["run_id"],)).fetchone()[0])
            self._invalidate(connection, data, token, reason)
        self.checkpoints._write(record.decision.operation.run_id, operation)
        return self.get(approval_id)

    def decide(self, approval_id, *, approved, actor, feedback=None):
        require(type(approved) is bool and type(actor) is str and bool(actor), "用户决定 / 身份无效")
        if feedback is not None:
            require(not approved and type(feedback) is dict and set(feedback) == {"mode", "note"}
                    and feedback["mode"] in {"stop", "replan"} and type(feedback["note"]) is str
                    and 0 < len(feedback["note"]) <= 4000, "拒绝意见无效")
        record = self.get(approval_id)
        run_id = record.decision.operation.run_id
        self.checkpoints.load(run_id)
        now = timestamp(self.clock())
        verdict = "APPROVED" if approved else "REJECTED"
        def operation(connection, token):
            data = dict(connection.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone())
            self.checkpoints._current(connection, run_id, connection.execute("SELECT revision FROM runs WHERE run_id=?", (run_id,)).fetchone()[0])
            ApprovalRecord.from_dict(data)
            if data["state"] in {"PENDING", "APPROVED"} and data["expires_at"] is not None and now >= data["expires_at"]:
                self._invalidate(connection, data, token, "审批已过期")
                return False
            require(now >= data["created_at"] and (data["decided_at"] is None or now >= data["decided_at"]), "审批时钟回退")
            if data["state"] != "PENDING":
                require(data["user_decision"] == verdict and data["decided_by"] == actor and data["state"] in {verdict, "CONSUMED"}, "审批决定冲突或已失效")
                prior = connection.execute("SELECT mode,note FROM approval_feedback WHERE approval_id=?", (approval_id,)).fetchone()
                require((dict(prior) if prior else None) == feedback, "不能修改已记录的拒绝意见")
                self._touch(connection, run_id, token, changed=False)
                return True
            connection.execute("UPDATE approvals SET state=?,user_decision=?,decided_by=?,decided_at=? WHERE approval_id=? AND state='PENDING'", (verdict, verdict, actor, now, approval_id))
            if feedback is not None:
                connection.execute("INSERT INTO approval_feedback VALUES(?,?,?)", (approval_id, feedback["mode"], feedback["note"]))
            self._touch(connection, run_id, token)
            return True
        if not self.checkpoints._write(run_id, operation):
            raise CheckpointError("审批已过期并失效")
        return self.get(approval_id)

    def consume_and_mark_running(self, approval_id, decision, revision):
        require(isinstance(decision, PermissionDecision), "需要当前权限决策")
        record = self.get(approval_id)
        old = record.decision.operation
        op = decision.operation
        require((op.run_id, op.turn, op.ordinal, op.provider_call_id) == (old.run_id, old.turn, old.ordinal, old.provider_call_id), "审批不能跨 Run / 调用复用")
        self.checkpoints.load(op.run_id)
        now = timestamp(self.clock())
        def operation(connection, token):
            data = dict(connection.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone())
            ApprovalRecord.from_dict(data)
            self.checkpoints._current(connection, op.run_id, revision)
            require(data["state"] == "APPROVED", "审批未批准、已失效或已经消费")
            require(now >= data["decided_at"], "审批时钟回退")
            current_decision = self.policy.evaluate(op)
            if current_decision != decision or decision.kind is not PermissionKind.ASK or data["binding_digest"] != op.binding_digest or (data["expires_at"] is not None and now >= data["expires_at"]):
                self._invalidate(connection, data, token, "当前策略 / 操作上下文变化或审批过期")
                return None
            self._prepared(connection, op, revision)
            connection.execute("UPDATE approvals SET state='CONSUMED',consumed_at=? WHERE approval_id=? AND state='APPROVED'", (now, approval_id))
            return self.checkpoints._start_call(connection, token, op.run_id, revision, op.turn, op.ordinal, op.provider_call_id, call=op.call, approval_id=approval_id)
        revision = self.checkpoints._write(op.run_id, operation)
        if revision is None:
            raise CheckpointError("旧审批已失效，不允许执行")
        return revision
