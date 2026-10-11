"""Isolated process probe; only temporary files and Mock Provider are used."""
import json
import os
from pathlib import Path
import sys

from agent import mini_claude_agent as module
from core.features import FeatureDefinition
from core.runtime_context.preflight import PreflightResult
from core.tools.base_tools import ToolResult
from core.tools.registry import ToolSpec
from test_run_snapshot import MockProvider, calls


def main():
    base, mode = Path(sys.argv[1]), sys.argv[2]
    workspace = base / "workspace"
    config = base / "config.yaml"
    module.run_preflight = lambda root: PreflightResult("OFFLINE", workspace_root=str(root), workspace_read_write=True)
    module.MiniClaudeAgent._setup_logging = lambda self: None
    module.BackgroundProcessor.start = lambda self: None
    module.configure_primary_provider = lambda *args: None
    agent = module.MiniClaudeAgent(config_path=config, workspace_root=workspace,
        workspace_confirmed=True, runtime_data_root=base / ("runtime-" + mode))
    effects = base / "effects.jsonl"
    def probe(label):
        with effects.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"label": label, "pid": os.getpid()}) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        (workspace / "target.txt").write_text(label, encoding="utf-8")
        if mode == "pause" and label == "A":
            agent._request_run_pause()
        if mode == "hold_handler" and label == "A":
            (base / "ready").write_text("ready", encoding="utf-8")
            sys.stdin.readline()
        return ToolResult("actual " + label)
    agent.tool_registry.register(ToolSpec("probe", "Deterministic probe",
        {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]}, probe))
    agent.feature_manager.register_feature(FeatureDefinition("probe_feature"))
    agent.feature_manager.register_tool_for_feature("probe", "probe_feature")
    provider = MockProvider([{"content": "batch", "tool_calls": calls("A", "B", "C")}, {}])
    agent.provider_manager.register_provider("mock", provider, is_primary=True)
    agent.compressor.set_provider(provider)
    agent.enable_checkpoints(base / "checkpoint.sqlite3")
    if mode == "response_uncommitted":
        create = provider.create_message
        def create_and_die(*args, **kwargs):
            create(*args, **kwargs)
            os._exit(37)
        provider.create_message = create_and_die
    store = agent._checkpoint_store
    save = store.save_snapshot
    def save_and_exit(run_id, revision, snapshot, **kwargs):
        result = save(run_id, revision, snapshot, **kwargs)
        (base / "run_id").write_text(run_id, encoding="utf-8")
        batch = snapshot["run"]["tool_batch"]
        if mode == "before_model" and kwargs.get("boundary") == "before_model":
            os._exit(35)
        if mode == "prepared" and batch and batch["next_call_index"] == 0:
            os._exit(31)
        if mode == "after_result" and batch and batch["next_call_index"] == 1:
            os._exit(34)
        return result
    store.save_snapshot = save_and_exit
    running = store.mark_running
    def running_and_exit(*args, **kwargs):
        result = running(*args, **kwargs)
        if mode == "running":
            os._exit(32)
        return result
    store.mark_running = running_and_exit
    commit = store._commit
    def commit_or_exit(connection):
        row = connection.execute("SELECT payload FROM runs").fetchone()
        data = json.loads(row[0]) if row and row[0] else None
        batch = data["run"]["tool_batch"] if data else None
        if mode == "before_result" and batch and batch["next_call_index"] == 1:
            os._exit(33)
        commit(connection)
    store._commit = commit_or_exit
    finish = store.finish
    def finish_and_exit(*args):
        result = finish(*args)
        if mode == "after_terminal":
            os._exit(36)
        return result
    store.finish = finish_and_exit
    try:
        if mode == "resume":
            run_id = (base / "run_id").read_text(encoding="utf-8")
            record = store.load(run_id)
            if record["snapshot"] and record["snapshot"]["run"]["model_turns_used"] != 0:
                provider.responses = [{}]
            result = agent.resume_checkpoint(run_id)
        else:
            result = agent.run("original task", require_tool_call=True)
            run_id = agent._run_state.run_id
        record = store.load(run_id)
        print(json.dumps({"result": result, "requests": len(provider.requests),
            "status": record["status"], "cursor": record["snapshot"]["run"]["tool_batch"]["next_call_index"]
                if record["snapshot"]["run"]["tool_batch"] else None,
            "journal": [r["state"] for r in record["journal"]], "run_id": run_id}), flush=True)
    except module.CheckpointError as error:
        print(json.dumps({"error": str(error), "requests": len(provider.requests)}), flush=True)
        return 7
    finally:
        agent.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
