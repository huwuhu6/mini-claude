"""Data-only permission contracts; decisions neither execute nor govern failures."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
from inspect import getsource, signature
import json
import re
import sys
from pathlib import Path

from core.run_snapshot import call_ids, fields, json_copy, require
from core.tools.base_tools import BaseTools

POLICY_VERSION = "main-conservative-v1"


def canonical(data):
    return json.dumps(json_copy(data), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(data):
    return hashlib.sha256(canonical(data).encode("utf-8")).hexdigest()


class PermissionKind(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    ASK = "ASK"


@dataclass(frozen=True)
class ToolOperation:
    run_id: str
    turn: int
    ordinal: int
    call_json: str
    workspace: str
    cwd: str
    context_json: str

    def __post_init__(self):
        require(type(self.run_id) is str and bool(self.run_id), "Run ID 无效")
        require(type(self.turn) is int and self.turn >= 0 and type(self.ordinal) is int and self.ordinal >= 0, "调用位置无效")
        call = json.loads(self.call_json)
        call_ids([call])
        require(type(json.loads(call["function"]["arguments"])) is dict, "工具参数必须为 JSON 对象")
        require(self.call_json == canonical(call), "原调用必须为规范 JSON")
        for path in (self.workspace, self.cwd):
            require(type(path) is str and Path(path).is_absolute() and str(Path(path).resolve()) == path, "工作区 / cwd 必须为规范绝对路径")
        context = json.loads(self.context_json)
        fields(context, {"policy_version", "workspace_digest", "tool_contract", "safety_digest", "runtime_digest"}, "审批上下文")
        require(context["policy_version"] == POLICY_VERSION and type(context["tool_contract"]) is dict, "策略上下文不兼容")
        for key in ("workspace_digest", "safety_digest", "runtime_digest"):
            require(type(context[key]) is str and len(context[key]) == 64 and all(c in "0123456789abcdef" for c in context[key]), "上下文摘要无效")
        require(self.context_json == canonical(context), "审批上下文必须为规范 JSON")

    @classmethod
    def from_dict(cls, data):
        fields(data, set(cls.__dataclass_fields__), "工具操作")
        return cls(**json_copy(data))

    def to_dict(self):
        return asdict(self)

    @property
    def call(self):
        return json.loads(self.call_json)

    @property
    def tool_name(self):
        return self.call["function"]["name"]

    @property
    def provider_call_id(self):
        return self.call["id"]

    @property
    def arguments(self):
        return json.loads(self.call["function"]["arguments"])

    @property
    def arguments_digest(self):
        return digest(self.arguments)

    @property
    def batch_id(self):
        return digest([self.run_id, self.turn])

    @property
    def internal_call_id(self):
        return digest([self.run_id, self.turn, self.ordinal, self.provider_call_id])

    @property
    def binding_digest(self):
        return digest(self.to_dict())


@dataclass(frozen=True)
class PermissionDecision:
    kind: PermissionKind
    operation: ToolOperation
    rule_id: str
    reason: str
    policy_version: str = POLICY_VERSION

    def __post_init__(self):
        require(isinstance(self.kind, PermissionKind) and isinstance(self.operation, ToolOperation), "权限决策类型无效")
        require(type(self.rule_id) is str and bool(self.rule_id) and type(self.reason) is str and bool(self.reason), "决策缺少审计说明")
        require(self.policy_version == POLICY_VERSION, "策略版本不兼容")

    def to_dict(self):
        return {"kind": self.kind.value, "operation": self.operation.to_dict(), "rule_id": self.rule_id,
                "reason": self.reason, "policy_version": self.policy_version}

    @classmethod
    def from_dict(cls, data):
        fields(data, {"kind", "operation", "rule_id", "reason", "policy_version"}, "权限决策")
        return cls(PermissionKind(data["kind"]), ToolOperation.from_dict(data["operation"]),
                   data["rule_id"], data["reason"], data["policy_version"])


class DefaultPermissionPolicy:
    """Hard gates first; only audited built-in handler identities auto-allow.

    The caller supplies freshly observed workspace/runtime digests. This class
    does not claim a filesystem lock or a shell sandbox and never calls handlers.
    """
    def __init__(self, registry, features, authority, command_policy, cwd):
        self.registry, self.features, self.authority = registry, features, authority
        self.command_policy, self.cwd = command_policy, cwd
        self.workspace_digest = None
        self.runtime_digest = None
        self._readers = {name: getattr(BaseTools, name) for name in
                         ("read_file", "search_code", "count_occurrences", "list_files")}

    @classmethod
    def for_agent(cls, agent):
        from agent.mini_claude_agent import MiniClaudeAgent
        policy = cls(agent.tool_registry, agent.feature_manager, agent.workspace_authority,
                     agent.command_policy, lambda: agent.runtime_context.cwd)
        policy.workspace_digest = agent.workspace_state_guard.content_digest
        policy.runtime_digest = lambda: digest(agent._snapshot_compatibility())
        if type(agent) is MiniClaudeAgent:
            policy._readers.update({name: getattr(MiniClaudeAgent, "_handle_" + name) for name in
                ("read_file", "search_code", "count_occurrences", "list_files", "get_background_status")})
        # get_background_logs may spill formatted output; health_check uses network.
        # Todo, Note, skills, grants, process control and custom tools stay ASK.
        return policy

    def bind(self, run_id, turn, ordinal, call, *, workspace_digest, runtime_digest):
        spec = self.registry.get_spec(call["function"]["name"])
        contract = {"unregistered": True}
        if spec is not None:
            try:
                source = getsource(spec.handler)
            except (TypeError, OSError):
                source = "opaque"
            contract = {"schema": spec.input_schema, "handler": f"{getattr(spec.handler, '__module__', 'opaque')}.{getattr(spec.handler, '__qualname__', 'opaque')}",
                        "source_hash": hashlib.sha256(source.encode("utf-8")).hexdigest()}
        rules = {key: [[p.pattern, int(p.flags)] for p in getattr(self.command_policy, key)] for key in
                 ("HIGH_RISK_EXECUTORS", "DESTRUCTIVE", "PIPE_BOMB", "POWERSHELL_BYPASS")}
        rules["BACKGROUND"] = [self.command_policy.BACKGROUND.pattern, int(self.command_policy.BACKGROUND.flags)]
        rules["implementation"] = getsource(type(self.command_policy))
        safety = {"features": sorted(self.features.get_enabled_features()),
                  "enabled": self.features.is_tool_enabled(call["function"]["name"]),
                  "roots": [str(self.authority.primary_root), *sorted(str(p) for p in self.authority.additional_roots)], "commands": rules}
        return ToolOperation(run_id, turn, ordinal, canonical(call), str(self.authority.primary_root),
            str(Path(self.cwd()).resolve()), canonical({"policy_version": POLICY_VERSION,
            "workspace_digest": workspace_digest, "runtime_digest": runtime_digest,
            "tool_contract": contract, "safety_digest": digest(safety)}))

    def evaluate(self, operation):
        def decision(kind, rule, reason):
            return PermissionDecision(kind, operation, rule, reason)
        spec = self.registry.get_spec(operation.tool_name)
        if spec is None:
            return decision(PermissionKind.DENY, "tool.unregistered", "工具未注册，审批不能创建执行能力")
        if not self.features.is_tool_enabled(operation.tool_name):
            return decision(PermissionKind.DENY, "feature.disabled", "当前功能已禁用，审批不能绕过能力边界")
        args = operation.arguments
        opaque = False
        try:
            signature(spec.handler).bind(**args)
        except TypeError:
            return decision(PermissionKind.DENY, "arguments.invalid", "参数无法绑定到当前 handler")
        except ValueError:
            opaque = True
        try:
            self.authority.check(operation.cwd)
            require(Path(operation.cwd).is_dir(), "cwd 不存在")
            if operation.tool_name in {"read_file", "write_file", "edit_file", "list_files"}:
                self.authority.check(args.get("path", "."))
            if operation.tool_name in {"search_code", "count_occurrences"}:
                paths = args.get("paths") or ["."]
                require(type(paths) is list and all(type(p) is str for p in paths), "paths 无效")
                for path in paths:
                    self.authority.check(path)
        except (ValueError, TypeError):
            return decision(PermissionKind.DENY, "workspace.boundary", "目录或文件路径无效 / 超出当前硬性授权边界")
        if operation.tool_name in {"bash", "run_background"}:
            command = args.get("command")
            if type(command) is not str:
                return decision(PermissionKind.DENY, "arguments.invalid", "command 必须为字符串")
            blocked = self.command_policy.check(command)
            if blocked:
                return decision(PermissionKind.DENY, "command.hard_deny", blocked)
            if operation.tool_name == "bash" and sys.platform == "win32" and re.match(r"^\s*start(?:\s|$)", command, re.I) and not self.features.is_tool_enabled("run_background"):
                return decision(PermissionKind.DENY, "feature.background_disabled", "Windows start 路由的后台能力已禁用")
        context = json.loads(operation.context_json)
        current = self.bind(operation.run_id, operation.turn, operation.ordinal, operation.call,
                           workspace_digest=self.workspace_digest() if self.workspace_digest else context["workspace_digest"],
                           runtime_digest=self.runtime_digest() if self.runtime_digest else context["runtime_digest"])
        if current != operation:
            return decision(PermissionKind.DENY, "context.changed", "工作区、cwd、工具实现或有效策略发生变化，旧授权不可复用")
        handler = getattr(spec.handler, "__func__", spec.handler)
        if not opaque and handler is self._readers.get(operation.tool_name):
            if any(any(c in p for c in "*?[") for p in (args.get("paths") or [])):
                return decision(PermissionKind.ASK, "paths.dynamic", "通配符目标需执行时逐项授权校验")
            return decision(PermissionKind.ALLOW, "audited.read_only", "已核实的内置只读操作")
        return decision(PermissionKind.ASK, "risk.unconfirmed", "操作会改变状态或风险无法确认，需要针对此调用授权")
