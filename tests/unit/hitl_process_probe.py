"""Independent process HITL/crash probe, restricted to a temporary workspace."""
import json
import os
from pathlib import Path
import sys
from agent import mini_claude_agent as module
from core.features import FeatureDefinition
from core.permissions import PermissionKind, PermissionDecision
from core.runtime_context.preflight import PreflightResult
from core.tools.base_tools import ToolResult
from core.tools.registry import ToolSpec
from test_run_snapshot import MockProvider, calls


def main():
    base, mode = Path(sys.argv[1]), sys.argv[2]
    workspace = base / "workspace"
    module.run_preflight = lambda root: PreflightResult("OFFLINE", workspace_root=str(root), workspace_read_write=True)
    module.MiniClaudeAgent._setup_logging = lambda self: None
    module.BackgroundProcessor.start = lambda self: None
    module.configure_primary_provider = lambda *args: None
    a = module.MiniClaudeAgent(config_path=base / "config.yaml", workspace_root=workspace,
        workspace_confirmed=True, runtime_data_root=base / ("runtime-" + mode))
    def probe(label):
        with (base / "effects.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"label": label, "pid": os.getpid()}) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        (workspace / "target.txt").write_text(label, encoding="utf-8")
        if mode == "hold" and label == "B":
            (base / "ready").write_text("ready", encoding="utf-8")
            sys.stdin.readline()
        return ToolResult("actual " + label)
    a.tool_registry.register(ToolSpec("probe", "Deterministic probe",
        {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]}, probe))
    a.feature_manager.register_feature(FeatureDefinition("probe_feature"))
    a.feature_manager.register_tool_for_feature("probe", "probe_feature")
    provider = MockProvider([{"tool_calls": calls("A", "B", "C")}, {}] if mode == "start" else [{}])
    a.provider_manager.register_provider("mock", provider, is_primary=True)
    a.compressor.set_provider(provider)
    a.enable_checkpoints(base / "checkpoint.sqlite3")
    a.enable_hitl()
    real = a._permission_policy.evaluate
    def evaluate(op):
        result = real(op)
        if result.kind is PermissionKind.ASK and op.arguments.get("label") in {"A", "C"}:
            return PermissionDecision(PermissionKind.ALLOW, op, "test.read_only", "deterministic mock")
        return result
    a._permission_policy.evaluate = evaluate
    store = a._checkpoint_store
    commit = store._commit
    def commit_or_die(connection):
        row = connection.execute("SELECT payload FROM runs").fetchone()
        data = json.loads(row[0]) if row and row[0] else None
        batch = data["run"]["tool_batch"] if data else None
        running = connection.execute("SELECT 1 FROM calls WHERE call_id='call-1' AND state='RUNNING'").fetchone()
        if mode == "running" and running:
            commit(connection)
            os._exit(42)
        if mode == "before_result" and batch and batch["next_call_index"] == 2:
            os._exit(43)
        commit(connection)
        if mode == "after_result" and batch and batch["next_call_index"] == 2:
            os._exit(44)
    store._commit = commit_or_die
    try:
        if mode == "start":
            result = a.run("original task", require_tool_call=True)
            run_id = a._run_state.run_id
            (base / "run_id").write_text(run_id, encoding="utf-8")
        else:
            run_id = (base / "run_id").read_text(encoding="utf-8")
            if mode in {"approve", "reject"}:
                approval = a.get_pending_approval(run_id)
                a.decide_approval(run_id, approval.approval_id, approved=mode == "approve", actor="process-user")
                result = mode
            else:
                result = a.resume_checkpoint(run_id)
        row = store.load(run_id)
        approval_state = a._approval_store.get(row["snapshot"]["run"]["approval_id"]).state.value if row["boundary"] == "approval" else None
        print(json.dumps({"run_id": run_id, "pid": os.getpid(), "status": row["status"],
            "requests": len(provider.requests), "approval": approval_state, "result": result,
            "journal": [r["state"] for r in row["journal"]]}), flush=True)
        return 0
    except module.CheckpointError as exc:
        print(json.dumps({"error": str(exc), "requests": len(provider.requests), "pid": os.getpid()}), flush=True)
        return 7
    finally:
        a.shutdown()


if __name__ == "__main__":
    sys.exit(main())
