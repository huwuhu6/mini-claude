"""Revocable resource rules, separate from exact one-call approvals and facts."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from core.permissions import ToolOperation, canonical, digest, tool_contract
from core.runtime_context.path_resolver import PathResolver
from core.run_snapshot import fields, require


def grant_fingerprint(operation):
    """Keep v4 exact-operation rules exact; never silently broaden old grants."""
    context = json.loads(operation.context_json)
    return digest([operation.workspace, operation.cwd, operation.tool_name,
                   operation.arguments, context["tool_contract"], context["safety_digest"],
                   context["runtime_digest"], context["policy_version"]])


def file_target(operation):
    return PathResolver(Path(operation.workspace)).resolve(operation.arguments["path"])


def reusable_file(operation):
    if operation.tool_name not in {"edit_file", "write_file"}:
        return False
    target = file_target(operation)
    try:
        relative = target.relative_to(Path(operation.workspace))
    except ValueError:
        return False
    name = target.name.lower()
    contract = json.loads(operation.context_json)["tool_contract"]
    audited = {f"agent.mini_claude_agent.MiniClaudeAgent._handle_{operation.tool_name}",
               f"core.tools.base_tools.BaseTools.{operation.tool_name}"}
    return (contract.get("handler") in audited and ".git" not in {part.lower() for part in relative.parts}
            and name not in {".env", "id_rsa", "id_ed25519", "credentials"}
            and not (name.startswith(".env.") and name != ".env.example"))


def can_remember(operation):
    return operation.tool_name not in {"edit_file", "write_file"} or reusable_file(operation)


class PermissionGrants:
    VERSION = 1

    def __init__(self, checkpoints, registry=None):
        self.checkpoints = checkpoints
        self.registry = registry
        self.process_grants = {}

    def _rule(self, operation, scope):
        require(scope in {"process", "conversation", "workspace"}, "授权范围无效")
        require(can_remember(operation), "此文件或 handler 仅支持单次确认")
        context = json.loads(operation.context_json)
        binding = {key: context[key] for key in ("runtime_digest", "safety_digest", "policy_version")}
        if reusable_file(operation):
            require(self.registry is not None, "文件规则要求当前工具注册信息")
            contracts = {name: tool_contract(self.registry.get_spec(name))
                         for name in ("edit_file", "write_file")}
            selector = {"kind": "file", "path": os.path.normcase(str(file_target(operation)))}
            binding["contracts"] = contracts
        else:
            # Shell remains exact: no wildcard grants for wrappers or compounds.
            selector = {"kind": "exact", "tool": operation.tool_name,
                        "arguments": operation.arguments, "cwd": operation.cwd,
                        "platform": sys.platform}
            binding["contracts"] = {operation.tool_name: context["tool_contract"]}
        conversation = self.checkpoints.conversation_for_run(operation.run_id) if scope == "conversation" else None
        require(scope != "conversation" or conversation is not None, "当前调用没有会话身份")
        return {"version": self.VERSION, "scope": scope, "conversation_id": conversation,
                "workspace": operation.workspace, "selector": selector, "binding": binding}

    def remember(self, operation, scope):
        rule = self._rule(operation, scope)
        fingerprint = digest(rule)
        if scope == "process":
            self.process_grants[fingerprint] = rule
        else:
            self.checkpoints.remember_permission(operation.run_id, operation.workspace,
                                                 fingerprint, canonical(rule))

    @staticmethod
    def _decode(row):
        rule = json.loads(row["operation_json"])
        if "version" not in rule:
            operation = ToolOperation.from_dict(rule)
            return operation if operation.workspace == row["workspace"] and grant_fingerprint(operation) == row["fingerprint"] else None
        fields(rule, {"version", "scope", "conversation_id", "workspace", "selector", "binding"}, "授权规则")
        require(type(rule["version"]) is int and rule["version"] == 1, "授权规则版本不兼容")
        require(rule["scope"] in {"conversation", "workspace"}, "持久规则范围无效")
        require((type(rule["conversation_id"]) is str and bool(rule["conversation_id"]))
                if rule["scope"] == "conversation" else rule["conversation_id"] is None, "规则会话身份无效")
        selector = rule["selector"]
        require(type(selector) is dict, "规则目标无效")
        kind = selector.get("kind")
        require(kind in {"file", "exact"}, "规则类型无效")
        fields(selector, {"kind", "path"} if kind == "file" else
               {"kind", "tool", "arguments", "cwd", "platform"}, "规则目标")
        fields(rule["binding"], {"runtime_digest", "safety_digest", "policy_version", "contracts"}, "规则绑定")
        require(type(rule["binding"]["contracts"]) is dict, "规则工具绑定无效")
        return rule if rule["workspace"] == row["workspace"] and digest(rule) == row["fingerprint"] else None

    def _matches(self, rule, operation):
        if isinstance(rule, ToolOperation):
            return grant_fingerprint(rule) == grant_fingerprint(operation)
        if rule["scope"] == "conversation" and self.checkpoints.conversation_for_run(operation.run_id) != rule["conversation_id"]:
            return False
        return rule == self._rule(operation, rule["scope"])

    def allows(self, operation):
        if not can_remember(operation):
            return False
        # Storage failures cannot silently downgrade to an in-memory allow.
        rules = [self._decode(row) for row in self.checkpoints.list_permission_grants(operation.workspace)]
        return any(self._matches(rule, operation) for rule in [*self.process_grants.values(), *rules] if rule is not None)

    def list(self, workspace):
        rows = [{"grant_id": key, "scope": "process", "rule": rule}
                for key, rule in self.process_grants.items() if rule["workspace"] == workspace]
        for row in self.checkpoints.list_permission_grants(workspace):
            rule = self._decode(row)
            if rule is not None:
                rows.append({"grant_id": row["grant_id"],
                             "scope": rule["scope"] if isinstance(rule, dict) else "workspace",
                             "rule": rule})
        return rows

    def revoke(self, workspace, grant_id):
        local = self.process_grants.get(grant_id)
        if local and local["workspace"] == workspace:
            del self.process_grants[grant_id]
            return True
        return self.checkpoints.revoke_permission(workspace, grant_id)
