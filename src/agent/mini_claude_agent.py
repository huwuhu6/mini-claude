"""
Mini Claude Agent - Unified agent integrating all systems.
"""
from __future__ import annotations
import logging
import hashlib
import sys
import json
import re
import uuid
import time
import shutil
import socket
import copy
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, List, Dict, Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

# Ensure src is in path
_project_root = Path(__file__).parent.parent.parent
_src_path = str(_project_root / 'src')
if _src_path not in sys.path:
    sys.path.insert(0, _src_path)

from models.config import ConfigManager, Config
from models.task import Task, TaskManager
from models.teammate import Teammate, TeammateRole
from models.todo import TodoManager

from providers.base import Message, ToolDefinition as ProviderToolDef
from providers.bootstrap import configure_primary_provider
from providers.manager import ProviderManager

from core.tools.base_tools import BaseTools, ToolResult
from core.tools.registry import ToolArgumentBindingError, ToolRegistry, ToolSpec
from core.tool_outcome import NonExecutionReason, non_execution_facts
from core.tools.definitions import (
    bash_spec,
    edit_file_spec,
    read_file_spec,
    write_file_spec,
)
from core.features import FeatureManager, FeatureDefinition
from core.messaging import MessageBus, Message as BusMessage, MessagePriority
from core.teammate_manager import TeammateManager, TeammateConfig
from core.background import BackgroundProcessor, BackgroundTaskStatus
from core.subagent import SubAgentManager, SubAgentType, SubAgentResult
from core.compression import Compressor
from core.prompt_builder import build_system_prompt
from core.agent_note import AgentNote
from core.context_memory import StructuredContextMemory
from core.loop_guard import canonicalize_args
from core.loop_controller import (
    AttemptHistory, AttemptStatus, CommandNormalizer, RuntimeDecision,
    LoopController, RuntimeEscalationException, RuntimePolicy, RuntimePolicyAdapter,
)
from core.tracing import TraceManager
from core.runtime_data import RuntimeDataPaths
from core.run_state import (
    RunState,
    RunStatus,
    ToolBatchAdvance,
    ToolBatchStatus,
    ToolBatchState,
    ToolCallResultState,
)
from core.checkpoint_store import CheckpointError, SqliteCheckpointStore, TERMINAL as CHECKPOINT_TERMINAL
from core.run_snapshot import (
    SCHEMA_VERSION, CHECKPOINT_SNAPSHOT_VERSION, HITL_SNAPSHOT_VERSION, SNAPSHOT_FIELDS, PAUSED_RESULT_OUTCOMES, decode_messages, decode_run, encode_run,
    BOUND_SNAPSHOT_VERSIONS, HITL_SNAPSHOT_VERSIONS, STANDALONE_SNAPSHOT_VERSIONS,
    decode_tool_result, fields as snapshot_fields, json_copy, require as snapshot_require, text as snapshot_text,
)
from core.approvals import ApprovalStore, ApprovalStatus
from core.permissions import DefaultPermissionPolicy, PermissionKind, digest as permission_digest
from core.session_recorder import SessionLogHandler, SessionRecorder
from core.failure_intelligence import FailureAnalyzer
from core.runtime_context import (
    RuntimeContext,
    run_preflight,
    EnvironmentBlocker,
    WorkspaceStateGuard,
)
from core.runtime_context.observation import ObservationNormalizer
from core.runtime_context.command_policy import CommandPolicy
from core.runtime_context.workspace_authority import WorkspaceAuthority
from skills import SkillLoader

logger = logging.getLogger(__name__)


class ToolDispatchStatus(str, Enum):
    DISPATCHED = "dispatched"
    FEATURE_DISABLED = "feature_disabled"
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_ARGUMENTS = "invalid_arguments"
    EXECUTION_FAILED = "execution_failed"
    WAITING_APPROVAL = "waiting_approval"
    PERMISSION_DENIED = "permission_denied"
    APPROVAL_REJECTED = "approval_rejected"


@dataclass(frozen=True)
class ToolDispatchResult:
    """Explicit outcome from the Main Agent's pre-handler dispatch boundary."""

    status: ToolDispatchStatus
    value: Any


class _RuntimeCompletionGate:
    """Completion compatibility gate; terminal runtime decisions stay in RuntimePolicy."""

    def __init__(self, policy: RuntimePolicy):
        self.policy = policy

    def reset(self) -> None:
        return None

    def check(self, _tracker):
        decision = self.policy.finalize()
        return type("CompletionDecision", (), {
            "should_terminate": decision.action is RuntimeDecision.HARD_STOP,
            "should_replan": decision.action is RuntimeDecision.REPLAN,
            "action": decision.action,
            "reason": decision.reason,
            "open_blocker_count": 1 if decision.action is not RuntimeDecision.ALLOW else 0,
        })()

    @staticmethod
    def message(_decision) -> str:
        return ""


class MiniClaudeAgent:
    """
    Unified Mini Claude Agent integrating all systems:
    - LLM Providers (Deepseek, Anthropic)
    - Tool system (bash, file ops)
    - Feature management
    - Task management
    - Team management
    - Message bus
    - Background processing
    - SubAgents
    - Skills
    - Context compression
    """

    def __init__(self, config_path: Optional[Path] = None,
                 workspace_root: Optional[Path] = None,
                 workdir: Optional[Path] = None,
                 workspace_confirmed: bool = False,
                 runtime_data_root: Optional[Path] = None,
                 checkpoint_enabled: bool = False, hitl_enabled: bool = False):
        self._ui_event_handler: Optional[Callable[[str, Dict[str, Any]], None]] = None
        self._last_assistant_note: Optional[str] = None
        # ── Resolve workspace root (explicit > legacy > cwd fallback) ──
        if workspace_root is not None:
            self.workdir = Path(workspace_root).resolve()
        elif workdir is not None:
            self.workdir = Path(workdir).resolve()
        else:
            self.workdir = Path.cwd()
            logger.warning(
                "未指定 workspace_root，使用 Path.cwd() — compatibility path only"
            )

        # Config must be loaded first (used by _setup_logging)
        self.config_manager = ConfigManager(config_path)
        self.config = self.config_manager.get_config()
        # Probe before session subsystems start so the system prompt and audit
        # record share the same bounded environment facts.
        self.preflight = run_preflight(self.workdir)
        # Preflight is a startup capability snapshot only. Runtime decisions
        # use fresh tool results and the unified attempt stream below.
        self.environment_blocker = EnvironmentBlocker(self.preflight)
        self.data_paths = RuntimeDataPaths.for_workspace(
            self.workdir, data_root=runtime_data_root
        )
        self.agent_note = AgentNote(self.data_paths.root)
        self._compression_notice_pending = False
        self.session_recorder = SessionRecorder(self.data_paths.sessions)

        self._setup_logging()
        self.session_recorder.record(
            "session_start",
            workspace=str(self.workdir),
            data_root=str(self.data_paths.root),
        )
        self.session_recorder.record("environment_preflight", **self.preflight.to_dict())
        logger.info(f"正在初始化 MiniClaudeAgent v{self.config.agent.version}")

        # Workspace Authority — unified permission boundary
        self.workspace_authority = WorkspaceAuthority(primary_root=self.workdir)

        # Runtime context — workspace binding, path resolution, shell session
        self.runtime_context = RuntimeContext(workspace_root=self.workdir)
        self.workspace_state_guard = WorkspaceStateGuard(self.workdir)

        # Core tools (delegates whitelist to WorkspaceAuthority when available)
        self.tools = BaseTools(
            self.workdir,
            authority=self.workspace_authority,
            shell_session=self.runtime_context.shell_session,
        )
        self.command_policy = CommandPolicy()
        # Keep compact, freshness-checked notes about files seen in this run.
        self.memory = StructuredContextMemory()
        self._memory_file_fingerprints: Dict[str, tuple[tuple[int, int], str]] = {}

        self.tool_registry = ToolRegistry()
        self._register_tools()

        # In-memory todo tracker (s_full.py s03 Nag system)
        self.todo = TodoManager()

        # Feature management
        self.feature_manager = FeatureManager()
        self._register_features()

        # LLM Providers
        self.provider_manager = ProviderManager()
        configure_primary_provider(self.provider_manager, self.config.llm)

        # Task management
        self.task_manager = TaskManager(self.data_paths.tasks)

        # Team management
        team_config = TeammateConfig(
            directory=str(self.data_paths.team),
            idle_timeout=self.config.team.idle_timeout,
            auto_claim_tasks=self.config.team.auto_claim_tasks,
        )
        self.team_manager = TeammateManager(team_config)

        # Message bus
        self.message_bus = MessageBus(storage_dir=self.data_paths.inbox)

        # Background processing
        self.background = BackgroundProcessor(
            max_concurrent=self.config.background.max_concurrent,
            notification_queue_size=self.config.background.notification_queue_size,
            output_dir=self.data_paths.root / "background",
        )

        # SubAgents
        self.subagent_manager = SubAgentManager(
            self.provider_manager, self.workdir,
            authority=self.workspace_authority,
        )

        # Skills
        skills_dir = self.workdir / self.config.skills.directory
        self.skill_loader = SkillLoader(skills_dir)
        if self.feature_manager.is_enabled('skills'):
            names = self.skill_loader.discover()
            if names:
                logger.info(f"已加载 {len(names)} 个技能模块: {', '.join(names)}")

        # Compression
        compression_config = {
            'context_window_tokens': self.config.compression.context_window_tokens,
            'microcompact_token_threshold': self.config.compression.microcompact_token_threshold,
            'full_compression_token_threshold': self.config.compression.full_compression_token_threshold,
            'max_transcripts': self.config.compression.max_transcripts,
            'transcript_dir': str(self.data_paths.root / 'transcripts'),
        }
        self.compressor = Compressor(compression_config)

        # Inject LLM provider for real AI-powered summarization
        primary = self.provider_manager.get_primary_provider()
        if primary:
            self.compressor.set_provider(primary)

        # Conversation state
        self.messages: List[Message] = []
        self._running = False

        # Stateless semantic observer + the single runtime fact stream.
        self.failure_analyzer = FailureAnalyzer()
        self.observation_normalizer = ObservationNormalizer()
        self.attempt_history = AttemptHistory(maxlen=32)
        self.runtime_policy = RuntimePolicy(self.attempt_history)
        # LoopController is only a facade over this exact policy instance.
        self.loop_controller = LoopController(policy=self.runtime_policy)
        # The adapter exists only for trace compatibility; it has no state or
        # second event store.
        self.runtime_observer = RuntimePolicyAdapter(self.runtime_policy)
        self.completion_guard = _RuntimeCompletionGate(self.runtime_policy)
        # Runtime trace system — append-only, hook-based observability
        self.trace = TraceManager(trace_dir=self.data_paths.traces)
        # Benchmark metrics — reset each _llm_tool_cycle call
        self.last_metrics: Dict[str, Any] = self._empty_usage_metrics()
        self._cmd_history: List[tuple] = []


        # Tracks current user prompt for trace recording
        self._current_user_prompt: str = ""
        self._run_state: Optional[RunState] = None
        # Instance-local replay guard; this is not a durable or cross-instance lease.
        self._seen_run_ids: set[str] = set()
        # Workspace confirmation state for trace schema
        self._workspace_confirmed = workspace_confirmed

        self._checkpoint_store: Optional[SqliteCheckpointStore] = None
        self._checkpoint_run_id: Optional[str] = None
        self._checkpoint_revision: Optional[int] = None
        self._checkpoint_faulted = False
        self._hitl_enabled = False
        self._permission_policy = None
        self._approval_store = None
        if checkpoint_enabled:
            self.enable_checkpoints()
        if hitl_enabled:
            self.enable_hitl()

        # Start background processor
        self.background.start()

        # Load system prompt
        self.refresh_system_prompt()

    def _setup_logging(self):
        """Configure logging based on config."""
        log_level = getattr(logging, self.config.logging.level.upper(), logging.INFO)
        # Runtime diagnostics belong in the session JSONL, not in the chat UI.
        # force=True prevents a previous agent instance from keeping an old
        # workspace's FileHandler when tests create multiple agents.
        logging.basicConfig(
            level=log_level,
            handlers=[SessionLogHandler(self.session_recorder)],
            force=True,
        )

        # Suppress verbose third-party logs
        logging.getLogger('httpx').setLevel(logging.WARNING)

    def _register_features(self):
        """Register all feature flags with their dependencies."""
        features_config = self.config.features
        self.feature_manager.register_feature(FeatureDefinition(
            name='bash', description='Shell command execution', category='tools',
            enabled=True,
        ))
        self.feature_manager.register_feature(FeatureDefinition(
            name='read_file', description='Read file contents', category='tools',
            enabled=True,
        ))
        self.feature_manager.register_feature(FeatureDefinition(
            name='write_file', description='Write file contents', category='tools',
            enabled=True,
        ))
        self.feature_manager.register_feature(FeatureDefinition(
            name='edit_file', description='Edit file contents', category='tools',
            enabled=True,
        ))
        self.feature_manager.register_feature(FeatureDefinition(
            name='subagent', description='SubAgent creation and execution',
            category='advanced', enabled=features_config.subagent,
        ))
        self.feature_manager.register_feature(FeatureDefinition(
            name='compression', description='Context compression',
            category='core', enabled=features_config.compression,
        ))
        self.feature_manager.register_feature(FeatureDefinition(
            name='memory', description='Transient structured file memory',
            category='core', enabled=features_config.memory,
        ))
        self.feature_manager.register_feature(FeatureDefinition(
            name='background', description='Background command execution',
            category='core', enabled=features_config.background,
        ))
        self.feature_manager.register_feature(FeatureDefinition(
            name='skills', description='Skill modules',
            category='advanced', enabled=features_config.skills,
        ))
        # Register tool→feature mapping for feature-aware tool filtering
        for tool_name, feature_name in (
            ('bash', 'bash'),
            ('read_file', 'read_file'),
            ('write_file', 'write_file'),
            ('edit_file', 'edit_file'),
            ('task', 'subagent'),
        ):
            self.feature_manager.register_tool_for_feature(tool_name, feature_name)
        self.feature_manager.register_tool_for_feature('load_skill', 'skills')
        self.feature_manager.register_tool_for_feature('update_agent_note', 'compression')
        self.feature_manager.register_tool_for_feature('run_background', 'background')
        for tool_name in (
            'get_background_status', 'get_background_logs',
            'stop_background', 'health_check',
        ):
            self.feature_manager.register_tool_for_feature(tool_name, 'background')

    def refresh_system_prompt(self) -> None:
        """Rebuild the stable system prompt from current runtime state."""
        skill_descriptions = ""
        if self.feature_manager.is_enabled("skills") and hasattr(self, "skill_loader"):
            skill_descriptions = self.skill_loader.descriptions()
        self.system_prompt = build_system_prompt(
            agent_name=self.config.agent.name,
            agent_version=self.config.agent.version,
            workspace=str(self.workdir),
            enabled_features=self.feature_manager.get_enabled_features(),
            preflight=self.preflight,
            platform=sys.platform,
            skill_descriptions=skill_descriptions,
        )

    # ═══════════════════════════════════════════════════════════
    # Main Agent Loop
    # ═══════════════════════════════════════════════════════════

    def run(self, user_input: str, require_tool_call: bool = False) -> str:
        """Process user input and return agent response."""
        if getattr(self, "_checkpoint_faulted", False):
            raise CheckpointError("当前实例已因 Checkpoint 故障停止；请用新实例核验原 Run，不能直接开始新任务")
        current = getattr(self, "_run_state", None)
        if current and current.status in {RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.WAITING_APPROVAL}:
            return "错误: 当前运行尚未完成；请先继续或终止该运行。"
        if self.has_unfinished_conversation:
            return "错误: 当前会话有未完成或 UNKNOWN 操作；请查看原任务，必要时人工检查后 /archive <原因>，不能覆盖原运行。"
        self._run_state = RunState(require_tool_call=require_tool_call)
        try:
            self._seen_run_ids = getattr(self, "_seen_run_ids", set())
            self._seen_run_ids.add(self._run_state.run_id)
            self.todo.reset()
            self._current_user_prompt = user_input
            self.messages.append(Message(role='user', content=user_input))
            recorder = getattr(self, "session_recorder", None)
            if recorder is not None:
                recorder.record("run_started", run_id=self._run_state.run_id)
        except BaseException as exc:
            self._abort_run_initialization(self._run_state, exc)
            raise
        return self._llm_tool_cycle(require_tool_call=require_tool_call)

    def _snapshot_compatibility(self) -> dict:
        """Strict local implementation/config identity; credentials and grants excluded."""
        from dataclasses import asdict
        runtime_root = Path(__file__).resolve().parents[1]
        # Fail closed after relevant implementation changes, including delegated helpers.
        sources = ["agent/mini_claude_agent.py", "core/run_state.py", "core/run_snapshot.py", "core/checkpoint_store.py",
                   "core/loop_controller.py", "core/tool_outcome.py", "core/tools/base_tools.py", "core/tools/file_edits.py", "core/tools/registry.py", "core/permissions.py", "core/permission_grants.py", "core/approvals.py",
                   "core/runtime_context/shell_session.py", "core/runtime_context/command_policy.py",
                   "core/runtime_context/workspace_authority.py", "core/runtime_context/path_resolver.py",
                   "core/features/manager.py", "core/compression.py",
                   "core/runtime_context/workspace_state.py", "core/runtime_context/file_checkpoint.py", "core/runtime_context/observation.py",
                   "core/failure_intelligence/analyzer.py", "core/failure_intelligence/signatures.py",
                   "core/failure_intelligence/models.py", "core/agent_note.py", "models/todo.py"]
        source_hash = hashlib.sha256()
        for relative in sources:
            source_hash.update(relative.encode("utf-8"))
            source_hash.update((runtime_root / relative).read_text(encoding="utf-8").encode("utf-8"))
        return {
            "runtime_source_hash": source_hash.hexdigest(),
            "hitl_enabled": getattr(self, "_hitl_enabled", False),
            "platform": sys.platform,
            "python_version": list(sys.version_info[:2]),
            "tools": self.tool_registry.execution_contract(),
            "llm": {key: getattr(self.config.llm, key) for key in
                    ("provider", "model", "max_tokens", "temperature", "stream")},
            "compression": asdict(self.config.compression),
            "history_maxlen": self.attempt_history.maxlen,
            "loop_horizon": self.runtime_policy.loop_detector.intent_horizon,
            "failure_horizon": self.runtime_policy.failure_detector.horizon,
        }

    def _validate_run_snapshot(self, raw: dict, *, _checkpoint_boundary: str | None = None):
        """Pure preparation: no live state, files, trace or authorization is overwritten."""
        snapshot = json_copy(raw)
        linked = type(snapshot) is dict and snapshot.get("schema_version") in BOUND_SNAPSHOT_VERSIONS
        snapshot_fields(snapshot, SNAPSHOT_FIELDS | ({"checkpoint_binding"} if linked else set()), "Snapshot")
        store = getattr(self, "_checkpoint_store", None)
        snapshot_require(type(snapshot["schema_version"]) is int
                         and (snapshot["schema_version"] in STANDALONE_SNAPSHOT_VERSIONS or (linked and store is not None)), "快照版本不兼容；持久化投影要求 Checkpoint Store")
        snapshot_require(snapshot["schema_version"] not in HITL_SNAPSHOT_VERSIONS or getattr(self, "_hitl_enabled", False), "审批快照必须启用持久化 HITL")
        snapshot_require(snapshot["compatibility"] == self._snapshot_compatibility(),
                         "工具实现、Schema 或关键运行配置不兼容")
        workspace = snapshot["workspace"]
        snapshot_fields(workspace, {"root", "cwd", "content_digest"} |
                        ({"file_targets"} if "file_targets" in workspace else set()), "Workspace")
        for key in ("root", "cwd"):
            snapshot_text(workspace[key], key, nonempty=True)
            path = Path(workspace[key])
            snapshot_require(path.is_absolute() and str(path.resolve()) == workspace[key],
                             f"{key} 必须为当前有效的绝对规范路径")
        snapshot_require(Path(workspace["root"]) == self.runtime_context.workspace_root
                         and Path(workspace["root"]) == self.workspace_authority.primary_root,
                         "Workspace 身份变化")
        cwd = Path(workspace["cwd"])
        snapshot_require(cwd.is_dir() and self.workspace_authority.is_authorized(cwd),
                         "cwd 已不存在或不在当前授权范围")
        snapshot_text(workspace["content_digest"], "工作区内容摘要")
        snapshot_require(workspace["content_digest"] == self.workspace_state_guard.content_digest(),
                         "工作区内容在导出后发生变化，无法安全继续原调用")
        state = decode_run(snapshot["run"], _pre_model=_checkpoint_boundary == "before_model",
                           _approval=_checkpoint_boundary == "approval" and snapshot["schema_version"] in HITL_SNAPSHOT_VERSIONS)
        current_targets = self._pending_file_targets(state.tool_batch)
        snapshot_require(workspace.get("file_targets", {}) == current_targets
                         and all(value["kind"] not in {"unavailable", "other"} for value in current_targets.values()),
                         "文件目标前置状态已变化或无法核验，不能继续原调用")
        if linked:
            snapshot_fields(snapshot["checkpoint_binding"], {"database", "run_id"}, "Checkpoint 归属")
            snapshot_require(snapshot["checkpoint_binding"] == {"database": str(store.path), "run_id": state.run_id},
                             "Checkpoint 数据库或 Run 归属冲突")
        snapshot_require(state.workspace_checkpoint_digest in {None, workspace["content_digest"]},
                         "Run 的工作区前置条件与快照冲突")
        messages = decode_messages(snapshot["messages"], state, _pre_model=_checkpoint_boundary == "before_model")
        snapshot_require(type(snapshot["history"]) is dict
                         and snapshot["history"].get("maxlen") == self.attempt_history.maxlen,
                         "历史窗口不兼容")
        history = AttemptHistory.from_state(snapshot["history"])
        snapshot_require(all(0 < e.turn <= state.model_turns_used for e in history),
                         "治理历史轮次不属于已执行模型轮次")
        todo = TodoManager()
        snapshot_require(type(snapshot["todo"]) is list, "Todo 必须为列表")
        for item in snapshot["todo"]:
            snapshot_fields(item, {"content", "status", "activeForm"}, "Todo")
            for key, value in item.items():
                snapshot_text(value, f"Todo {key}", nonempty=True)
        todo.update(snapshot["todo"])
        snapshot_require(todo.items == snapshot["todo"], "Todo 数据不得被隐式修补")
        snapshot_text(snapshot["agent_note"], "Agent Note")
        snapshot_require(len(snapshot["agent_note"]) <= AgentNote.MAX_CHARS, "Note 超出上限")
        snapshot_require(snapshot["memory"] == {"strategy": "rebuild"}, "文件记忆策略不兼容")
        context = snapshot["context"]
        snapshot_fields(context, {"user_prompt", "compression_notice_pending", "last_assistant_note"}, "Context")
        snapshot_text(context["user_prompt"], "用户任务")
        snapshot_require(type(context["compression_notice_pending"]) is bool, "压缩提示类型错误")
        snapshot_require(context["last_assistant_note"] is None
                         or type(context["last_assistant_note"]) is str, "Assistant Note 类型错误")
        diagnostics = snapshot["diagnostics"]
        snapshot_fields(diagnostics, {"source_session_id", "source_trace_task_id", "metrics"}, "Diagnostics")
        snapshot_text(diagnostics["source_session_id"], "原 Session ID", nonempty=True)
        snapshot_require(diagnostics["source_trace_task_id"] == state.trace_task_id, "原 Trace ID 不一致")
        metrics = diagnostics["metrics"]
        snapshot_fields(metrics, set(self._empty_usage_metrics()), "Metrics")
        snapshot_require(all(type(v) in {int, float} and v >= 0 for v in metrics.values())
                         and metrics["turns"] == state.model_turns_used, "用量指标不一致")
        return snapshot, state, messages, todo, history, cwd

    def export_run_snapshot(self) -> dict:
        """Export only a controlled PAUSED boundary; never an executing handler."""
        state = self._run_state
        snapshot_require(state is not None and state.status is RunStatus.PAUSED,
                         "只能导出安全边界暂停中的 Run")
        snapshot_require(not self.background.list(), "后台任务表不属于本快照契约")
        snapshot = self._build_run_snapshot_data(state)
        self._validate_run_snapshot(snapshot)  # Refuse inconsistent live state, never repair it.
        return snapshot

    def _build_run_snapshot_data(self, state: RunState) -> dict:
        """Data construction only; each caller must validate its explicit boundary."""
        from dataclasses import asdict
        snapshot = json_copy({
            "schema_version": SCHEMA_VERSION,
            "workspace": {"root": str(self.runtime_context.workspace_root),
                          "cwd": str(self.runtime_context.cwd.resolve()),
                          "content_digest": self.workspace_state_guard.content_digest(),
                          "file_targets": self._pending_file_targets(state.tool_batch)},
            "compatibility": self._snapshot_compatibility(),
            "run": encode_run(state),
            "messages": [asdict(message) for message in self.messages],
            "history": self.attempt_history.export_state(),
            "todo": self.todo.items,
            "agent_note": self.agent_note.read(),
            "memory": {"strategy": "rebuild"},
            "context": {"user_prompt": self._current_user_prompt,
                        "compression_notice_pending": self._compression_notice_pending,
                        "last_assistant_note": self._last_assistant_note},
            "diagnostics": {"source_session_id": self.session_recorder.session_id,
                            "source_trace_task_id": state.trace_task_id,
                            "metrics": self.last_metrics},
        })
        store = getattr(self, "_checkpoint_store", None)
        if store is not None:
            snapshot["schema_version"] = HITL_SNAPSHOT_VERSION if getattr(self, "_hitl_enabled", False) else CHECKPOINT_SNAPSHOT_VERSION
            snapshot["checkpoint_binding"] = {"database": str(store.path), "run_id": state.run_id}
        return snapshot

    def _pending_file_targets(self, batch):
        from core.runtime_context.file_checkpoint import pending_file_targets
        trusted = {}
        for name in ("write_file", "edit_file"):
            spec = self.tool_registry.get_spec(name)
            handler = getattr(spec.handler, "__func__", None) if spec else None
            trusted[name] = (handler is getattr(MiniClaudeAgent, "_handle_" + name)
                and getattr(getattr(self.tools, name), "__func__", None) is getattr(BaseTools, name))
        return pending_file_targets(batch, self.tools, trusted)

    def restore_run_snapshot(self, snapshot: dict, *, _checkpoint_boundary: str | None = None, _refresh_waiting: bool = False) -> None:
        """Install validated detached state; caller explicitly continues the paused Run."""
        refreshing = (_refresh_waiting and _checkpoint_boundary == "approval"
            and self._run_state is not None and self._run_state.status is RunStatus.WAITING_APPROVAL
            and self._run_state.run_id == snapshot["run"]["run_id"])
        snapshot_require(not self.has_active_run or refreshing, "已有未完成 Run，不能被快照覆盖")
        snapshot_require(not self.background.list(), "恢复目标有独立后台任务")
        data, state, messages, todo, history, cwd = self._validate_run_snapshot(snapshot, _checkpoint_boundary=_checkpoint_boundary)
        from core.runtime_context.file_checkpoint import require_resumable_scope
        if _checkpoint_boundary != "approval":
            # Preserve the existing PREPARED + explicit approval protocol.
            # UNKNOWN calls are rejected by recover() before this installation.
            require_resumable_scope(state.tool_batch)
        if data["schema_version"] in BOUND_SNAPSHOT_VERSIONS:
            store = getattr(self, "_checkpoint_store", None)
            snapshot_require(_checkpoint_boundary is not None and store.holds_lock(state.run_id),
                             "持久化快照必须通过 Checkpoint 恢复入口并持有执行锁")
            record = store.load(state.run_id)
            snapshot_require(record["snapshot"] == data and record["boundary"] == _checkpoint_boundary
                             and record["status"] in {"active", "paused", "waiting_approval"}, "不能导入陈旧或未提交的持久化快照")
        seen_ids = getattr(self, "_seen_run_ids", set())
        snapshot_require(refreshing or (state.run_id not in seen_ids
                         and (self._run_state is None or self._run_state.run_id != state.run_id)),
                         "本实例已经持有或执行过该 Run，拒绝回退重放")
        # Prepare fallible components before publishing; old state survives IO failure.
        trace = TraceManager(trace_dir=self.data_paths.traces)
        source_trace_id = state.trace_task_id
        state.trace_task_id = self._create_run_trace(
            trace, state, data["context"]["user_prompt"], resumed_from=source_trace_id)
        state.trace_started = True
        state.workspace_checkpoint_digest = data["workspace"]["content_digest"]
        if state.has_pending_tool_batch:
            # This is a partial diagnostic turn, not another model request.
            trace.start_turn(state.tool_batch.model_iteration)
            trace.set_message_count(len(messages))
        note = AgentNote(self.data_paths.root)
        note.replace(data["agent_note"])
        memory = StructuredContextMemory(max_recent_files=self.memory.max_recent_files,
            max_observations=self.memory.max_observations, max_render_chars=self.memory.max_render_chars)
        restored_seen_ids = seen_ids | {state.run_id}
        # No handler, observer reset or user-input append occurs in this commit section.
        self.messages = messages
        self.todo = todo
        self.agent_note = note
        self.attempt_history = history
        self.runtime_policy.history = history
        self.memory = memory
        self._memory_file_fingerprints = {}
        self.runtime_context.shell_session.cwd = cwd
        self.trace = trace
        self.runtime_context.current_task_id = state.trace_task_id
        self._current_user_prompt = data["context"]["user_prompt"]
        self._compression_notice_pending = data["context"]["compression_notice_pending"]
        self._last_assistant_note = data["context"]["last_assistant_note"]
        self.last_metrics = data["diagnostics"]["metrics"]
        self._seen_run_ids = restored_seen_ids
        self._run_state = state
        self.session_recorder.record("run_snapshot_restored", run_id=state.run_id,
            trace_task_id=state.trace_task_id, source_trace_task_id=source_trace_id,
            source_session_id=data["diagnostics"]["source_session_id"])

    def enable_checkpoints(self, path: Path | None = None, *, require_existing: bool = False) -> None:
        """Opt in before a Run. Normal CLI/Mock/Benchmark behavior stays in-memory."""
        if self.has_active_run or getattr(self, "_checkpoint_store", None) is not None:
            raise CheckpointError("只能在没有活动 Run 且尚未启用时配置 Checkpoint")
        database = Path(path or self.data_paths.checkpoints).resolve()
        if database.is_relative_to(self.workdir.resolve()):
            raise CheckpointError("Checkpoint 数据库必须位于用户工作区之外")
        if self.background.list():
            raise CheckpointError("已有后台任务表，不能启用 Checkpoint")
        self._checkpoint_store = SqliteCheckpointStore(database, require_existing=require_existing)
        self._checkpoint_run_id = None
        self._checkpoint_revision = None
        self._checkpoint_faulted = False

    def _checkpoint_claim(self, *, creating: bool = False) -> None:
        store = getattr(self, "_checkpoint_store", None)
        if store is None:
            return
        state = self._run_state
        if store.holds_lock(state.run_id):
            return
        conversation_id = getattr(self, "_conversation_id", None)
        if creating and conversation_id is None:
            conversation_id = str(uuid.uuid4())
            self._conversation_id = conversation_id
            self._conversation_head_run_id = None
        if conversation_id and not store.holds_lock("conversation:" + conversation_id):
            store.acquire("conversation:" + conversation_id)
        try:
            store.acquire(state.run_id)
            if creating:
                revision = store.create_run(state.run_id, conversation_id=conversation_id,
                    workspace=str(self.workdir.resolve()), title=self._current_user_prompt,
                    expected_head=getattr(self, "_conversation_head_run_id", None))
                self._conversation_head_run_id = state.run_id
                self._checkpoint_run_id = state.run_id
                self._checkpoint_revision = revision
            else:
                record = store.load(state.run_id)
                if (record["revision"] != self._checkpoint_revision
                        or record["status"] in CHECKPOINT_TERMINAL):
                    raise CheckpointError("Checkpoint 已被推进或终结，旧内存状态不能继续")
        except BaseException:
            if creating:
                self._checkpoint_faulted = True
            store.release(state.run_id)
            if conversation_id:
                store.release("conversation:" + conversation_id)
            raise

    def _release_conversation_lock(self):
        store = getattr(self, "_checkpoint_store", None)
        identity = getattr(self, "_conversation_id", None)
        if store and identity:
            store.release("conversation:" + identity)

    def list_conversations(self, *, archived=False):
        if getattr(self, "_checkpoint_store", None) is None:
            raise CheckpointError("会话历史要求启用 Checkpoint")
        return self._checkpoint_store.list_conversations(str(self.workdir.resolve()), archived=archived)

    def open_conversation(self, conversation_id):
        store = self._checkpoint_store
        row = store.get_conversation(conversation_id)
        snapshot_require(row["workspace"] == str(self.workdir.resolve()) and row["archived_at"] is None,
                         "会话不属于当前 Workspace 或已归档")
        if self.has_active_run and getattr(self, "_conversation_id", None) != conversation_id:
            raise CheckpointError("已有活动 Run，不能切换会话")
        store.acquire("conversation:" + conversation_id)
        try:
            row = store.get_conversation(conversation_id)
            self._conversation_id = conversation_id
            self._conversation_head_run_id = row["head_run_id"]
            store.touch_conversation(conversation_id)
            result = self.open_checkpoint_conversation(row["head_run_id"])
            return result
        finally:
            self._release_conversation_lock()

    def start_new_conversation(self):
        if self.has_active_run or self.has_unfinished_conversation:
            raise CheckpointError("当前任务未完成；请保持暂停或先归档")
        self._conversation_id = None
        self._conversation_head_run_id = None
        self.messages.clear()
        self._run_state = None

    def archive_checkpoint(self, run_id, reason):
        """Detach from an operation without rewriting its execution facts."""
        if self.has_active_run and self._run_state.run_id != run_id:
            raise CheckpointError("不能在另一个 Run 活动时归档")
        store = self._checkpoint_store
        identity = store.conversation_for_run(run_id)
        if identity:
            store.acquire("conversation:" + identity)
        try:
            store.acquire(run_id)
            try:
                record = store.recover(run_id)
                if record["snapshot"] is None:
                    conversation = store.get_conversation(identity) if identity else None
                    snapshot_require(conversation is not None and
                        conversation["workspace"] == str(self.workdir.resolve()) and
                        record["status"] == "started" and not record["journal"], "归档工作区不一致")
                else:
                    snapshot_require(record["snapshot"]["workspace"]["root"] == str(self.workdir.resolve()), "归档工作区不一致")
                store.archive_run(run_id, reason)
            finally:
                store.release(run_id)
        finally:
            if identity:
                store.release("conversation:" + identity)
        if getattr(self, "_conversation_id", None) == identity or (self._run_state and self._run_state.run_id == run_id):
            self._run_state = None
            self._conversation_id = None
            self._conversation_head_run_id = None
            self._checkpoint_faulted = False
            self.messages.clear()
        return "已归档；原执行事实和审批记录保留，原操作不能恢复。可以开始新会话。"

    def _checkpoint_save(self, boundary: str) -> None:
        store = getattr(self, "_checkpoint_store", None)
        if store is None:
            return
        try:
            if self.background.list():
                raise CheckpointError("Checkpoint 不支持运行中的独立后台任务")
            from copy import deepcopy
            state = deepcopy(self._run_state)
            if boundary == "before_model":
                if state.has_pending_tool_batch or (state.tool_batch and not state.tool_batch.finalized):
                    raise CheckpointError("未完成的批次不能进入模型 Checkpoint 边界")
                # Finished history is in messages/journal; no live batch at this boundary.
                state.tool_batch = None
            state.status = RunStatus.PAUSED
            state.pause_requested = False
            if state.tool_batch and state.tool_batch.has_pending_calls:
                state.tool_batch.status = ToolBatchStatus.PAUSED
            data = self._build_run_snapshot_data(state)
            self._validate_run_snapshot(data, _checkpoint_boundary=boundary)
            self._checkpoint_revision = store.save_snapshot(state.run_id,
                self._checkpoint_revision, data, paused=self._run_state.status is RunStatus.PAUSED,
                boundary=boundary)
        except BaseException:
            self._checkpoint_faulted = True
            raise

    def _checkpoint_dispatch(self, name: str, args: Dict[str, Any]) -> None:
        store = getattr(self, "_checkpoint_store", None)
        if store is None:
            return
        try:
            state = self._run_state
            if self._checkpoint_faulted or state is None or state.status is not RunStatus.RUNNING or not state.has_pending_tool_batch or self._checkpoint_run_id != state.run_id:
                raise CheckpointError("持久化模式的 handler 必须通过当前 Run 批次执行")
            batch = state.tool_batch
            call = batch.tool_calls[batch.next_call_index]
            function = call["function"]
            if function["name"] != name or json.dumps(json.loads(function["arguments"]), sort_keys=True) != json.dumps(args, sort_keys=True):
                raise CheckpointError("实际 handler 参数与原 Tool Call 不一致")
            if name in {"write_file", "edit_file"}:
                record = store.load(state.run_id)
                expected = record["snapshot"]["workspace"].get("file_targets", {}).get(call["id"])
                current = self._pending_file_targets(batch).get(call["id"])
                if expected != current:
                    raise CheckpointError("文件目标前置状态在 dispatch 前变化，禁止执行原调用")
            if state.approval_id is not None:
                decision = self._hitl_decision(call)
                self._checkpoint_revision = self._approval_store.consume_and_mark_running(
                    state.approval_id, decision, self._checkpoint_revision)
            else:
                self._checkpoint_revision = store.mark_running(state.run_id,
                    self._checkpoint_revision, batch.model_iteration, batch.next_call_index,
                    batch.pending_call_ids[0], call=call)
        except BaseException:
            self._checkpoint_faulted = True
            raise

    def enable_hitl(self) -> None:
        """Opt in before a Run; durable approvals never degrade to memory-only."""
        if self.has_active_run or getattr(self, "_checkpoint_store", None) is None:
            raise CheckpointError("HITL 要求先启用 SQLite Checkpoint，且当前没有活动 Run")
        if getattr(self, "_hitl_enabled", False):
            raise CheckpointError("HITL 已启用")
        self._hitl_enabled = True
        self._permission_policy = DefaultPermissionPolicy.for_agent(self)
        self._approval_store = ApprovalStore(self._checkpoint_store, self._permission_policy)
        from core.permission_grants import PermissionGrants
        self._permission_grants = PermissionGrants(self._checkpoint_store, self.tool_registry)

    def remember_approval(self, run_id, approval_id, scope):
        store = self._checkpoint_store
        store.acquire(run_id)
        try:
            record = self.get_pending_approval(run_id)
            if record.approval_id != approval_id or record.state is not ApprovalStatus.APPROVED:
                raise CheckpointError("只能为刚刚批准的当前操作保存规则")
            operation = record.decision.operation
            if self._permission_policy.evaluate(operation) != record.decision:
                raise CheckpointError("操作上下文已变化，不能保存旧授权")
            expiry = record.to_dict()["expires_at"]
            if expiry is not None and self._approval_store.clock() >= expiry:
                raise CheckpointError("审批已过期，不能保存授权")
            if record.decision.rule_id == "file.sensitive":
                raise CheckpointError("敏感文件只能单次确认")
            self._permission_grants.remember(operation, scope)
        finally:
            store.release(run_id)

    def list_permissions(self):
        return self._permission_grants.list(str(self.workdir.resolve()))

    def get_session_info(self):
        """Read-only identity and resolved storage view for the terminal UI."""
        state = self._run_state
        store = getattr(self, "_checkpoint_store", None)
        conversation = getattr(self, "_conversation_id", None)
        head = getattr(self, "_conversation_head_run_id", None)
        if state and head and state.run_id != head:
            state = None
        record = store.load(head) if store and head else None
        unknown_ids = [call["call_id"] for call in record["journal"] if call["state"] == "UNKNOWN"] if record else []
        title = store.get_conversation(conversation)["title"] if store and conversation else "新会话"
        return {"conversation_id": conversation, "title": title,
                "run_id": state.run_id if state else head,
                "status": state.status.value if state else record["status"] if record else "ready",
                "execution_blocked": bool(unknown_ids or (record and record["status"] == "unknown")),
                "unknown_call_ids": unknown_ids,
                "trace_id": state.trace_task_id if state else None,
                "recorder_id": self.session_recorder.session_id,
                "workspace": str(self.workdir.resolve()), "cwd": str(self.runtime_context.cwd),
                "checkpoint_db": str(store.path) if store else None,
                "data_root": str(self.data_paths.root.resolve()),
                "session_log": str(self.session_recorder.path.resolve()),
                "traces": str(self.data_paths.traces.resolve()),
                "hitl": getattr(self, "_hitl_enabled", False)}

    def get_conversation_preview(self, limit=6):
        """Reference history only; no tool results or execution replay."""
        return [{"role": m.role, "content": m.content} for m in self.messages
                if m.role in {"user", "assistant"} and m.content][-limit:]

    def revoke_permission(self, grant_id):
        return self._permission_grants.revoke(str(self.workdir.resolve()), grant_id)

    def get_checkpoint_info(self, run_id: str):
        """Read-only CLI view; neither claims a Run nor repairs its journal."""
        if not getattr(self, "_hitl_enabled", False):
            raise CheckpointError("未启用持久化 HITL")
        record = self._checkpoint_store.load(run_id)
        snapshot = record["snapshot"]
        calls = []
        for call in record["journal"]:
            function = json.loads(call["call_json"])["function"]
            try:
                arguments = json.loads(function["arguments"])
            except json.JSONDecodeError:
                # Journal preserves malformed original input for inspection too.
                arguments = function["arguments"]
            execution = None
            if call["result_json"] is not None:
                fact = decode_tool_result(json.loads(call["result_json"]))
                execution = {"result": fact.result, "effect": fact.effect}
            elif call["state"] == "UNKNOWN":
                execution = {"result": "UNKNOWN", "effect": "UNCERTAIN"}
            calls.append({"call_id": call["call_id"], "state": call["state"],
                          "tool_name": function["name"], "arguments": arguments,
                          "execution": execution,
                          "file_evidence": (self.get_file_recovery_evidence(run_id, call["call_id"], turn=call["turn"])
                              if call["state"] == "UNKNOWN" and function["name"] in {"write_file", "edit_file"} else None)})
        return {"run_id": run_id, "status": record["status"], "checked_at": self._approval_store.clock(),
            "conversation_id": self._checkpoint_store.conversation_for_run(run_id),
            "archive": self._checkpoint_store.archive_info(run_id),
            "title": snapshot["context"]["user_prompt"] if snapshot else "未完成初始化",
            "workspace": snapshot["workspace"]["root"] if snapshot else None,
            "cwd": snapshot["workspace"]["cwd"] if snapshot else None,
            "result": record["final_result"],
            "calls": calls,
            "approval_id": snapshot["run"].get("approval_id") if snapshot else None,
            "approvals": self._approval_store.list_for_run(run_id)}

    def get_file_recovery_evidence(self, run_id, call_id, *, turn=None):
        """Observe an UNKNOWN file operation without modifying its durable facts."""
        from core.runtime_context.file_checkpoint import file_fingerprint, classify_file_observation
        store = self._checkpoint_store
        result = {"run_id": run_id, "call_id": call_id, "classification": "UNVERIFIABLE",
                  "before": None, "expected_sha256": None, "current": None}
        if store.holds_lock(run_id):
            return result  # Do not inspect a potentially active handler in this thread.
        try:
            store.acquire(run_id)
        except CheckpointError:
            return result
        try:
            record = store.load(run_id)
            snapshot = record["snapshot"]
            if snapshot is None or snapshot["workspace"]["root"] != str(self.workdir.resolve()):
                return result
            matches = [row for row in record["journal"] if row["call_id"] == call_id and
                       (turn is None or row["turn"] == turn)]
            if len(matches) != 1 or matches[0]["state"] != "UNKNOWN":
                return result
            call = matches[0]
            entry = next((entry for entry in record["file_evidence"] if
                (entry["turn"], entry["ordinal"]) == (call["turn"], call["ordinal"])), None)
            if entry is None:
                return result
            prepared = json.loads(entry["evidence_json"])
            result.update(before=prepared["before"], expected_sha256=prepared["expected_sha256"],
                          turn=call["turn"], ordinal=call["ordinal"])
            try:
                function = json.loads(call["call_json"])["function"]
                args = json.loads(function["arguments"])
                path = self.tools.safe_path(args["path"])
                if str(path) != prepared["before"]["path"]:
                    return result
                current = file_fingerprint(path)
            except (ValueError, TypeError, KeyError, OSError):
                return result
            result["current"] = current
            result["classification"] = classify_file_observation(prepared, current)
            return result
        finally:
            store.release(run_id)

    def list_checkpoint_runs(self):
        if not getattr(self, "_hitl_enabled", False):
            raise CheckpointError("未启用持久化 HITL")
        return [self.get_checkpoint_info(run_id) for run_id in self._checkpoint_store.list_run_ids()]

    def _load_terminal_history(self, record) -> str:
        """Load validated reference messages, never execution state or authority."""
        snapshot = record['snapshot']
        if snapshot is None:
            raise CheckpointError('该任务没有完整对话快照；只能检查记录或归档后重新规划')
        snapshot_require(snapshot['workspace']['root'] == str(self.workdir.resolve())
            and self.workspace_authority.primary_root == self.workdir.resolve(), '会话不属于当前 Workspace')
        state = decode_run(snapshot['run'], _terminal=True)
        messages = decode_messages(snapshot['messages'], state)
        note = snapshot['agent_note']
        snapshot_text(note, 'Agent Note')
        snapshot_require(len(note) <= AgentNote.MAX_CHARS, 'Note 超出上限')
        # Keep cwd, Todo, policy budgets and ToolBatchState out of this view.
        self.agent_note.replace(note)
        self.messages = messages
        self.session_recorder.record('conversation_loaded', run_id=record['run_id'])
        if any(call['state'] == 'UNKNOWN' for call in record['journal']):
            return (f'已加载会话只读历史（{record["status"].upper()}，含 UNKNOWN）。\n'
                    'Run 已结束，但有调用结果不确定；禁止重放工具或覆盖原任务。\n'
                    '可用 /status 或 /approval ' + record['run_id'] + ' 检查记录；'
                    '人工核验后可 /archive <原因>，再开始新会话。')
        return f'已加载会话上下文（{record["status"].upper()}），可继续输入新任务。\n' + (record['final_result'] or '')

    def open_checkpoint_conversation(self, run_id: str) -> str:
        """加载完成会话的上下文；未完成任务仍走严格执行恢复协议。"""
        store = getattr(self, '_checkpoint_store', None)
        if self.has_active_run and self._run_state.run_id == run_id and self._run_state.status is RunStatus.WAITING_APPROVAL:
            return self.resume_checkpoint(run_id)
        if store is None or self.has_active_run:
            raise CheckpointError('已有活动 Run 或未启用 Checkpoint，不能切换会话')
        identity = store.conversation_for_run(run_id)
        acquired_conversation = bool(identity and not store.holds_lock("conversation:" + identity))
        if acquired_conversation:
            store.acquire("conversation:" + identity)
        try:
            store.acquire(run_id)
            try:
                conversation = store.get_conversation(identity) if identity else None
                if conversation:
                    snapshot_require(conversation["workspace"] == str(self.workdir.resolve())
                        and conversation["archived_at"] is None and conversation["head_run_id"] == run_id,
                        "Workspace / 会话归属已变化、已归档或 Run 不是当前 head")
                record = store.load(run_id)
                if record['status'] not in {'completed', 'terminated', 'failed', 'cancelled'}:
                    completed = False
                else:
                    completed = True
                    result = self._load_terminal_history(record)
                    self._run_state = None
                    self._conversation_id = identity
                    self._conversation_head_run_id = run_id
            finally:
                store.release(run_id)
        finally:
            if acquired_conversation:
                store.release("conversation:" + identity)
        if not completed:
            return self.resume_checkpoint(run_id)
        return result

    def get_pending_approval(self, run_id: str):
        if not getattr(self, "_hitl_enabled", False):
            raise CheckpointError("未启用持久化 HITL")
        record = self._checkpoint_store.load(run_id)
        if record["boundary"] != "approval" or record["status"] != "waiting_approval":
            raise CheckpointError("当前 Run 没有可处理的等待审批")
        return self._approval_store.get(record["snapshot"]["run"]["approval_id"])

    def decide_approval(self, run_id: str, approval_id: str, *, approved: bool, actor: str, feedback=None):
        """Audit a caller decision; no handler, resume or thread waiting here."""
        store = self._checkpoint_store
        store.acquire(run_id)
        try:
            if store.archive_info(run_id):
                raise CheckpointError("Run 已归档，不能继续审批")
            record = self.get_pending_approval(run_id)
            if record.approval_id != approval_id:
                raise CheckpointError("审批 ID 不属于当前 Run 的待处理调用")
            return self._approval_store.decide(approval_id, approved=approved, actor=actor, feedback=feedback)
        finally:
            store.release(run_id)

    @staticmethod
    def _approval_wait_message(approval) -> str:
        return f"任务等待审批：approval_id={approval.approval_id}，工具={approval.decision.operation.tool_name}。"

    def resume_checkpoint(self, run_id: str) -> str:
        """Claim -> inspect approval/journal -> install -> existing batch loop."""
        store = getattr(self, "_checkpoint_store", None)
        same_wait = bool(self._run_state and self._run_state.run_id == run_id
                         and self._run_state.status is RunStatus.WAITING_APPROVAL)
        if store is None or (self.has_active_run and not same_wait):
            raise CheckpointError("恢复要求启用 Checkpoint，且当前没有其他活动 Run")
        identity = store.conversation_for_run(run_id)
        if identity:
            if not store.holds_lock("conversation:" + identity):
                store.acquire("conversation:" + identity)
        try:
            store.acquire(run_id)
        except BaseException:
            if identity:
                store.release("conversation:" + identity)
            raise
        try:
            if store.archive_info(run_id):
                raise CheckpointError("Run 已归档，不能恢复原操作")
            if identity and store.get_conversation(identity)["head_run_id"] != run_id:
                raise CheckpointError("这不是会话当前 Run；请通过会话入口加载最新上下文")
            if identity:
                self._conversation_id = identity
                self._conversation_head_run_id = run_id
            record = store.recover(run_id)
            if record["status"] == "unknown":
                raise CheckpointError("UNKNOWN：存在无法确认的工具副作用，必须人工检查，禁止自动重试")
            if record["status"] in CHECKPOINT_TERMINAL:
                if any(call["state"] == "UNKNOWN" for call in record["journal"]):
                    return self._load_terminal_history(record)
                return record["final_result"] or ""
            if record["snapshot"] is None:
                raise CheckpointError("尚未提交完整运行边界；初始化或模型响应无法原样恢复")
            approval = None
            if record["boundary"] == "approval":
                if not getattr(self, "_hitl_enabled", False):
                    raise CheckpointError("等待审批的 Run 必须通过 HITL 恢复")
                approval = self._approval_store.get(record["snapshot"]["run"]["approval_id"])
                if approval.state in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED} and approval.to_dict()["expires_at"] is not None and self._approval_store.clock() >= approval.to_dict()["expires_at"]:
                    self._approval_store.invalidate(approval.approval_id, "审批已过期")
                    raise CheckpointError("审批已过期并失效")
                if approval.state is ApprovalStatus.PENDING and same_wait:
                    return self._approval_wait_message(approval)
                if approval.state not in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
                    raise CheckpointError("审批已失效或消费，不能继续原操作")
            try:
                self.restore_run_snapshot(record["snapshot"], _checkpoint_boundary=record["boundary"], _refresh_waiting=same_wait)
            except ValueError:
                if approval and approval.state in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}:
                    self._approval_store.invalidate(approval.approval_id, "恢复时工作区 / Runtime 上下文不兼容")
                raise
            self._checkpoint_run_id = run_id
            self._checkpoint_revision = record["revision"]
            self._checkpoint_faulted = False
            if approval and approval.state is ApprovalStatus.PENDING:
                # A fresh instance now owns an in-memory WAITING lifecycle, so
                # ordinary user input cannot silently replace the pending Run.
                return self._approval_wait_message(approval)
            if approval:
                # Approval is still authoritative. Only _checkpoint_dispatch consumes it.
                self._run_state.status = RunStatus.PAUSED
            self.session_recorder.record("checkpoint_restored", run_id=run_id,
                revision=record["revision"], trace_task_id=self._run_state.trace_task_id)
            return self._continue_run()
        finally:
            store.release(run_id)
            if identity:
                store.release("conversation:" + identity)

    def _create_run_trace(self, trace, state, prompt: str, *, resumed_from: str | None = None) -> str:
        """Create a diagnostic segment without resetting execution/governance state."""
        request_config = {key: getattr(self.config.llm, key) for key in
                          ("provider", "model", "max_tokens", "temperature", "stream")}
        request_config["run_id"] = state.run_id
        if resumed_from is not None:
            request_config["resumed_from_trace_task_id"] = resumed_from
        return trace.start_task(user_prompt=prompt,
            workspace_root=str(self.runtime_context.workspace_root),
            workspace_confirmed=self._workspace_confirmed,
            require_tool_call=state.require_tool_call, environment=self.preflight.to_dict(),
            request_config=request_config)

    @property
    def has_unfinished_conversation(self) -> bool:
        # A failed restore can leave a selected durable Run without importing
        # its in-memory RunState. It still must not be cleared or overwritten.
        store = getattr(self, "_checkpoint_store", None)
        head = getattr(self, "_conversation_head_run_id", None)
        if store is None or head is None:
            return False
        record = store.load(head)
        return (record["status"] not in CHECKPOINT_TERMINAL or record["status"] == "unknown"
                or any(call["state"] == "UNKNOWN" for call in record["journal"]))

    @property
    def has_pending_tool_batch(self) -> bool:
        state = getattr(self, "_run_state", None)
        return bool(state and state.has_pending_tool_batch)

    @property
    def has_active_run(self) -> bool:
        state = getattr(self, "_run_state", None)
        return bool(state and state.status in {RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.WAITING_APPROVAL})

    def _request_run_pause(self) -> None:
        """Set an internal pause signal; the loop observes it after a call completes."""
        state = getattr(self, "_run_state", None)
        if state is None:
            raise RuntimeError("当前没有可暂停的运行")
        state.request_pause()

    def _continue_run(self) -> str:
        """Continue the active in-memory run without adding input or resetting state."""
        state = getattr(self, "_run_state", None)
        if state is None:
            return "错误: 当前没有可继续的运行。"
        if state.status is RunStatus.WAITING_APPROVAL:
            return self.resume_checkpoint(state.run_id)
        if state.status in {
            RunStatus.COMPLETED,
            RunStatus.TERMINATED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return state.terminal_result or ""
        if state.status is not RunStatus.PAUSED:
            return "错误: 当前运行未处于可继续的暂停状态。"

        self._checkpoint_claim()
        try:
            store = getattr(self, "_checkpoint_store", None)
            if store is not None:
                record = store.load(state.run_id)
                self._validate_run_snapshot(record["snapshot"], _checkpoint_boundary=record["boundary"])
                if record["boundary"] != "approval":
                    from core.runtime_context.file_checkpoint import require_resumable_scope
                    require_resumable_scope(state.tool_batch)
            # Grants and directories can change after snapshot validation or a pause.
            cwd = self.workspace_authority.check(str(self.runtime_context.cwd.resolve()))
            if not cwd.is_dir():
                raise ValueError("继续运行的 cwd 已不存在或不是目录")
            if state.workspace_checkpoint_digest is not None:
                snapshot_require(state.workspace_checkpoint_digest == self.workspace_state_guard.content_digest(),
                                 "工作区内容在导入后发生变化，无法安全继续原调用")
            state.resume()
            self.session_recorder.record(
                "run_continued",
                run_id=state.run_id,
                next_tool_call_id=(
                    state.tool_batch.pending_call_ids[0]
                    if state.tool_batch and state.tool_batch.pending_call_ids
                    else ""
                ),
            )
        except BaseException as exc:
            # Continue preparation failed before entering the cycle or dispatching.
            self._abort_run_initialization(state, exc, continuing=True)
            store = getattr(self, "_checkpoint_store", None)
            if store is not None:
                store.release(state.run_id)
                self._release_conversation_lock()
            raise
        result = self._llm_tool_cycle(
            max_iterations=state.max_iterations,
            require_tool_call=state.require_tool_call,
            _continuing=True,
        )
        if state.status not in {RunStatus.PAUSED, RunStatus.WAITING_APPROVAL}:
            self.session_recorder.record(
                "final",
                status="RESPONSE",
                run_id=state.run_id,
                content=result,
            )
        return result

    def _abort_run_initialization(
        self, state: RunState, error: BaseException, *, continuing: bool = False,
    ) -> None:
        """Bootstrap has not dispatched a tool; preserve or end its lifecycle."""
        if continuing:
            state.status = RunStatus.PAUSED
            if state.has_pending_tool_batch and state.tool_batch is not None:
                state.tool_batch.status = ToolBatchStatus.PAUSED
            return
        status = RunStatus.FAILED if isinstance(error, Exception) else RunStatus.CANCELLED
        state.finish(status, f"运行初始化失败: {error}")
        if state.trace_started:
            try:
                self.trace.end_task(status.value.upper())
            except Exception:
                logger.exception("运行初始化失败后的 Trace 收尾失败")

    def _finalize_tool_batch(self, batch: ToolBatchState) -> None:
        """Close deferred feedback separately from completing the last call."""
        if batch.has_pending_calls or batch.finalized:
            return
        while batch.pending_replan_messages:
            message = Message(role="user", content=batch.pending_replan_messages[0])
            try:
                self.messages.append(message)
            finally:
                # An interrupt may arrive after append actually succeeded.
                # Consume only feedback whose exact message is already present.
                if any(item is message for item in self.messages):
                    batch.pending_replan_messages.pop(0)
        batch.finalized = True
        if (getattr(self, "_checkpoint_store", None) is not None
                and batch is self._run_state.tool_batch
                and self._run_state.status in {RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.WAITING_APPROVAL}
                and all(r.outcome in PAUSED_RESULT_OUTCOMES for r in batch.results.values())):
            self._checkpoint_save("tools")

    def _finish_run(self, status: RunStatus, result: str) -> str:
        state = getattr(self, "_run_state", None)
        if state is not None:
            if state.has_pending_tool_batch and state.tool_batch is not None:
                state.tool_batch.mark_terminal(failed=status is RunStatus.FAILED)
            state.finish(status, result)
            store = getattr(self, "_checkpoint_store", None)
            if store is not None:
                try:
                    data = self._build_run_snapshot_data(state)
                    # Compression can remove a finished diagnostic batch from messages.
                    batch = state.tool_batch
                    if batch and batch.finalized and (batch.assistant_message_index >= len(self.messages)
                            or self.messages[batch.assistant_message_index].tool_calls != batch.tool_calls):
                        data["run"]["tool_batch"] = None
                    self._checkpoint_revision = store.finish(state.run_id,
                        self._checkpoint_revision, status.value, result, data)
                except BaseException:
                    self._checkpoint_faulted = True
                    raise
        return result

    def set_ui_event_handler(
        self, handler: Optional[Callable[[str, Dict[str, Any]], None]]
    ) -> None:
        """Attach a presentation callback without coupling the runtime to a UI."""
        self._ui_event_handler = handler

    def _emit_ui_event(self, event: str, **data: Any) -> None:
        if getattr(self, "_ui_event_handler", None) is None:
            return
        try:
            self._ui_event_handler(event, data)
        except Exception:
            logger.exception("UI event handler failed: %s", event)

    @staticmethod
    def _tool_event_summary(name: str, args: Dict[str, Any]) -> str:
        """Return a short human-readable summary for the terminal UI."""
        if not isinstance(args, dict):
            return ""
        if name == "bash":
            return str(args.get("command", ""))[:120].replace("\n", " ")
        if name == "run_background":
            return str(args.get("command", ""))[:120].replace("\n", " ")
        if name in {"read_file", "write_file", "edit_file", "list_files", "search_code"}:
            target = args.get("path") or args.get("pattern") or args.get("query")
            return str(target) if target else ""
        return ""

    def _register_tools(self) -> None:
        """Register each exposed tool schema together with its existing handler."""
        _platform_label = {
            'win32': 'Windows (CMD)',
            'linux': 'Linux (bash)',
            'darwin': 'macOS (zsh)',
        }.get(sys.platform, sys.platform)

        all_tools = [
            bash_spec(
                self._handle_bash,
                description=(
                    f'Run shell commands on {_platform_label} for builds/tests, system inspection, '
                    'binary analysis, and shell operations. For ordinary source-file reading or search, '
                    'prefer read_file or search_code for bounded observations. '
                    'Large output is saved under .agent/logs and only a preview is returned; '
                    'use read_file with line ranges on that path for omitted content.'
                ),
            ),
            {
                'name': 'run_background',
                'handler': self._handle_run_background,
                'description': (
                    f'Run a long-lived command asynchronously on {_platform_label}. '
                    'Use this for servers and development watchers that intentionally keep running '
                    '(for example Redis, Spring Boot, or a frontend dev server). '
                    'The tool returns immediately; verify readiness with bash afterward.'
                ),
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'command': {'type': 'string', 'description': 'The command to run asynchronously'},
                        'description': {'type': 'string', 'description': 'Short description of the background task'},
                        'timeout': {
                            'type': 'integer',
                            'description': (
                                'Compatibility field for the task record. Detached processes are not '
                                'automatically killed when this time is reached.'
                            ),
                            'default': 3600,
                        },
                    },
                    'required': ['command'],
                },
            },
            {
                'name': 'get_background_status',
                'handler': self._handle_get_background_status,
                'description': '查询后台任务的状态、PID、启动时间、工作目录和退出码。',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'job_id': {'type': 'string', 'description': '后台任务 ID'},
                    },
                    'required': ['job_id'],
                },
            },
            {
                'name': 'get_background_logs',
                'handler': self._handle_get_background_logs,
                'description': '读取后台任务最近的 stdout/stderr 行，不会把完整日志一次性放入上下文。',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'job_id': {'type': 'string', 'description': '后台任务 ID'},
                        'tail': {'type': 'integer', 'description': '每类输出读取的最近行数', 'default': 50},
                    },
                    'required': ['job_id'],
                },
            },
            {
                'name': 'stop_background',
                'handler': self._handle_stop_background,
                'description': '显式停止一个正在运行的后台任务。Agent 退出时不会自动调用此工具。',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'job_id': {'type': 'string', 'description': '后台任务 ID'},
                    },
                    'required': ['job_id'],
                },
            },
            {
                'name': 'health_check',
                'handler': self._handle_health_check,
                'description': '按本机端口或 HTTP URL 检查服务是否可用，可附带后台任务 ID。',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'job_id': {'type': 'string', 'description': '可选的后台任务 ID'},
                        'port': {'type': 'integer', 'description': '可选的本机 TCP 端口'},
                        'url': {'type': 'string', 'description': '可选的 HTTP/HTTPS 健康检查地址'},
                        'timeout': {'type': 'number', 'description': '检查超时时间（秒）', 'default': 3},
                    },
                    'required': [],
                },
            },
            read_file_spec(self._handle_read_file),
            write_file_spec(self._handle_write_file),
            edit_file_spec(self._handle_edit_file),
            {
                'name': 'update_agent_note',
                'handler': self._handle_update_agent_note,
                'description': '覆盖当前会话的简短工作笔记。记录用户明确约束、已验证的发现和待验证的假设；及时删去过期结论。笔记每轮都会提供给你，压缩前请保存会丢失的关键信息。',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'content': {'type': 'string', 'description': '完整 Markdown 笔记，最多 6000 字符；空字符串表示清空。'},
                    },
                    'required': ['content'],
                },
            },
            {
                'name': 'load_skill',
                'handler': self._handle_load_skill_dispatch,
                'description': 'Load specialized knowledge or instructions from a named skill module. Use this when you need domain-specific guidance.',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'name': {'type': 'string', 'description': 'Name of the skill module to load'},
                    },
                    'required': ['name'],
                },
            },
            {
                'name': 'task',
                'handler': self._handle_task,
                'description': 'Spawn an isolated subagent for exploration, research, or delegated work. The subagent runs independently with its own tool loop and returns a final summary.',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'prompt': {'type': 'string', 'description': 'Task description for the subagent'},
                        'agent_type': {
                            'type': 'string',
                            'enum': ['Explore', 'general-purpose'],
                            'description': 'Type of subagent: Explore (read-only search) or general-purpose (full tool access)',
                        },
                    },
                    'required': ['prompt'],
                },
            },
            {
                'name': 'search_code',
                'handler': self._handle_search_code,
                'description': (
                    'Search source and text files for line-oriented regular expressions and return matching lines with bounded context. '
                    'Patterns are evaluated one line at a time and do not match across lines. Binary files are not searched. '
                    'Use read_file for a known file or line range; use bash for binary inspection, system commands, builds, and tests.'
                ),
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'paths': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': 'Workspace file/directory paths or globs (for example ["*.py", "src/"]). Defaults to the workspace. .agent is omitted from broad searches; name .agent/logs explicitly to search saved command output.',
                        },
                        'patterns': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': 'Line-oriented regex patterns; a line is returned once if it matches any pattern.',
                        },
                        'context_lines': {
                            'type': 'integer',
                            'description': 'Number of context lines before and after each matching line (0-50). Default: 0.',
                            'minimum': 0,
                            'maximum': 50,
                            'default': 0,
                        },
                        'case_sensitive': {
                            'type': 'boolean',
                            'description': '是否区分大小写。默认 false',
                        },
                        'max_matches': {
                            'type': 'integer',
                            'description': 'Maximum matching lines to return. Default: 50; range: 1-200.',
                            'minimum': 1,
                            'maximum': 200,
                            'default': 50,
                        },
                        'include_filename': {
                            'type': 'boolean',
                            'description': '是否在输出中包含文件名。默认 true',
                        },
                        'include_line_number': {
                            'type': 'boolean',
                            'description': '是否在输出中包含行号。默认 true',
                        },
                    },
                    'required': ['patterns'],
                },
            },
            {
                'name': 'count_occurrences',
                'handler': self._handle_count_occurrences,
                'description': 'Count regex occurrences, not matching lines, across files and report totals with per-file counts. Use to verify renames/refactors; use search_code to inspect matching source lines.',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'paths': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': '文件或目录路径列表，支持通配符。不传则默认为项目根目录',
                        },
                        'patterns': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': '要统计的正则表达式列表（OR关系）',
                        },
                        'case_sensitive': {
                            'type': 'boolean',
                            'description': '是否区分大小写。默认 false',
                        },
                    },
                    'required': ['patterns'],
                },
            },
            # {
            #     'name': 'syntax_check',
            #     'description': '快速检查 Python 源文件是否存在语法错误。使用 ast.parse 进行轻量结构验证。用于 rename/refactor 后确认文件仍可被正确解析。',
            #     'input_schema': {
            #         'type': 'object',
            #         'properties': {
            #             'paths': {
            #                 'type': 'array',
            #                 'items': {'type': 'string'},
            #                 'description': '要检查的 Python 文件或目录路径列表',
            #             },
            #         },
            #         'required': ['paths'],
            #     },
            # },
            {
                'name': 'list_files',
                'handler': self._handle_list_files,
                'description': 'Browse project structure by listing files and directories with depth and ignore rules. Does not read file contents.',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'path': {
                            'type': 'string',
                            'description': '要列出的目录路径，默认为当前工作目录',
                        },
                        'max_depth': {
                            'type': 'integer',
                            'description': '递归深度。0=仅当前目录，1=含直接子目录，2=含子目录的子目录（默认）。仅在目录结构复杂时增加此值',
                        },
                        'max_files': {
                            'type': 'integer',
                            'description': '返回条目的最大数量，超出后截断提示。默认 200',
                        },
                    },
                    'required': [],
                },
            },
            {
                'name': 'TodoWrite',
                'handler': self._handle_todo_write,
                'description': (
                    'Maintain lightweight progress for a multi-step user task when useful. '
                    'Avoid maintaining todos for simple tasks. Max 20 items; only one '
                    'in_progress item is allowed.'
                ),
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'items': {
                            'type': 'array',
                            'maxItems': 20,
                            'items': {
                                'type': 'object',
                                'properties': {
                                    'content': {'type': 'string'},
                                    'status': {
                                        'type': 'string',
                                        'enum': ['pending', 'in_progress', 'completed'],
                                    },
                                    'activeForm': {'type': 'string'},
                                },
                                'required': ['content', 'status', 'activeForm'],
                            },
                        },
                    },
                    'required': ['items'],
                },
            },
        ]

        for tool in all_tools:
            if isinstance(tool, ToolSpec):
                self.tool_registry.register(tool)
                continue
            self.tool_registry.register(ToolSpec(
                name=tool['name'],
                description=tool['description'],
                input_schema=tool['input_schema'],
                handler=tool['handler'],
            ))

    def _get_llm_tools(self) -> List[Dict[str, Any]]:
        """Get registered tool definitions filtered by feature flags."""
        if not hasattr(self, 'tool_registry'):
            self.tool_registry = ToolRegistry()
            self._register_tools()
        return [tool for tool in self.feature_manager.filter_tools(self.tool_registry.definitions())
                if self._mode_capability_error(tool["name"]) is None]

    def _mode_capability_error(self, name, args=None):
        if getattr(self, "_checkpoint_store", None) is not None:
            command = (args or {}).get("command", "")
            if name in {"run_background", "task"} or (name == "bash" and sys.platform == "win32"
                    and isinstance(command, str) and re.match(r"^\s*start(?:\s|$)", command, re.I)):
                return "持久化模式暂不支持后台或子 Agent 执行，审批不能创建该能力"
        return None

    # ── Tool Handler Methods (bound in ToolRegistry) ──

    def _handle_bash(self, command: str) -> ToolResult:
        # Windows `start` launches another process/window but can still keep
        # the parent shell waiting when called through subprocess.run. Route
        # this narrow, explicit server-launch form through the background
        # executor so the Agent can continue to health-check the service.
        if sys.platform == "win32" and re.match(r"^\s*start(?:\s|$)", command, re.IGNORECASE):
            return self._queue_background_command(
                command,
                description=f"Windows start: {command[:80]}",
            )
        result = self.tools.run_bash(command)
        return result

    def _handle_run_background(self, command: str, description: str = "",
                               timeout: int = 3600) -> str | ToolResult:
        """Queue a long-lived command without blocking the LLM turn."""
        return self._queue_background_command(command, description, timeout)

    def _background_capability_error(self) -> Optional[str]:
        if not self.feature_manager.is_tool_enabled("run_background"):
            return "错误: 后台执行功能当前未启用"
        return None

    def _queue_background_command(self, command: str, description: str = "",
                                  timeout: int = 3600) -> str | ToolResult:
        """Submit a command to the shared background executor."""
        if getattr(self, "_checkpoint_store", None) is not None:
            return ToolResult(content="错误: Checkpoint 模式不支持后台任务执行或恢复", success=False, execution_success=False)
        capability_error = self._background_capability_error()
        if capability_error:
            return ToolResult(content=capability_error, success=False, execution_success=False)
        block_msg = self.command_policy.check(command)
        if block_msg:
            return block_msg
        task = self.background.launch(
            command,
            description=description,
            timeout=timeout,
            cwd=str(self.runtime_context.cwd),
        )
        return json.dumps(
            {
                "status": "async_launched" if task.pid else "launch_failed",
                "job_id": task.id,
                "pid": task.pid,
                "started_at": task.started_at,
                "cwd": task.cwd,
                "stdout_file": task.stdout_file,
                "stderr_file": task.stderr_file,
                "process_status": task.status.value,
                "error": task.error or None,
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _background_task_payload(task) -> Dict[str, Any]:
        return {
            "job_id": task.id,
            "status": task.status.value,
            "pid": task.pid,
            "command": task.command,
            "cwd": task.cwd,
            "started_at": task.started_at,
            "completed_at": task.completed_at,
            "exit_code": task.exit_code,
            "stdout_file": task.stdout_file,
            "stderr_file": task.stderr_file,
            "error": task.error or None,
        }

    def _handle_get_background_status(self, job_id: str) -> str:
        task = self.background.get(job_id)
        if not task:
            return json.dumps({"status": "not_found", "job_id": job_id}, ensure_ascii=False)
        return json.dumps(self._background_task_payload(task), ensure_ascii=False)

    def _handle_get_background_logs(self, job_id: str, tail: int = 50) -> str:
        logs = self.background.logs(job_id, tail=max(1, min(int(tail), 200)))
        if logs is None:
            return json.dumps({"status": "not_found", "job_id": job_id}, ensure_ascii=False)
        task = self.background.get(job_id)
        return self.tools.format_tool_output(
            json.dumps(logs, ensure_ascii=False),
            success=True,
            exit_code=task.exit_code if task else None,
        )

    def _handle_stop_background(self, job_id: str) -> str:
        stopped = self.background.stop(job_id)
        task = self.background.get(job_id)
        return json.dumps(
            {
                "job_id": job_id,
                "stopped": stopped,
                "status": task.status.value if task else "not_found",
            },
            ensure_ascii=False,
        )

    def _handle_health_check(self, job_id: str = "", port: int = None,
                             url: str = "", timeout: float = 3) -> str:
        checks: Dict[str, Any] = {}
        if job_id:
            task = self.background.get(job_id)
            checks["job"] = (
                self._background_task_payload(task)
                if task else {"job_id": job_id, "status": "not_found"}
            )

        if port is not None:
            try:
                with socket.create_connection(("127.0.0.1", int(port)), timeout=float(timeout)):
                    checks["port"] = {"port": int(port), "healthy": True}
            except OSError as exc:
                checks["port"] = {"port": int(port), "healthy": False, "error": str(exc)}

        if url:
            try:
                with urlopen(url, timeout=float(timeout)) as response:
                    checks["url"] = {
                        "url": url,
                        "healthy": 200 <= response.status < 400,
                        "status_code": response.status,
                    }
            except HTTPError as exc:
                checks["url"] = {
                    "url": url, "healthy": False, "status_code": exc.code,
                    "error": str(exc),
                }
            except (URLError, OSError) as exc:
                checks["url"] = {"url": url, "healthy": False, "error": str(exc)}

        health_checks = [value for key, value in checks.items() if key != "job" and isinstance(value, dict)]
        healthy = bool(health_checks) and all(item.get("healthy") is True for item in health_checks)
        return json.dumps({"healthy": healthy, "checks": checks}, ensure_ascii=False)

    def _handle_read_file(self, path: str, start_line: int = None,
                          end_line: int = None) -> ToolResult:
        return self.tools.read_file(path, start_line, end_line)

    def _handle_write_file(self, path: str, content: str) -> str:
        result = self.tools.write_file(path, content)
        return result.content

    def _handle_edit_file(self, path: str, edits: list) -> str:
        result = self.tools.edit_file(path, edits)
        return result.content

    def _handle_update_agent_note(self, content: str) -> ToolResult:
        if len(content) > AgentNote.MAX_CHARS:
            return ToolResult(
                f"错误: 笔记超过 {AgentNote.MAX_CHARS} 字符上限，请精简后重试。",
                success=False,
            )
        return ToolResult(self.agent_note.replace(content))

    def _handle_add_workdir(self, path: str) -> str:
        return self.tools.add_allowed_path(path)

    def _handle_load_skill_dispatch(self, name: str) -> str:
        """Wrapper: calls _load_skill_internal and extracts content string."""
        result = self._load_skill_internal(name)
        return result.content

    def _handle_task(self, prompt: str, agent_type: str = "general-purpose") -> str:
        """Spawn a subagent inside a **Shadow Workspace** with Two-Phase Commit.

        Architecture:
        1. Create an isolated shadow directory under ``.claude/shadow/<task_id>``.
        2. Mirror necessary context files from the main workdir into the shadow.
        3. Run the subagent with its *workdir* pointed at the shadow.
        4. On **success** → COMMIT: copy new/modified files back to main workdir.
        5. On **failure** → ROLLBACK: delete the entire shadow directory,
           roll back the LLM context to the pre-subagent checkpoint.
        """
        if getattr(self, "_checkpoint_store", None) is not None:
            return "错误: Checkpoint 模式不支持子 Agent 执行栈恢复"
        try:
            atype = SubAgentType(agent_type)
        except ValueError:
            atype = SubAgentType.GENERAL

        # ── Context snapshot ──────────────────────────────
        checkpoint = len(self.messages)

        # ── Create Shadow Workspace ────────────────────────
        shadow_root = self.data_paths.root / "shadow"
        shadow_root.mkdir(parents=True, exist_ok=True)
        task_id = str(uuid.uuid4())[:8]
        shadow_dir = shadow_root / task_id
        shadow_dir.mkdir(parents=True, exist_ok=True)

        # Mirror existing files so subagent can read context
        for p in self.workdir.iterdir():
            if p.is_file() and not p.name.startswith("."):
                try:
                    shutil.copy2(p, shadow_dir / p.name)
                except OSError:
                    pass

        # ── Execute subagent in shadow ────────────────────
        result = self.subagent_manager.run(
            prompt, agent_type=atype, workdir=shadow_dir,
        )

        if not result.success:
            # ── ROLLBACK ─────────────────────────────────
            # 1. Destroy shadow directory (no partial file leaks)
            shutil.rmtree(shadow_dir, ignore_errors=True)
            logger.warning(f"子代理回滚: 影子目录已销毁 {shadow_dir}")

            # 2. Context rollback
            if len(self.messages) > checkpoint:
                snipped = len(self.messages) - checkpoint
                self.messages = self.messages[:checkpoint]
                logger.warning(
                    f"子代理回滚: 丢弃了 {snipped} 条潜在脏消息"
                )

            # 3. Trace: record the rollback event
            self.trace.record_rollback()

            return (
                f"[子代理执行失败 — 上下文已回滚]\n"
                f"原因: {result.error or '未知错误'}\n"
                f"任务 ID: {result.task_id}\n"
                f"注意: 请勿重试此子代理调用。将失败原因告知用户并等待新指令。"
            )

        # ── COMMIT: merge shadow → main workdir ───────────
        committed = 0
        for p in shadow_dir.iterdir():
            if p.is_file():
                try:
                    shutil.copy2(p, self.workdir / p.name)
                    committed += 1
                except OSError:
                    pass
        if committed:
            logger.info(f"子代理提交: {committed} 个文件从影子目录合并到主工作区")

        # Clean up shadow after successful commit
        shutil.rmtree(shadow_dir, ignore_errors=True)

        # ── Enriched audit trail ──────────────────────────
        tokens_str = (
            f"输入 {result.usage.get('prompt_tokens', 0)} tokens, "
            f"输出 {result.usage.get('completion_tokens', 0)} tokens, "
            f"合计 {result.usage.get('total_tokens', 0)} tokens"
        )
        return (
            f"[子代理执行完成]\n"
            f"任务 ID: {result.task_id}\n"
            f"Token 统计: {tokens_str}\n"
            f"已提交文件: {committed} 个\n"
            f"━━━ 子代理输出 ━━━\n"
            f"{result.content}"
        )

    def _handle_todo_write(self, items: list) -> str:
        """Update the in-memory todo list silently and return a static stub.

        The rendered todo state is intentionally discarded — it is a
        highly dynamic payload that would shatter prefix-cache affinity
        if persisted as a tool message.  Use ``_get_dynamic_hot_context``
        to inject current todo status as temporary hot context instead.
        """
        self.todo.update(items)
        return "Todo state successfully updated in the system background."

    def _handle_search_code(self, paths: list = None, patterns: list = None,
                             context_lines: int = 0,
                             case_sensitive: bool = False,
                             max_matches: int = 50,
                             include_filename: bool = True,
                              include_line_number: bool = True) -> ToolResult:
        """Handle search_code tool calls — delegate to BaseTools."""
        if paths is None:
            paths = ["."]
        if patterns is None:
            return ToolResult("错误: 需要至少提供一个模式 (patterns)", success=False)
        return self.tools.search_code(
            paths=paths, patterns=patterns,
            context_lines=context_lines,
            case_sensitive=case_sensitive,
            max_matches=max_matches,
            include_filename=include_filename,
            include_line_number=include_line_number,
        )

    def _handle_count_occurrences(self, paths: list = None, patterns: list = None,
                                   case_sensitive: bool = False) -> ToolResult:
        """Handle count_occurrences tool calls — delegate to BaseTools."""
        if paths is None:
            paths = ["."]
        if patterns is None:
            return ToolResult("错误: 需要至少提供一个模式 (patterns)", success=False)
        return self.tools.count_occurrences(
            paths=paths, patterns=patterns,
            case_sensitive=case_sensitive,
        )

    # def _handle_syntax_check(self, paths: list) -> str:
    #     """Handle syntax_check tool calls — delegate to BaseTools with semantic wrapping."""
    #     result = self.tools.syntax_check(paths=paths)
    #     if result.success:
    #         return result.content + (
    #             "\n\n✅ [SYSTEM INFO] Python Interpreter validation passed. "
    #             "No syntax anomalies found. The modified files are structurally sound and safe."
    #         )
    #     else:
    #         return result.content + (
    #             "\n\n❌ [SYSTEM CRITICAL ALERT] SYNTAX ERROR DETECTED.\n"
    #             "Your last edit broke the Python compilation track. The code cannot be parsed.\n"
    #             "Action Required: Check the file path and line number in the error message "
    #             "above immediately. Use read_file or edit_file to fix the broken indent, "
    #             "unmatched brackets, or typos right now."
    #         )

    def _handle_list_files(self, path: str = ".", max_depth: int = 2,
                            max_files: int = 200) -> ToolResult:
        """Handle list_files tool calls — delegate to BaseTools."""
        return self.tools.list_files(
            path=path, max_depth=max_depth, max_files=max_files,
        )

    def _load_skill_internal(self, name: str) -> ToolResult:
        """Load skill content and format it for the LLM."""
        if not self.feature_manager.is_enabled('skills'):
            return ToolResult(content="错误: 技能系统未启用", success=False)
        content = self.skill_loader.get_skill_content(name)
        if content is None:
            available = ', '.join(s.name for s in self.skill_loader.get_all()) or '(无)'
            return ToolResult(
                content=f"未找到技能: {name}\n可用技能: {available}",
                success=False,
            )
        skill = self.skill_loader.get(name)
        header = f"<skill name=\"{skill.name}\""
        if skill.description:
            header += f" description=\"{skill.description}\""
        header += ">"
        return ToolResult(content=f"{header}\n{skill.content}\n</skill>")

    def _hitl_decision(self, call):
        state = self._run_state
        batch = state.tool_batch
        op = self._permission_policy.bind(state.run_id, batch.model_iteration,
            batch.next_call_index, call, workspace_digest=self.workspace_state_guard.content_digest(),
            runtime_digest=permission_digest(self._snapshot_compatibility()))
        return self._permission_policy.evaluate(op)

    def _hitl_gate(self, name, args):
        state = self._run_state
        if state is None or state.status is not RunStatus.RUNNING or not state.has_pending_tool_batch:
            raise CheckpointError("HITL handler 必须通过当前持久化 Run 批次")
        batch = state.tool_batch
        call = batch.tool_calls[batch.next_call_index]
        if call["function"]["name"] != name or json.loads(call["function"]["arguments"]) != args:
            raise CheckpointError("HITL 调用身份 / 参数与原调用不一致")
        if state.approval_id is not None:
            approval = self._approval_store.get(state.approval_id)
            if approval.state is ApprovalStatus.REJECTED:
                feedback = self._checkpoint_store.approval_feedback(state.approval_id)
                return ToolDispatchResult(ToolDispatchStatus.APPROVAL_REJECTED,
                    "[NOT_EXECUTED] 用户拒绝本操作；当前批次剩余调用不执行。原因：" + feedback["note"])
            if approval.state is not ApprovalStatus.APPROVED:
                raise CheckpointError("审批未批准、已失效或已消费")
            # Always consume/recheck an existing grant, even if policy now says DENY.
            return None
        decision = self._hitl_decision(call)
        if decision.kind is PermissionKind.DENY:
            return ToolDispatchResult(ToolDispatchStatus.PERMISSION_DENIED,
                f"[NOT_EXECUTED] 权限拒绝（{decision.rule_id}）：{decision.reason}")
        if decision.kind is PermissionKind.ALLOW:
            return None
        if decision.rule_id != "file.sensitive" and self._permission_grants.allows(decision.operation):
            return None
        # The current call was visited but has not been processed. Its signature
        # must not count as completed in the durable dedup prefix.
        batch.seen_signatures.discard((name, call["function"]["arguments"]))
        pending = copy.deepcopy(state)
        pending.status = RunStatus.WAITING_APPROVAL
        pending.pause_requested = False
        pending.tool_batch.status = ToolBatchStatus.PAUSED
        data = self._build_run_snapshot_data(pending)
        try:
            approval, revision = self._approval_store.request_wait(decision, self._checkpoint_revision, data)
        except BaseException:
            self._checkpoint_faulted = True
            raise
        # Publish memory only after both request and projection have committed.
        state.approval_id = approval.approval_id
        state.status = RunStatus.WAITING_APPROVAL
        state.pause_requested = False
        batch.status = ToolBatchStatus.PAUSED
        self._checkpoint_revision = revision
        return ToolDispatchResult(ToolDispatchStatus.WAITING_APPROVAL, approval)

    def _execute_tool(self, name: str, args: Dict[str, Any]) -> ToolDispatchResult:
        """Check current capability, then dispatch through the Main Agent registry."""
        if getattr(self, "_hitl_enabled", False):
            try:
                control = self._hitl_gate(name, args)
            except BaseException:
                # Real pre-dispatch failures are not handler outcomes. Durable
                # PREPARED / WAITING facts must survive without fabricated closure.
                self._checkpoint_faulted = True
                raise
            if control is not None:
                return control
        if not self.tool_registry.has_handler(name) and not (getattr(self, "_hitl_enabled", False) and self._run_state.approval_id):
            return ToolDispatchResult(
                ToolDispatchStatus.UNKNOWN_TOOL,
                f"错误: 未知工具 '{name}'",
            )
        if not self.feature_manager.is_tool_enabled(name) and not (getattr(self, "_hitl_enabled", False) and self._run_state.approval_id):
            return ToolDispatchResult(
                ToolDispatchStatus.FEATURE_DISABLED,
                ToolResult(
                    content=f"错误: 工具 '{name}' 所属功能当前未启用",
                    success=False,
                    execution_success=False,
                ),
            )
        mode_error = self._mode_capability_error(name, args)
        if mode_error:
            return ToolDispatchResult(ToolDispatchStatus.FEATURE_DISABLED, mode_error)
        entered_execution = False

        def enter_execution():
            nonlocal entered_execution
            self._checkpoint_dispatch(name, args)
            entered_execution = True
            self._emit_ui_event("tool_execution_started", name=name,
                                summary=self._tool_event_summary(name, args),
                                command=args.get("command", "") if name in {"bash", "run_background"} else "",
                                cwd=str(getattr(getattr(self, "runtime_context", None), "cwd", "")))

        try:
            value = self.tool_registry.execute(name, args, on_enter=enter_execution)
            return ToolDispatchResult(ToolDispatchStatus.DISPATCHED, value)
        except CheckpointError:
            self._checkpoint_faulted = True
            raise
        except RuntimeEscalationException:
            # Runtime control signals must reach the loop's control handler.
            raise
        except ToolArgumentBindingError as e:
            if getattr(self, "_checkpoint_faulted", False):
                raise
            if entered_execution or e.registry is not self.tool_registry:
                logger.error("工具 '%s' 进入执行后发生参数异常: %s", name, e)
                return ToolDispatchResult(ToolDispatchStatus.EXECUTION_FAILED, f"错误: {e}")
            logger.error(f"工具 '{name}' 参数错误: {e}")
            return ToolDispatchResult(
                ToolDispatchStatus.INVALID_ARGUMENTS,
                f"错误: 工具 '{name}' 参数无效。详情: {str(e)}",
            )
        except Exception as e:
            if getattr(self, "_checkpoint_faulted", False):
                raise
            logger.error(f"执行工具 '{name}' 出错: {e}")
            return ToolDispatchResult(
                ToolDispatchStatus.EXECUTION_FAILED,
                f"错误: {str(e)}",
            )

    def _close_tool_call_batch(
        self,
        tool_calls: List[Dict[str, Any]],
        resolved_call_ids: set[str],
        reason: str,
        *,
        unknown_call_id: str = "",
    ) -> None:
        """Give every unreported call an explicit terminal tool result."""
        for tool_call in tool_calls:
            call_id = tool_call.get("id", "")
            if call_id in resolved_call_ids:
                continue
            function = tool_call.get("function", {})
            tool_name = function.get("name", "")
            if call_id == unknown_call_id:
                outcome = "UNKNOWN"
                result_text = (
                    f"[UNKNOWN] 工具“{tool_name}”已进入执行阶段，但运行时未能确认结果；"
                    "请检查实际影响后再决定是否重试。"
                )
            else:
                outcome = "NOT_EXECUTED"
                result_text = (
                    f"[NOT_EXECUTED] 工具“{tool_name}”未执行；"
                    f"当前批次因以下原因提前结束：{reason}。"
                )
            self.messages.append(Message(
                role="tool", content=result_text, tool_call_id=call_id,
            ))
            resolved_call_ids.add(call_id)
            self.session_recorder.record(
                "tool_result",
                call_id=call_id,
                tool=tool_name,
                success=False,
                blocked=outcome == "NOT_EXECUTED",
                outcome=outcome,
                result=result_text,
            )
            if outcome == "UNKNOWN":
                self._emit_ui_event("tool_result", name=tool_name, call_id=call_id,
                                    success=False, blocked=False, outcome=outcome)

    # ── Pre-LLM Processing (mirrors s_full.py agent_loop) ────────────

    def _record_environment_block(
        self,
        tool_name: str,
        args_hash: str,
        block,
        started_at: float,
        call_id: str,
    ) -> str:
        """Persist blocker evidence and surface it as recoverable tool feedback.

        EnvironmentBlocker remains the authority for classifying a command or
        result.  Whether that evidence terminates the task belongs to the
        progress/completion governance layer, so this helper never ends the
        task by itself.
        """
        result_text = block.message
        self.trace.record_tool_call(
            tool_name=tool_name,
            args_hash=args_hash,
            success=False,
            loop_guard_blocked=False,
            error_message=result_text[:200],
            result_preview=result_text,
            started_at=started_at,
            finished_at=time.time(),
            failure_category=block.category,
            guard_type="ENVIRONMENT_BLOCK",
            guard_reason=block.category,
            recoverability="UNKNOWN",
            strategy_fingerprint="ENVIRONMENT_BLOCKER",
            escalated=False,
            circuit_breaker_triggered=False,
            cwd=str(self.runtime_context.cwd),
            workspace_root=str(self.runtime_context.workspace_root),
            session_id=self.runtime_context.shell_session.session_id,
        )
        self.session_recorder.record(
            "tool_result",
            call_id=call_id,
            tool=tool_name,
            success=False,
            blocked=True,
            outcome="BLOCKED_NOT_EXECUTED",
            result=result_text,
        )
        self._emit_ui_event("tool_result", name=tool_name, success=False, blocked=True)
        return result_text

    def _is_tool_error(self, result: ToolResult | str, tool_name: str = "") -> bool:
        """Detect whether a tool result indicates failure.

        Checks two patterns:
          - "错误:" prefix (from _execute_tool exception handling)
          - "[Exit Code: N]" with N != 0 (from bash tool return)
        """
        if isinstance(result, ToolResult):
            return not bool(
                result.execution_success
                if result.execution_success is not None
                else result.success
            )
        result_text = result
        if not result_text:
            return False
        if result_text.startswith("错误:"):
            return True
        # File tools return localized Harness failures after the dispatcher
        # unwraps ToolResult.content.
        if "Harness" in result_text[:240] and any(
            marker in result_text[:240] for marker in ("失败", "拦截", "failed", "blocked")
        ):
            return True
        if result_text.startswith("[Exit Code: ") and "]" in result_text[:20]:
            try:
                code_start = len("[Exit Code: ")
                code_end = result_text.index("]")
                return int(result_text[code_start:code_end]) != 0
            except (ValueError, IndexError):
                pass
        if '"status": "launch_failed"' in result_text:
            return True
        return False

    @staticmethod
    def _read_file_result_range(result_text: str) -> Optional[tuple[int, int, int]]:
        """Extract the actual bounded range returned by ``read_file``."""
        match = re.search(
            r"^--- FILE: .+? \(LINES: (\d+)-(\d+) of (\d+)\) ---$",
            result_text,
            re.MULTILINE,
        )
        if not match:
            return None
        return tuple(int(value) for value in match.groups())

    def _canonical_workspace_file(self, path: str) -> tuple[str, Path]:
        """Authorize a file path and give memory one workspace-relative identity."""
        file_path = self.tools.safe_path(path)
        workspace_root = getattr(self, "workdir", self.tools.workdir).resolve()
        try:
            canonical = file_path.relative_to(workspace_root).as_posix()
        except ValueError as exc:
            raise ValueError(f"Memory path must be inside the primary workspace: {path}") from exc
        return canonical, file_path

    def _file_freshness(self, path: str) -> Optional[str]:
        """Hash changed file contents, reusing hashes while metadata is stable."""
        try:
            canonical_path, file_path = self._canonical_workspace_file(path)
            if not file_path.is_file():
                self._memory_file_fingerprints.pop(canonical_path, None)
                return None
            stat = file_path.stat()
            metadata = (stat.st_size, stat.st_mtime_ns)
            cached = self._memory_file_fingerprints.get(canonical_path)
            if cached is not None and cached[0] == metadata:
                return cached[1]
            digest = hashlib.sha256()
            with file_path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(64 * 1024), b""):
                    digest.update(chunk)
            freshness = digest.hexdigest()
            self._memory_file_fingerprints[canonical_path] = (metadata, freshness)
            return freshness
        except (OSError, ValueError) as exc:
            logger.debug("Unable to refresh structured memory for %s: %s", path, exc)
            return None

    @staticmethod
    def _read_file_observation(
        result_text: str, start_line: int, end_line: int, total_lines: int,
    ) -> str:
        """Create a bounded note from the exact file range returned by the tool."""
        body = result_text.split("---", 2)[-1]
        body = body.split("--- END FILE:", 1)[0]
        preview = " ".join(body.split())[:160]
        return f"Read lines {start_line}-{end_line} of {total_lines}: {preview}"

    def _refresh_structured_memory_freshness(self) -> None:
        """Drop observations that no longer match the current file contents."""
        for path in self.memory.recent_files:
            freshness = self._file_freshness(path)
            if freshness is None:
                self.memory.invalidate(path)
            else:
                self.memory.refresh_freshness(path, freshness)

    def _update_structured_memory(
        self,
        tool_name: str,
        args: Dict[str, Any],
        tool_result: ToolResult,
        success: bool,
    ) -> None:
        """Track successful file reads and invalidate notes after edits."""
        if not success or not self.feature_manager.is_enabled("memory"):
            return
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            return
        try:
            canonical_path, _ = self._canonical_workspace_file(path)
            if tool_name == "read_file":
                read_range = self._read_file_result_range(tool_result.content)
                freshness = self._file_freshness(canonical_path)
                if read_range is None or freshness is None:
                    return
                start_line, end_line, total_lines = read_range
                self.memory.remember_file(canonical_path, freshness)
                self.memory.record_observation(
                    canonical_path,
                    start_line,
                    end_line,
                    self._read_file_observation(
                        tool_result.content, start_line, end_line, total_lines,
                    ),
                    freshness,
                )
            elif tool_name in {"write_file", "edit_file"}:
                self.memory.remember_file(
                    canonical_path, self._file_freshness(canonical_path),
                )
                self.memory.invalidate(canonical_path)
        except (OSError, ValueError) as exc:
            logger.debug("Structured memory update skipped for %s: %s", tool_name, exc)

    @staticmethod
    def _empty_usage_metrics() -> Dict[str, Any]:
        return {
            "turns": 0,
            "estimated_prompt_tokens": 0,
            "actual_prompt_tokens": 0,
            "main_prompt_tokens": 0,
            "main_cached_tokens": 0,
            "main_uncached_prompt_tokens": 0,
            "main_cache_hit_rate": 0.0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "reasoning_tokens": 0,
            "main_reasoning_tokens": 0,
            "summary_reasoning_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "uncached_prompt_tokens": 0,
            "cache_hit_rate": 0.0,
            "summary_prompt_tokens": 0,
            "summary_completion_tokens": 0,
            "summary_total_tokens": 0,
            "summary_cached_tokens": 0,
            "summary_uncached_prompt_tokens": 0,
            "summary_cache_hit_rate": 0.0,
            "api_errors": 0,
        }

    def _record_provider_usage(
        self,
        usage: Dict[str, Any],
        *,
        estimated_prompt_tokens: int = 0,
        source: str = "main",
    ) -> None:
        """Record estimated and provider-reported usage without conflating them."""
        usage = usage if isinstance(usage, dict) else {}

        def value(name: str) -> int:
            raw = usage.get(name, 0)
            return raw if isinstance(raw, int) and raw >= 0 else 0

        prompt = value("prompt_tokens")
        completion = value("completion_tokens")
        total = value("total_tokens")
        reasoning = value("reasoning_tokens")
        cached = min(value("cached_tokens"), prompt)
        self.last_metrics["total_tokens"] += total
        self.last_metrics["actual_prompt_tokens"] += prompt
        self.last_metrics["completion_tokens"] += completion
        self.last_metrics["reasoning_tokens"] += reasoning
        self.last_metrics["prompt_tokens"] += prompt
        self.last_metrics["cached_tokens"] += cached
        self.last_metrics["uncached_prompt_tokens"] = max(
            self.last_metrics["prompt_tokens"] - self.last_metrics["cached_tokens"],
            0,
        )
        prompt_total = self.last_metrics["prompt_tokens"]
        self.last_metrics["cache_hit_rate"] = (
            self.last_metrics["cached_tokens"] / prompt_total
            if prompt_total else 0.0
        )
        if source == "summary":
            self.last_metrics["summary_prompt_tokens"] += prompt
            self.last_metrics["summary_completion_tokens"] += completion
            self.last_metrics["summary_reasoning_tokens"] += reasoning
            self.last_metrics["summary_total_tokens"] += total
            self.last_metrics["summary_cached_tokens"] += cached
            summary_prompt_total = self.last_metrics["summary_prompt_tokens"]
            self.last_metrics["summary_uncached_prompt_tokens"] = max(
                summary_prompt_total - self.last_metrics["summary_cached_tokens"],
                0,
            )
            self.last_metrics["summary_cache_hit_rate"] = (
                self.last_metrics["summary_cached_tokens"] / summary_prompt_total
                if summary_prompt_total else 0.0
            )
        else:
            self.last_metrics["estimated_prompt_tokens"] += max(
                int(estimated_prompt_tokens), 0
            )
            self.last_metrics["main_prompt_tokens"] += prompt
            self.last_metrics["main_reasoning_tokens"] += reasoning
            self.last_metrics["main_cached_tokens"] += cached
            self.last_metrics["main_uncached_prompt_tokens"] = max(
                self.last_metrics["main_prompt_tokens"] - self.last_metrics["main_cached_tokens"],
                0,
            )
            main_prompt_total = self.last_metrics["main_prompt_tokens"]
            self.last_metrics["main_cache_hit_rate"] = (
                self.last_metrics["main_cached_tokens"] / main_prompt_total
                if main_prompt_total else 0.0
            )

        self.trace.record_provider_usage(
            usage,
            estimated_prompt_tokens=estimated_prompt_tokens,
            source=source,
        )

    def _build_request_messages(self, hot_text: str) -> List[Message]:
        """Build the exact transient message list sent to the provider."""
        if not hot_text:
            return self.messages
        msgs_for_llm = list(self.messages)
        if msgs_for_llm and msgs_for_llm[-1].role == 'user':
            last = msgs_for_llm[-1]
            msgs_for_llm[-1] = Message(
                role='user',
                content=last.content + "\n\n" + hot_text,
            )
        else:
            msgs_for_llm.append(Message(role='user', content=hot_text))
        return msgs_for_llm

    def _check_auto_compress(
        self,
        estimated_prompt_tokens: Optional[int] = None,
    ) -> bool:
        """Auto-compress if token threshold exceeded.

        Returns:
            True if compression (full or micro) was actually triggered.
        """
        if self.has_pending_tool_batch:
            return False
        if self.feature_manager.is_enabled('compression'):
            if not self._compression_notice_pending:
                if ((len(self.messages) > 17 and self.compressor.should_compress(
                        self.messages, estimated_prompt_tokens=estimated_prompt_tokens))
                        or (self.compressor.should_microcompact(
                            self.messages, estimated_prompt_tokens=estimated_prompt_tokens)
                            and self.compressor.has_microcompact_work(self.messages))):
                    self._compression_notice_pending = True
                return False
            self._compression_notice_pending = False
            before = copy.deepcopy(self.messages)
            if self.compressor.should_compress(
                self.messages, estimated_prompt_tokens=estimated_prompt_tokens,
            ):
                logger.info("触发自动压缩")
                self.messages = self.compressor.compress(self.messages)
            elif self.compressor.should_microcompact(
                self.messages, estimated_prompt_tokens=estimated_prompt_tokens,
            ):
                logger.info("触发微压缩")
                self.messages = self.compressor.microcompact(self.messages)
            changed = self.messages != before
            summary_usage = self.compressor.consume_last_summary_usage()
            if summary_usage is not None:
                self._record_provider_usage(summary_usage, source="summary")
            return changed
        return False

    def _drain_background_notifications(self) -> Optional[str]:
        """Drain background task notifications and return as formatted string.

        Returns:
            Formatted notification text, or None if nothing to report.
        """
        if not self.feature_manager.is_enabled('background'):
            return None
        notifs = self.background.check_notifications()
        if not notifs:
            return None
        lines = []
        for tid in notifs:
            task = self.background.get(tid)
            if task:
                lines.append(f"[bg:{tid}] {task.status.value}: {task.result or task.error}")
        if not lines:
            return None
        return "\n".join(lines)

    def _check_inbox(self) -> Optional[str]:
        """Check lead inbox for messages from teammates.

        Returns:
            Formatted inbox text, or None if inbox is empty.
        """
        inbox = self.message_bus.read_inbox(self.config.agent.name)
        if not inbox:
            return None
        import json as _json
        text = _json.dumps([{'from': m.sender, 'type': m.msg_type.value, 'content': m.content[:200]}
                           for m in inbox], indent=2, ensure_ascii=False)
        return text

    def _get_dynamic_hot_context(self,
                                  iteration: Optional[int] = None,
                                  max_iterations: Optional[int] = None) -> str:
        """Aggregate all transient dynamic state into one hot-context block.

        This data is injected temporarily before each LLM call and must
        NEVER be appended to ``self.messages``, protecting prefix-cache
        stability and message-history immutability.

        Sources:
          - Incomplete todo items (from in-memory TodoManager)
          - Background task notifications

        Returns:
            Empty string if nothing dynamic; otherwise an XML-wrapped block.
        """
        parts = []

        # Current session note and a one-turn warning before history changes.
        note_text = self.agent_note.read()
        if note_text:
            parts.append(f"<agent-note>\n{note_text}\n</agent-note>")

        if self._compression_notice_pending:
            parts.append(self._compression_warning_block())

        # Todo status snapshot
        if self.todo.has_open_items():
            todo_text = self.todo.render()
            parts.append(f"<todo-status>\n{todo_text}\n</todo-status>")

        # 2. Background notifications
        bg_text = self._drain_background_notifications()
        if bg_text:
            parts.append(f"<background-results>\n{bg_text}\n</background-results>")

        # Bounded file observations are transient and freshness-checked.
        if (
            getattr(self, "memory", None) is not None
            and self.feature_manager.is_enabled("memory")
        ):
            self._refresh_structured_memory_freshness()
            memory_text = self.memory.render()
            if memory_text:
                parts.append(
                    f"<structured-file-memory>\n{memory_text}\n</structured-file-memory>"
                )

        # Late-run guidance is advisory; it does not block useful inspection.
        if (iteration is not None and max_iterations is not None
                and max_iterations > 0 and 2 * (iteration + 1) >= max_iterations):
            remaining = max(max_iterations - iteration - 1, 0)
            parts.append(
                "<execution-budget>"
                f"Model call {iteration + 1}/{max_iterations}; "
                f"{remaining} calls remain after this one. "
                "If this task requires edits, prioritize a small verifiable "
                "implementation over open-ended investigation. If blocked, "
                "identify the specific missing evidence. Do not claim "
                "unverified completion."
                "</execution-budget>"
            )

        if not parts:
            return ""
        return "<dynamic_context>\n" + "\n".join(parts) + "\n</dynamic_context>"

    @staticmethod
    def _compression_warning_block() -> str:
        return (
            "<compression-warning>下一次模型调用前将压缩较早的对话和工具输出。"
            "请现在检查尚需保留的用户约束、关键结论及其证据，必要时调用 "
            "update_agent_note 更新完整笔记；没有新信息时直接继续任务。"
            "不要把未验证的推测写成事实。</compression-warning>"
        )

    def _sync_compression_warning(self, hot_text: str) -> str:
        """Update the pending warning without draining notifications twice."""
        warning = self._compression_warning_block()
        if self._compression_notice_pending:
            if warning in hot_text:
                return hot_text
            if hot_text:
                return hot_text.replace(
                    "\n</dynamic_context>", f"\n{warning}\n</dynamic_context>",
                )
            return f"<dynamic_context>\n{warning}\n</dynamic_context>"
        if warning not in hot_text:
            return hot_text
        return hot_text.replace(f"{warning}\n", "").replace(
            "<dynamic_context>\n</dynamic_context>", "",
        )

    # ── Core LLM + Tool Cycle ──────────────────────────────────────

    def _llm_tool_cycle(self, max_iterations: int = 50, require_tool_call: bool = False,
                        *, _continuing: bool = False) -> str:
        store = getattr(self, "_checkpoint_store", None)
        if store is None:
            return self._llm_tool_cycle_impl(max_iterations, require_tool_call, _continuing=_continuing)
        if self._checkpoint_faulted:
            raise CheckpointError("Checkpoint 故障实例不能再次推进，请用新实例核验原 Run")
        state = self._run_state
        if state is None or state.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.TERMINATED, RunStatus.CANCELLED}:
            state = RunState(max_iterations=max_iterations, require_tool_call=require_tool_call)
            self._run_state = state
        creating = self._checkpoint_run_id != state.run_id
        try:
            self._checkpoint_claim(creating=creating)
            return self._llm_tool_cycle_impl(max_iterations, require_tool_call, _continuing=_continuing)
        except BaseException as error:
            if self._checkpoint_faulted or (creating and state.status is RunStatus.RUNNING):
                self._checkpoint_faulted = True
                # Memory can lead the DB. Do not fabricate closure or continue from it.
                state.finish(RunStatus.FAILED if isinstance(error, Exception) else RunStatus.CANCELLED,
                             f"Checkpoint 故障，运行已停止: {error}")
            elif state.status in {RunStatus.FAILED, RunStatus.CANCELLED}:
                # Bootstrap has not dispatched. Persist its terminal lifecycle,
                # but a second persistence failure must not replace the signal.
                try:
                    self._finish_run(state.status, state.terminal_result or "运行初始化失败")
                except BaseException:
                    logger.exception("初始化失败后的 Checkpoint 收尾失败；保留原始中断/异常")
            raise
        finally:
            if store.holds_lock(state.run_id):
                store.release(state.run_id)
            self._release_conversation_lock()

    def _llm_tool_cycle_impl(
        self, max_iterations: int = 50, require_tool_call: bool = False,
        *, _continuing: bool = False,
    ) -> str:
        """Core LLM + tool execution cycle, looping tools back to LLM (s_full.py s02 pattern).

        Args:
            max_iterations: Maximum number of LLM calls to prevent infinite loops.
        """
        state = getattr(self, "_run_state", None)
        is_new_run = state is None or state.status in {
            RunStatus.COMPLETED,
            RunStatus.TERMINATED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
        if is_new_run:
            state = RunState(
                max_iterations=max_iterations,
                require_tool_call=require_tool_call,
            )
            self._run_state = state
        elif state.status is RunStatus.PAUSED:
            return "错误: 当前运行已暂停，请通过继续入口恢复原工具批次。"
        elif state.status is not RunStatus.RUNNING:
            return "错误: 当前运行状态不允许继续执行。"
        else:
            max_iterations = state.max_iterations
            require_tool_call = state.require_tool_call

        try:
            if is_new_run:
                self._seen_run_ids = getattr(self, "_seen_run_ids", set())
                self._seen_run_ids.add(state.run_id)
            provider = self.provider_manager.get_primary_provider()
            if not provider and not state.has_pending_tool_batch:
                return self._finish_run(
                    RunStatus.FAILED,
                    "错误: 没有可用的 LLM 提供者。请配置 API 密钥。",
                )

            tools = self._get_llm_tools()
            tool_defs = [ProviderToolDef(**t) for t in tools]

            if not state.trace_started:
                # New Run initialization happens once. Continuing an in-memory batch
                # keeps its Trace task and governance history alive.
                self.last_metrics = self._empty_usage_metrics()
                tid = self._create_run_trace(self.trace, state, self._current_user_prompt)
                state.trace_task_id = tid
                state.trace_started = True
                self.session_recorder.record(
                    "run_trace_link",
                    run_id=state.run_id,
                    trace_task_id=tid,
                )
                self.runtime_context.current_task_id = tid
                self.runtime_policy.reset()
                self.workspace_state_guard.reset()
                self.runtime_observer.reset()
                self.completion_guard.reset()
                self._last_assistant_note = None
            if state.tool_batch is not None and not state.has_pending_tool_batch:
                self._finalize_tool_batch(state.tool_batch)
        except BaseException as exc:
            self._abort_run_initialization(state, exc, continuing=_continuing)
            raise

        active_tool_calls: List[Dict[str, Any]] = []
        resolved_tool_call_ids: set[str] = set()
        active_batch_assistant_index = -1
        current_tool_call_id = ""
        current_tool_execution_started = False
        current_tool_result_received = False
        current_tool_result_available = False
        tool_call_outcome = ""
        while state.model_turns_used < max_iterations or state.has_pending_tool_batch:
            resuming_batch = state.has_pending_tool_batch
            try:
                if resuming_batch:
                    batch = state.tool_batch
                    assert batch is not None
                    iteration = batch.model_iteration
                    content = batch.assistant_content
                    tool_calls = batch.tool_calls
                    finish_reason = None
                    estimated_prompt_tokens = 0
                    usage = {}
                else:
                    if not provider:
                        self.trace.end_task("FAILED", "NO_PROVIDER")
                        return self._finish_run(
                            RunStatus.FAILED,
                            "错误: 没有可用的 LLM 提供者。请配置 API 密钥。",
                        )
                    iteration = state.model_turns_used
                    self._emit_ui_event("model_request_started", iteration=iteration + 1)
                    self.session_recorder.record(
                        "model_request_started", turn=iteration + 1,
                        run_id=state.run_id,
                    )
                    logger.info(
                        "LLM_TURN_START: iteration=%s messages=%s",
                        iteration + 1, len(self.messages),
                    )
                    self.trace.start_turn(iteration)
                    # Pending tool batches bypass this entire pre-LLM pipeline.
                    self.messages = Compressor._clean_tool_chains(self.messages)
                    hot_text = self._get_dynamic_hot_context(
                        iteration=iteration,
                        max_iterations=max_iterations,
                    )
                    msgs_for_llm = self._build_request_messages(hot_text)
                    estimated_prompt_tokens = self.compressor.estimate_prompt_tokens(
                        msgs_for_llm,
                        system_prompt=self.system_prompt,
                        tools=tools,
                    )
                    if self._check_auto_compress(estimated_prompt_tokens):
                        self.trace.record_compression()
                    hot_text = self._sync_compression_warning(hot_text)
                    self.messages = Compressor._clean_tool_chains(self.messages)
                    msgs_for_llm = self._build_request_messages(hot_text)
                    estimated_prompt_tokens = self.compressor.estimate_prompt_tokens(
                        msgs_for_llm,
                        system_prompt=self.system_prompt,
                        tools=tools,
                    )
                    role_counts = Counter(m.role for m in self.messages)
                    logger.debug(
                        f"[LLM#{iteration}] 发送 {len(self.messages)} 条消息: {dict(role_counts)}"
                    )
                    self.trace.set_message_count(len(self.messages))
                    self.trace.record_provider_stream(getattr(provider, 'stream', False))
                    self._checkpoint_save("before_model")
                    response = provider.create_message(
                        msgs_for_llm,
                        tool_defs,
                        system=self.system_prompt,
                        max_tokens=self.config.llm.max_tokens,
                        temperature=self.config.llm.temperature,
                    )
                    parsed = self._parse_response(provider, response)
                    self.trace.record_reasoning_content_chars(
                        parsed.get('reasoning_content_chars')
                    )
                    content = parsed.get('content', '')
                    tool_calls = parsed.get('tool_calls', [])
                    finish_reason = parsed.get('finish_reason')
                    usage = parsed.get('usage', {})
                    state.model_turns_used = iteration + 1
                    self.last_metrics["turns"] = state.model_turns_used

                # A truncated response may contain incomplete text or tool
                # calls. Never publish or execute either as a complete turn.
                if not resuming_batch and finish_reason in {'length', 'max_tokens', 'limit'}:
                    self._record_provider_usage(
                        parsed.get('usage', {}),
                        estimated_prompt_tokens=estimated_prompt_tokens,
                        source="main",
                    )
                    self.trace.record_provider_finish_reason(finish_reason)
                    self.trace.end_task("FAILED", "PROVIDER_OUTPUT_LIMIT")
                    return self._finish_run(
                        RunStatus.FAILED,
                        "错误: 模型输出达到上限，本轮回复可能不完整，任务未完成。",
                    )
                if not resuming_batch:
                    self.trace.record_provider_finish_reason(finish_reason)

                if not resuming_batch and content and tool_calls:
                    logger.info("LLM_NOTE[%s]: %s", iteration + 1, content)
                    self.session_recorder.record("assistant_note", turn=iteration + 1, content=content)
                    self._last_assistant_note = content
                    self._emit_ui_event("assistant_note", text=content)
                logger.info(
                    "LLM_TURN_RESULT: iteration=%s tool_calls=%s",
                    iteration + 1,
                    len(tool_calls),
                )

                # ── Trace: record assistant content ──
                if not resuming_batch:
                    self.trace.record_assistant_content(content)

                # ── Accumulate benchmark metrics ──
                if not resuming_batch:
                    self._record_provider_usage(
                        usage,
                        estimated_prompt_tokens=estimated_prompt_tokens,
                        source="main",
                    )
                if not tool_calls and not content.strip():
                    self.trace.end_task("FAILED", "EMPTY_PROVIDER_RESPONSE")
                    return self._finish_run(
                        RunStatus.FAILED,
                        "错误: 模型未返回可见回复或工具调用，任务未完成。",
                    )

                if not tool_calls:
                    self.messages.append(Message(role='assistant', content=content))
                    if require_tool_call and not state.tool_call_seen:
                        if state.no_tool_retry_count == 0:
                            state.no_tool_retry_count += 1
                            self.trace.record_no_tool_retry(state.no_tool_retry_count)
                            self.messages.append(Message(
                                role='user',
                                content=(
                                    "请继续执行任务，不要只描述计划。请立即调用合适的工具，"
                                    "完成用户要求后再给出总结。"
                                ),
                            ))
                            logger.warning("首轮未产生工具调用，已追加一次执行纠偏")
                            continue
                        self.trace.end_task("FAILED")
                        return self._finish_run(RunStatus.FAILED, content)
                    completion_decision = self.completion_guard.check(self.runtime_observer)
                    latest_event = self.runtime_policy.history.all()[-1:]
                    if latest_event:
                        # finalize() annotates the existing attempt; refresh
                        # the trace entry in place so this is not a duplicate.
                        self.trace.update_last_attempt_event(latest_event[0].to_dict())
                    if completion_decision.should_terminate:
                        # Completion STOP may reference only the latest
                        # unresolved failure, never an arbitrary historical
                        # observation.  This is annotation plumbing; the
                        # completion decision itself is unchanged.
                        completion_refs: list[str] = []
                        for event in reversed(self.runtime_policy.history.all()):
                            if event.status.name in {"FAILURE", "BLOCKED"} or event.observed_failure:
                                completion_refs = list(event.governance_evidence_ids or event.evidence_ids)
                                break
                        self.trace.record_completion_guard(
                            completion_decision.reason,
                            completion_decision.open_blocker_count,
                            governance_action=completion_decision.action.value,
                            evidence_ids=list(dict.fromkeys(completion_refs)),
                        )
                        self.trace.end_task(
                            "BLOCKED_ENVIRONMENT",
                            "UNRESOLVED_BLOCKER_COMPLETION",
                        )
                        return self._finish_run(
                            RunStatus.TERMINATED,
                            self.completion_guard.message(completion_decision),
                        )
                    if completion_decision.action.value == "REPLAN":
                        self.trace.record_completion_guard(
                            completion_decision.reason,
                            completion_decision.open_blocker_count,
                            governance_action=completion_decision.action.value,
                        )
                        self.messages.append(Message(
                            role='user',
                            content=self.completion_guard.message(completion_decision),
                        ))
                        continue
                    self.trace.end_task("SUCCESS")
                    return self._finish_run(RunStatus.COMPLETED, content)

                # ── Store assistant message with tool calls ──
                state.tool_call_seen = True
                if resuming_batch:
                    batch = state.tool_batch
                    assert batch is not None
                    active_batch_assistant_index = batch.assistant_message_index
                else:
                    # Provider response IDs are preserved. The fallback only
                    # protects the message chain if a compatible provider omits it.
                    for call_index, tc in enumerate(tool_calls, start=1):
                        if not isinstance(tc.get("id"), str) or not tc["id"].strip():
                            tc["id"] = f"local_{iteration + 1}_{call_index}"
                    call_ids = [tc["id"] for tc in tool_calls]
                    if len(call_ids) != len(set(call_ids)):
                        raise ValueError("同一工具批次包含重复 Tool Call ID，拒绝执行以避免结果关联错误")
                    self.messages.append(Message(
                        role='assistant',
                        content=content,
                        tool_calls=tool_calls,
                    ))
                    batch = ToolBatchState(
                        tool_calls=tool_calls,
                        assistant_message_index=len(self.messages) - 1,
                        model_iteration=iteration,
                        assistant_content=content,
                    )
                    batch.start()
                    state.tool_batch = batch
                    self._checkpoint_save("tools")
                    active_batch_assistant_index = batch.assistant_message_index
                active_tool_calls = batch.tool_calls
                resolved_tool_call_ids = batch.resolved_call_ids
                current_result_facts = {}

                def append_tool_result(tool_call: Dict[str, Any], result_text: str) -> None:
                    call_id = tool_call.get("id", "")
                    if call_id in batch.resolved_call_ids:
                        raise RuntimeError(f"Tool Call {call_id} 已有结果，拒绝重复写入")
                    self.messages.append(Message(
                        role="tool", content=result_text, tool_call_id=call_id,
                    ))
                    batch.resolved_call_ids.add(call_id)

                def defer_replan(message: str) -> None:
                    batch.pending_replan_messages.append(message)

                def finish_tool_batch(
                    reason: str,
                    *,
                    unknown_call_id: str = "",
                    failed: bool = False,
                ) -> None:
                    nonlocal active_batch_assistant_index
                    # A KeyboardInterrupt may arrive after appending a result
                    # message but before recording its ID in the local set.
                    if active_batch_assistant_index >= 0:
                        resolved_tool_call_ids.update(
                            message.tool_call_id
                            for message in self.messages[active_batch_assistant_index + 1:]
                            if message.role == "tool" and message.tool_call_id
                        )
                    current_message_ids = {
                        message.tool_call_id
                        for message in self.messages[active_batch_assistant_index + 1:]
                        if message.role == "tool" and message.tool_call_id
                    } if active_batch_assistant_index >= 0 else set()
                    if (
                        current_tool_result_available
                        and current_tool_call_id
                        and current_tool_call_id not in current_message_ids
                    ):
                        # Dispatch already returned a definite result. If only
                        # the message append failed, preserve that result rather
                        # than misclassifying the executed call as UNKNOWN.
                        self.messages.append(Message(
                            role="tool",
                            content=result_text,
                            tool_call_id=current_tool_call_id,
                        ))
                        resolved_tool_call_ids.add(current_tool_call_id)
                        batch.resolved_call_ids.add(current_tool_call_id)
                        record_tool_result_event(
                            tname,
                            result_text,
                            success=tool_call_outcome == "EXECUTED_SUCCESS",
                            blocked=(
                                tool_call_outcome.startswith("BLOCKED_")
                                or tool_call_outcome == "SKIPPED_DUPLICATE"
                            ),
                            outcome=tool_call_outcome,
                            cancelled=tool_call_outcome == "CANCELLED",
                        )
                        unknown_call_id = ""
                    self._close_tool_call_batch(
                        active_tool_calls,
                        resolved_tool_call_ids,
                        reason,
                        unknown_call_id=unknown_call_id,
                    )
                    for message in self.messages[active_batch_assistant_index + 1:]:
                        if (
                            message.role != "tool"
                            or not message.tool_call_id
                            or message.tool_call_id in batch.results
                        ):
                            continue
                        if (
                            message.tool_call_id == current_tool_call_id
                            and current_tool_result_available
                            and tool_call_outcome
                        ):
                            outcome = tool_call_outcome
                        elif message.content.startswith("[UNKNOWN]"):
                            outcome = "UNKNOWN"
                        elif message.content.startswith("[NOT_EXECUTED]"):
                            outcome = "NOT_EXECUTED"
                        else:
                            outcome = "RECORDED_BEFORE_TERMINAL"
                        batch.results[message.tool_call_id] = ToolCallResultState(
                            message.tool_call_id, outcome, message.content,
                            **(non_execution_facts(NonExecutionReason.BATCH_ABORTED) if outcome == "NOT_EXECUTED"
                               else current_result_facts if message.tool_call_id == current_tool_call_id else {}),
                        )
                    ordered_call_ids = [
                        str(call.get("id", "")) for call in batch.tool_calls
                    ]
                    if all(call_id in batch.results for call_id in ordered_call_ids):
                        # This batch is terminal and every protocol call now has
                        # a recorded result; expose no stale resumable cursor.
                        batch.next_call_index = len(batch.tool_calls)
                    self._finalize_tool_batch(batch)
                    batch.mark_terminal(failed=failed)
                    active_batch_assistant_index = -1

                def record_tool_result_event(
                    tool_name: str,
                    result_text: str,
                    *,
                    success: bool,
                    blocked: bool,
                    outcome: str,
                    cancelled: bool = False,
                    output_visibility: Optional[Dict[str, Any]] = None,
                ) -> None:
                    event = {
                        "call_id": current_tool_call_id,
                        "tool": tool_name,
                        "success": success,
                        "blocked": blocked,
                        "outcome": outcome,
                        "result": result_text,
                    }
                    if current_result_facts:
                        event["execution"] = dict(current_result_facts)
                    if cancelled:
                        event["cancelled"] = True
                    if output_visibility:
                        event["output_visibility"] = output_visibility
                    self.session_recorder.record("tool_result", **event)

                def advance_current_call(outcome: str, result_text: str) -> bool:
                    advance = state.record_tool_result(
                        current_tool_call_id,
                        outcome,
                        result_text,
                        **current_result_facts,
                    )
                    self._checkpoint_save("tools")
                    if advance is ToolBatchAdvance.PAUSED or state.status is RunStatus.PAUSED:
                        self.session_recorder.record(
                            "run_paused",
                            run_id=state.run_id,
                            next_tool_call_id=(
                                batch.pending_call_ids[0]
                                if batch.pending_call_ids else ""
                            ),
                            completed_tool_call_ids=list(batch.results),
                        )
                        return True
                    return False

                # ── Deduplicate: skip identical tool+args in the same response ──
                seen_tool_sigs = batch.seen_signatures
                # ── Execute each tool and store results ──
                while batch.has_pending_calls:
                    call_index = batch.next_call_index
                    tc = batch.tool_calls[call_index]
                    current_tool_call_id = tc["id"]
                    current_result_facts = {}
                    current_tool_execution_started = False
                    current_tool_result_received = False
                    current_tool_result_available = False
                    tool_call_outcome = ""
                    result_text = ""  # Never reuse the preceding call's fallback result.
                    fn = tc.get('function', {})
                    tname = fn.get('name', '')
                    args_raw = fn.get('arguments', '{}')

                    # Duplicate detection within same LLM response
                    sig = (tname, args_raw)
                    if sig in seen_tool_sigs:
                        logger.warning(f"检测到重复工具调用【{tname}】，已自动跳过")
                        duplicate_intent = CommandNormalizer.normalize(
                            tname, {"raw_arguments": str(args_raw)}
                        )
                        event, _ = self.runtime_policy.record_attempt(
                            turn=iteration + 1, tool_name=tname,
                            intent_key=duplicate_intent.to_key(),
                            args_fingerprint=canonicalize_args({"raw_arguments": str(args_raw)}),
                            success=False, result_text="duplicate tool call in same response",
                            failure_category="", block_reason="SAME_RESPONSE_DUPLICATE",
                        )
                        self.trace.record_attempt_event(event.to_dict())
                        result_text = (
                            "[SKIPPED_DUPLICATE] 同一模型响应中相同工具和参数已处理；"
                            "本次未重复执行。"
                        )
                        tool_call_outcome = "SKIPPED_DUPLICATE"
                        current_result_facts = non_execution_facts(NonExecutionReason.DUPLICATE_SKIPPED)
                        current_tool_result_available = True
                        self.session_recorder.record(
                            "tool_call", call_id=tc["id"], tool=tname,
                            args={"raw_arguments": str(args_raw)},
                            outcome="SKIPPED_DUPLICATE",
                        )
                        append_tool_result(tc, result_text)
                        self.session_recorder.record(
                            "tool_result", call_id=tc["id"], tool=tname,
                            success=False, blocked=True,
                            outcome="SKIPPED_DUPLICATE", result=result_text,
                        )
                        self._emit_ui_event("tool_result", name=tname, call_id=tc["id"],
                                            success=False, blocked=True, outcome="SKIPPED_DUPLICATE")
                        if advance_current_call("SKIPPED_DUPLICATE", result_text):
                            return "任务已在安全工具边界暂停，可在同一进程继续。"
                        continue
                    seen_tool_sigs.add(sig)
                    logger.info("TOOL_REQUEST: name=%s raw_args=%s", tname, args_raw)

                    try:
                        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                        if getattr(self, "_hitl_enabled", False):
                            snapshot_require(type(args) is dict, "工具参数必须为 JSON 对象")
                            json_copy(args)  # Reject non-finite values before governance or dispatch.
                    except (ValueError, TypeError) as exc:
                        # A malformed model argument should become tool feedback,
                        # not terminate the entire agent task.
                        result_text = f"工具参数 JSON 无效，请重新生成合法 JSON: {exc}"
                        tool_call_outcome = "INVALID_ARGUMENTS"
                        current_result_facts = non_execution_facts(NonExecutionReason.ARGUMENTS_REJECTED)
                        current_tool_result_available = True
                        args_hash = canonicalize_args({"raw_arguments": str(args_raw)})
                        intent = CommandNormalizer.normalize(
                            tname, {"raw_arguments": str(args_raw)}
                        )
                        event, _ = self.runtime_policy.record_attempt(
                            turn=iteration + 1, tool_name=tname,
                            intent_key=intent.to_key(), args_fingerprint=args_hash,
                            success=False, result_text=result_text,
                            failure_category="INVALID_ARGUMENTS",
                        )
                        self.trace.record_attempt_event(event.to_dict())
                        self.trace.record_tool_call(
                            tool_name=tname,
                            args_hash=args_hash,
                            success=False,
                            error_message=result_text[:200],
                            result_preview=result_text[:200],
                            failure_category="INVALID_ARGUMENTS",
                            recoverability="SELF_HEALABLE",
                            cwd=str(self.runtime_context.cwd),
                            workspace_root=str(self.runtime_context.workspace_root),
                            session_id=self.runtime_context.shell_session.session_id,
                        )
                        append_tool_result(tc, result_text)
                        self.session_recorder.record(
                            "tool_call",
                            call_id=tc.get("id"),
                            tool=tname,
                            args={"raw_arguments": str(args_raw)},
                            argument_error=True,
                        )
                        self.session_recorder.record(
                            "tool_result",
                            call_id=tc.get("id"),
                            tool=tname,
                            success=False,
                            blocked=False,
                            outcome="INVALID_ARGUMENTS",
                            result=result_text,
                        )
                        logger.warning(f"工具参数解析失败【{tname}】，已反馈给 Agent: {exc}")
                        if advance_current_call("INVALID_ARGUMENTS", result_text):
                            return "任务已在安全工具边界暂停，可在同一进程继续。"
                        continue

                    self._emit_ui_event(
                        "tool_start",
                        name=tname,
                        summary=self._tool_event_summary(tname, args),
                    )
                    logger.info("TOOL_CALL: name=%s args=%s", tname, args)
                    self.session_recorder.record(
                        "tool_call",
                        call_id=tc.get("id"),
                        tool=tname,
                        args=args,
                    )

                    # ── Trace + V3 Defense + Execute + Failure Intelligence ──
                    t_start = time.time()
                    args_hash = canonicalize_args(args)
                    # Capture the before snapshot for the stateless workspace
                    # observer; lifecycle and recurrence live in AttemptHistory.
                    tracker_state_before = self.workspace_state_guard.snapshot()
                    state_before = tracker_state_before if self.workspace_state_guard.is_write_operation(tname, args) else None
                    state_guard_blocked = False
                    v3_block_msg = None
                    failure_sig = None
                    observation_text = ""
                    tool_call_outcome = "BLOCKED_NOT_EXECUTED"

                    try:
                        # ── V3 Layer 2+3: intent-based dedup → circuit breaker ──
                        # Startup OFFLINE is retained as evidence in the trace,
                        # never as an execution blocker. Real result below is
                        # the fresher capability observation.
                        preflight_evidence = self.environment_blocker.check_command(tname, args)
                        preflight_block = None
                        if preflight_block:
                            result_text = self._record_environment_block(
                                tname, args_hash, preflight_block, t_start, tc.get("id", "")
                            )
                            observation_text = result_text
                            intent = CommandNormalizer.normalize(tname, args)
                            decision = self.runtime_observer.observe(
                                turn=iteration + 1, tool_name=tname,
                                intent_key=intent.to_key(), success=False,
                                result_text=observation_text,
                                failure_category=preflight_block.category,
                                blocker_category=preflight_block.category,
                                workspace_before=tracker_state_before,
                                workspace_after=tracker_state_before,
                                workspace_root=str(self.runtime_context.workspace_root),
                                command_blocked=True,
                            )
                            self.trace.annotate_current_tool(
                                intent_key=intent.to_key(),
                                observation_fingerprint=decision.event.observation_fingerprint,
                                semantic_state=decision.event.semantic_state,
                                progress_detected=decision.progress_detected,
                                progress_reason=list(decision.progress_reason),
                                stagnation_reason=list(decision.stagnation_reason),
                                recovery_stage=decision.recovery_stage.value,
                                open_blocker_count=decision.open_blocker_count,
                                oscillation_detected=decision.oscillation_detected,
                                verification_improved=decision.event.verification_improved,
                                governance_decision=decision.action.value,
                                workspace_before_digest=decision.event.workspace_before_digest,
                                workspace_after_digest=decision.event.workspace_after_digest,
                                changed_paths=list(decision.event.changed_paths),
                            )
                            tool_call_outcome = "BLOCKED_PRECHECK"
                            current_result_facts = non_execution_facts(NonExecutionReason.PRECHECK_REJECTED)
                            current_tool_result_available = True
                            append_tool_result(tc, result_text)
                            current_tool_result_received = True
                            if decision.should_terminate:
                                state.pause_requested = False
                                state.record_tool_result(
                                    tc["id"], "BLOCKED_PRECHECK", result_text,
                                    **current_result_facts,
                                )
                                finish_tool_batch("Runtime Policy HARD_STOP")
                                self.trace.end_task("CIRCUIT_BROKEN", decision.reason)
                                return self._finish_run(
                                    RunStatus.TERMINATED, result_text,
                                )
                            if decision.should_replan:
                                defer_replan(
                                    "请重新规划：当前阻断仍只是证据，先寻找合法替代或验证其是否可恢复，"
                                    "不要重复同一失败策略。"
                                )
                            if advance_current_call("BLOCKED_PRECHECK", result_text):
                                return "任务已在安全工具边界暂停，可在同一进程继续。"
                            continue

                        state_stall_preblock = None
                        if state_before is not None:
                            state_stall_preblock = self.workspace_state_guard.pending_write_message()
                        if state_stall_preblock:
                            state_guard_blocked = True
                            v3_block_msg = state_stall_preblock
                        else:
                            v3_block_msg = self.loop_controller.check(tname, args)

                        if v3_block_msg:
                            result_text = v3_block_msg
                            observation_text = result_text
                            tool_call_outcome = "BLOCKED_NOT_EXECUTED"
                            current_result_facts = non_execution_facts(NonExecutionReason.PRECHECK_REJECTED)
                            current_tool_result_available = True
                            tool_result = ToolResult(
                                content=result_text, success=False,
                                execution_success=False,
                            )
                            append_tool_result(tc, result_text)
                            current_tool_result_received = True
                            record_tool_result_event(
                                tname,
                                result_text,
                                success=False,
                                blocked=True,
                                outcome="BLOCKED_NOT_EXECUTED",
                                output_visibility=tool_result.output_visibility,
                            )
                            observation_evidence = ObservationNormalizer.normalize(
                                tname, args, tool_result
                            )
                            logger.warning("TOOL_BLOCKED: name=%s reason=%s", tname, result_text)
                        else:
                            current_tool_execution_started = True
                            dispatch = self._execute_tool(tname, args)
                            # Preserve compatibility with tests/extensions that replace
                            # the private dispatcher with a plain handler result.
                            if isinstance(dispatch, ToolDispatchResult):
                                dispatch_status = dispatch.status
                                result = dispatch.value
                            else:
                                dispatch_status = ToolDispatchStatus.DISPATCHED
                                result = dispatch
                            if dispatch_status is ToolDispatchStatus.WAITING_APPROVAL:
                                current_tool_execution_started = False
                                return self._approval_wait_message(result)
                            if dispatch_status in {ToolDispatchStatus.PERMISSION_DENIED, ToolDispatchStatus.APPROVAL_REJECTED}:
                                current_tool_execution_started = False
                                tool_call_outcome = "BLOCKED_PERMISSION_DENIED"
                                current_result_facts = non_execution_facts(
                                    NonExecutionReason.APPROVAL_REJECTED if dispatch_status is ToolDispatchStatus.APPROVAL_REJECTED
                                    else NonExecutionReason.PERMISSION_DENIED)
                                result_text = str(result)
                                current_tool_result_available = True
                                append_tool_result(tc, result_text)
                                current_tool_result_received = True
                                record_tool_result_event(tname, result_text, success=False,
                                    blocked=True, outcome=tool_call_outcome)
                                if dispatch_status is ToolDispatchStatus.APPROVAL_REJECTED:
                                    feedback = self._checkpoint_store.approval_feedback(state.approval_id)
                                    # Close the suffix as a single known, non-execution projection.
                                    state.pause_requested = False
                                    state.record_tool_result(tc["id"], tool_call_outcome, result_text, **current_result_facts)
                                    while batch.has_pending_calls:
                                        remaining = batch.tool_calls[batch.next_call_index]
                                        batch.seen_signatures.add((remaining["function"]["name"], remaining["function"]["arguments"]))
                                        text = "[NOT_EXECUTED] 前置操作被用户拒绝，剩余依赖操作未执行；请重新规划。"
                                        append_tool_result(remaining, text)
                                        state.record_tool_result(remaining["id"], "BLOCKED_NOT_EXECUTED", text,
                                                                 **non_execution_facts(NonExecutionReason.BATCH_ABORTED))
                                    defer_replan("用户拒绝了原操作，剩余工具均未执行。用户意见：" + feedback["note"])
                                    self._checkpoint_save("tools")
                                    if feedback["mode"] == "stop":
                                        self._finalize_tool_batch(batch)
                                        self.trace.end_task("CANCELLED")
                                        return self._finish_run(RunStatus.TERMINATED, "已拒绝操作并结束本轮；工具未执行。")
                                    break
                                if advance_current_call(tool_call_outcome, result_text):
                                    return "任务已在安全工具边界暂停，可在同一进程继续。"
                                continue
                            tool_result = result if isinstance(result, ToolResult) else ToolResult(
                                content=str(result), success=not self._is_tool_error(str(result), tname)
                            )
                            # NONE is a Runtime pre-handler fact, never a handler's
                            # claim that an already-started call did not start.
                            if tool_result.effect == "NONE":
                                tool_result.effect = "UNCERTAIN"
                            result_text = tool_result.content
                            not_dispatched = dispatch_status in {
                                ToolDispatchStatus.FEATURE_DISABLED,
                                ToolDispatchStatus.UNKNOWN_TOOL,
                                ToolDispatchStatus.INVALID_ARGUMENTS,
                            }
                            cancelled = False
                            if not_dispatched:
                                current_tool_execution_started = False
                                current_result_facts = non_execution_facts({
                                    ToolDispatchStatus.FEATURE_DISABLED: NonExecutionReason.FEATURE_DISABLED,
                                    ToolDispatchStatus.UNKNOWN_TOOL: NonExecutionReason.UNKNOWN_TOOL,
                                    ToolDispatchStatus.INVALID_ARGUMENTS: NonExecutionReason.ARGUMENTS_REJECTED,
                                }[dispatch_status])
                                v3_block_msg = result_text
                                tool_call_outcome = (
                                    "INVALID_ARGUMENTS"
                                    if dispatch_status is ToolDispatchStatus.INVALID_ARGUMENTS
                                    else f"BLOCKED_{dispatch_status.value.upper()}"
                                )
                            else:
                                current_result_facts = {"result": tool_result.result, "effect": tool_result.effect}
                                cancelled = (
                                    tool_result.cancelled
                                    or result_text.startswith("[用户中断]")
                                )
                                if cancelled:
                                    result_text += (
                                        "\n[执行取消；取消前是否产生部分副作用未知。]"
                                    )
                                    tool_call_outcome = "CANCELLED"
                                else:
                                    tool_call_outcome = (
                                        "EXECUTED_SUCCESS"
                                        if tool_result.execution_success
                                        else "EXECUTED_FAILURE"
                                    )

                            uncertain_effect = (not not_dispatched and not cancelled and
                                (tool_result.result == "UNKNOWN" or
                                 (tool_result.result == "FAILED" and tool_result.effect in {"UNCERTAIN", "PARTIAL"})))
                            if uncertain_effect and getattr(self, "_checkpoint_store", None) is not None:
                                tool_call_outcome = "UNKNOWN"
                                result_text = "[UNKNOWN] 工具已启动，副作用无法确认；禁止自动重试。\n" + result_text
                                current_tool_result_available = True
                                append_tool_result(tc, result_text)
                                current_tool_result_received = True
                                record_tool_result_event(tname, result_text, success=False, blocked=False, outcome="UNKNOWN")
                                state.pause_requested = False
                                state.record_tool_result(tc["id"], "UNKNOWN", result_text, **current_result_facts)
                                finish_tool_batch("工具副作用不确定")
                                self._emit_ui_event("tool_result", name=tname, call_id=tc["id"], success=False,
                                                    blocked=False, outcome="UNKNOWN", **current_result_facts)
                                self.trace.end_task("FAILED")
                                return self._finish_run(RunStatus.FAILED, "UNKNOWN：工具副作用无法确认；请核验或归档，禁止自动重试。")

                            current_tool_result_available = True
                            append_tool_result(tc, result_text)
                            current_tool_result_received = True
                            if not_dispatched:
                                observation_text = result_text
                                record_tool_result_event(
                                    tname,
                                    result_text,
                                    success=False,
                                    blocked=True,
                                    outcome=f"BLOCKED_{dispatch_status.value.upper()}",
                                    output_visibility=tool_result.output_visibility,
                                )
                                logger.warning(
                                    "TOOL_BLOCKED: name=%s reason=%s", tname, result_text,
                                )
                            else:
                                success = not cancelled and bool(tool_result.execution_success)
                                record_tool_result_event(
                                    tname,
                                    result_text,
                                    success=success,
                                    blocked=False,
                                    outcome=(
                                        "CANCELLED" if cancelled
                                        else "EXECUTED_SUCCESS" if success
                                        else "EXECUTED_FAILURE"
                                    ),
                                    cancelled=cancelled,
                                    output_visibility=tool_result.output_visibility,
                                )
                            observation_evidence = ObservationNormalizer.normalize(
                                tname, args, tool_result
                            )
                            observation_text = (
                                observation_evidence.evidence_text or result_text
                            )

                            if not not_dispatched:
                                # Environment classification is evidence only. A
                                # dynamic refusal/package/permission error remains
                                # visible to the analyzer and can recover later.
                                environment_block = self.environment_blocker.classify_result(
                                    observation_text,
                                    failed=(
                                        not bool(tool_result.execution_success)
                                        or observation_evidence.observed_failure
                                    ),
                                )

                                state_guard_message = None
                                after_snapshot = self.workspace_state_guard.snapshot()
                                if state_before is not None:
                                    mutation = self.workspace_state_guard.mutation(
                                        state_before, after_snapshot,
                                    )
                                # WorkspaceStateGuard is an observer; stall policy is
                                # derived from the shared AttemptHistory below.

                                if cancelled:
                                    state.pause_requested = False
                                    state.record_tool_result(
                                        tc["id"], "CANCELLED", result_text,
                                        **current_result_facts,
                                    )
                                    finish_tool_batch("用户取消当前工具")
                                    t_end = time.time()
                                    self.trace.record_tool_call(
                                        tool_name=tname, args_hash=args_hash,
                                        success=False, loop_guard_blocked=False,
                                        error_message=result_text[:200],
                                        result_preview=result_text[:200],
                                        started_at=t_start, finished_at=t_end,
                                        failure_category="USER_CANCELLED",
                                        recoverability="NON_RECOVERABLE",
                                        strategy_fingerprint="",
                                        escalated=False,
                                        circuit_breaker_triggered=False,
                                        cwd=str(self.runtime_context.cwd),
                                        workspace_root=str(self.runtime_context.workspace_root),
                                        session_id=self.runtime_context.shell_session.session_id,
                                    )
                                    self._emit_ui_event(
                                        "tool_result",
                                        name=tname,
                                        success=False,
                                        blocked=False,
                                        call_id=tc["id"],
                                        outcome="CANCELLED",
                                    )
                                    self.session_recorder.record(
                                        "runtime_cancelled",
                                        reason="user_interrupt",
                                        tool=tname,
                                    )
                                    self.trace.end_task("CANCELLED")
                                    return self._finish_run(
                                        RunStatus.CANCELLED,
                                        "已取消当前任务。",
                                    )

                                # ── Failure Intelligence Analysis ──
                                if (
                                    not bool(tool_result.execution_success)
                                    or observation_evidence.observed_failure
                                ):
                                    failure_sig = self.failure_analyzer.analyze(
                                        tool_name=tname,
                                        tool_args=args,
                                        result_text=(
                                            observation_evidence.evidence_text or result_text
                                        ),
                                    )
                                # FailureAnalyzer is stateless. The event stream is
                                # the sole place where this failure is counted.

                    except RuntimeEscalationException as e:
                        # ── V3 hard circuit breaker triggered ──
                        logger.critical(
                            f"[V3] 硬断路器触发，物理终止循环: {e}"
                        )
                        self.trace.record_circuit_breaker()
                        self.trace.record_tool_call(
                            tool_name=tname, args_hash=args_hash,
                            success=False, loop_guard_blocked=False,
                            error_message=str(e)[:200],
                            result_preview=str(e)[:200],
                            started_at=t_start, finished_at=time.time(),
                            failure_category="CIRCUIT_BREAKER",
                            recoverability="NON_RECOVERABLE",
                            strategy_fingerprint="",
                            escalated=False,
                            circuit_breaker_triggered=True,
                            cwd=str(self.runtime_context.cwd),
                            workspace_root=str(self.runtime_context.workspace_root),
                            session_id=self.runtime_context.shell_session.session_id,
                        )
                        finish_tool_batch(
                            "Runtime Policy HARD_STOP",
                            unknown_call_id=(
                                current_tool_call_id
                                if current_tool_execution_started and not current_tool_result_received
                                else ""
                            ),
                        )
                        # Hard terminate the loop
                        self.trace.end_task("CIRCUIT_BROKEN")
                        return self._finish_run(RunStatus.TERMINATED, str(e))

                    t_end = time.time()

                    t_success = (
                        not v3_block_msg
                        and not state_guard_blocked
                        and bool(tool_result.execution_success)
                    )
                    self._update_structured_memory(
                        tname, args, tool_result, t_success,
                    )
                    self.trace.record_tool_call(
                        tool_name=tname, args_hash=args_hash,
                        success=t_success,
                        loop_guard_blocked=bool(v3_block_msg) or state_guard_blocked,
                        error_message="" if t_success else result_text[:200],
                        result_preview=result_text,
                        output_visibility=tool_result.output_visibility,
                        started_at=t_start, finished_at=t_end,
                        execution_success=bool(tool_result.execution_success),
                        observed_failure=observation_evidence.observed_failure,
                        semantic_status=observation_evidence.semantic_status,
                        observation=observation_evidence.observation,
                        exit_code=tool_result.exit_code,
                        segment_exit_codes=tool_result.segment_exit_codes,
                        failure_category=(
                            "STATE_STALLED" if state_guard_blocked
                            else failure_sig.category.value if failure_sig else ""
                        ),
                        recoverability=(
                            "NON_RECOVERABLE" if state_guard_blocked
                            else failure_sig.recoverability.value if failure_sig else ""
                        ),
                        strategy_fingerprint=failure_sig.strategy_fingerprint if failure_sig else "",
                        escalated=failure_sig.escalated if failure_sig else False,
                        circuit_breaker_triggered=state_guard_blocked,
                        cwd=str(self.runtime_context.cwd),
                        workspace_root=str(self.runtime_context.workspace_root),
                        session_id=self.runtime_context.shell_session.session_id,
                    )

                    # Progress-aware evidence is computed after the existing
                    # guards and Failure Intelligence have classified the call.
                    intent = CommandNormalizer.normalize(tname, args)
                    progress_failure_category = (
                        "STATE_STALLED" if state_guard_blocked
                        else failure_sig.category.value if failure_sig else ""
                    )
                    progress_decision = self.runtime_observer.observe(
                        turn=iteration + 1,
                        tool_name=tname,
                        intent_key=intent.to_key(),
                        strategy_fingerprint=(
                            failure_sig.strategy_fingerprint if failure_sig else ""
                        ),
                        success=t_success,
                        result_text=observation_text or result_text,
                        failure_category=progress_failure_category,
                        workspace_before=tracker_state_before,
                        workspace_after=self.workspace_state_guard.snapshot(),
                        workspace_root=str(self.runtime_context.workspace_root),
                        command_blocked=bool(v3_block_msg) or state_guard_blocked,
                        block_reason=(v3_block_msg or "STATE_STALLED"),
                        args_fingerprint=args_hash,
                        duration_ms=max((t_end - t_start) * 1000.0, 0.0),
                        execution_success=bool(tool_result.execution_success),
                        observed_failure=observation_evidence.observed_failure,
                        semantic_status=observation_evidence.semantic_status,
                        observation=observation_evidence.observation,
                        exit_code=tool_result.exit_code,
                        segment_exit_codes=tool_result.segment_exit_codes,
                        resolution_evidence=observation_evidence.resolution_evidence,
                        subject_key=CommandNormalizer.subject_key(tname, args),
                        evidence_ids=observation_evidence.evidence_ids,
                    )
                    self.trace.record_attempt_event(progress_decision.event.to_dict())
                    self.trace.annotate_current_tool(
                        intent_key=intent.to_key(),
                        observation_fingerprint=progress_decision.event.observation_fingerprint,
                        observation_changed=progress_decision.observation_changed,
                        semantic_state=progress_decision.event.semantic_state,
                        progress_detected=progress_decision.progress_detected,
                        progress_reason=list(progress_decision.progress_reason),
                        stagnation_reason=list(progress_decision.stagnation_reason),
                        recovery_stage=progress_decision.recovery_stage.value,
                        open_blocker_count=progress_decision.open_blocker_count,
                        oscillation_detected=progress_decision.oscillation_detected,
                        verification_improved=progress_decision.event.verification_improved,
                        governance_decision=progress_decision.action.value,
                        workspace_before_digest=progress_decision.event.workspace_before_digest,
                        workspace_after_digest=progress_decision.event.workspace_after_digest,
                        changed_paths=list(progress_decision.event.changed_paths),
                        evidence_ids=list(observation_evidence.evidence_ids),
                        governance_evidence_ids=list(progress_decision.event.governance_evidence_ids),
                    )

                    logger.info(
                        "TOOL_RESULT: name=%s success=%s result=%s",
                        tname,
                        t_success,
                        result_text,
                    )
                    self._emit_ui_event(
                        "tool_result",
                        name=tname,
                        success=t_success,
                        blocked=bool(v3_block_msg) or state_guard_blocked,
                        call_id=tc["id"],
                        outcome=tool_call_outcome,
                    )

                    # Log with clean format
                    result_preview = result_text[:200].replace('\n', ' ').strip()
                    if tname == 'bash':
                        cmd = args.get('command', '')[:120].replace('\n', '\\n')
                        logger.info(f"[工具] {tname} | 命令: \"{cmd}\" | 结果: {result_preview}")
                    else:
                        logger.info(f"[工具] {tname} | 结果: {result_preview}")

                    # Store structured tool result (s_full.py style)
                    if progress_decision.should_terminate:
                        state.pause_requested = False
                        state.record_tool_result(
                            tc["id"], tool_call_outcome, result_text,
                            **current_result_facts,
                        )
                        finish_tool_batch(
                            "Runtime Policy HARD_STOP",
                            unknown_call_id=(
                                current_tool_call_id
                                if current_tool_execution_started and not current_tool_result_received
                                else ""
                            ),
                        )
                        self.trace.end_task("CIRCUIT_BROKEN", progress_decision.reason)
                        return self._finish_run(
                            RunStatus.TERMINATED, result_text,
                        )
                    if progress_decision.should_replan:
                        defer_replan(
                            "[Progress Governance] 最近的策略没有产生新的有效 Observation 或 State Progress。"
                            "请重新规划，改变解决路径；不要继续重复同一失败或振荡操作。"
                        )
                    if advance_current_call(tool_call_outcome, result_text):
                        return "任务已在安全工具边界暂停，可在同一进程继续。"

                # Keep all results from this response adjacent. Replan prompts
                # become ordinary conversation messages only after the batch.
                self._finalize_tool_batch(batch)
                active_tool_calls = []
                resolved_tool_call_ids = set()
                active_batch_assistant_index = -1
                current_tool_call_id = ""
                current_tool_execution_started = False
                current_tool_result_received = False
                iteration = state.model_turns_used

            except KeyboardInterrupt:
                if getattr(self, "_checkpoint_faulted", False) or state.status is RunStatus.WAITING_APPROVAL:
                    raise
                if active_tool_calls:
                    finish_tool_batch(
                        "用户中断当前任务",
                        unknown_call_id=(
                            current_tool_call_id
                            if current_tool_execution_started and not current_tool_result_received
                            else ""
                        ),
                    )
                logger.warning("LLM_TASK_CANCELLED: interrupted by user at iteration=%s", iteration + 1)
                self._emit_ui_event("task_cancelled")
                self.session_recorder.record(
                    "runtime_cancelled",
                    reason="user_interrupt",
                    stage="llm_turn",
                    iteration=iteration + 1,
                )
                self.trace.record_runtime_error("用户中断当前任务")
                self.trace.end_task("CANCELLED")
                return self._finish_run(RunStatus.CANCELLED, "已停止当前任务。")
            except Exception as e:
                if getattr(self, "_checkpoint_faulted", False) or state.status is RunStatus.WAITING_APPROVAL:
                    raise
                if active_tool_calls:
                    finish_tool_batch(
                        "工具批次异常终止",
                        unknown_call_id=(
                            current_tool_call_id
                            if current_tool_execution_started and not current_tool_result_received
                            else ""
                        ),
                        failed=True,
                    )
                self.last_metrics["api_errors"] += 1
                logger.exception("LLM_TURN_ERROR: iteration=%s", iteration + 1)
                self._emit_ui_event("runtime_error", message=str(e)[:200])
                diagnostic = getattr(provider, "last_error_diagnostic", None)
                if diagnostic:
                    self.trace.record_provider_diagnostic(diagnostic)
                    self.session_recorder.record("provider_diagnostic", **diagnostic)
                    chain = diagnostic.get("exception_chain") or [{}]
                    error_message = (
                        f"provider={diagnostic.get('provider')} "
                        f"category={diagnostic.get('error_category')} "
                        f"endpoint_host={diagnostic.get('endpoint_host')} "
                        f"proxy_present={diagnostic.get('proxy_present')} "
                        f"root={chain[0].get('message', '')}"
                    )
                else:
                    error_message = str(e)
                self.session_recorder.record("runtime_error", message=error_message)
                logger.error(f"LLM 循环出错: {error_message}")
                self.trace.record_runtime_error(error_message)
                self.trace.end_task("FAILED")
                return self._finish_run(RunStatus.FAILED, f"错误: {error_message}")

        logger.warning(f"模型调用轮次已达最大次数 ({max_iterations})，强制终止")
        self.trace.end_task("LOOP_ABORTED", "GLOBAL_ITERATION_LIMIT")
        return self._finish_run(
            RunStatus.FAILED,
            f"错误: 模型调用已达 {max_iterations} 轮上限，任务未完成。",
        )

    def _parse_response(self, provider, response: Any) -> Dict[str, Any]:
        """Parse provider response safely."""
        if hasattr(provider, 'parse_response'):
            return provider.parse_response(response)
        return {'content': str(response), 'tool_calls': [], 'usage': {}}

    # ═══════════════════════════════════════════════════════════
    # High-Level API
    # ═══════════════════════════════════════════════════════════

    def chat(self, message: str, require_tool_call: bool = False) -> str:
        """Submit a user instruction to the Agent Runtime."""
        state = getattr(self, "_run_state", None)
        if state and state.status in {RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.WAITING_APPROVAL}:
            return "错误: 当前运行尚未完成；请先继续或终止该运行。"
        self.session_recorder.start_round()
        logger.info("USER_INPUT: %s", message)
        self.session_recorder.record("user_input", content=message)
        result = self.run(message, require_tool_call=require_tool_call)
        state = getattr(self, "_run_state", None)
        logger.info("AGENT_RESPONSE: %s", result)
        # A final model response only means the conversation turn ended; the
        # benchmark trace remains the authority for task success.
        final_event = {
            "status": ("WAITING_APPROVAL" if state and state.status is RunStatus.WAITING_APPROVAL
                       else "PAUSED" if state and state.status is RunStatus.PAUSED else "RESPONSE"),
            "run_id": state.run_id if state else "",
        }
        if result == self._last_assistant_note:
            final_event["content_reused"] = True
        else:
            final_event["content"] = result
        self.session_recorder.record("final", **final_event)
        return result

    def create_task(self, title: str, description: str = "",
                    priority: int = 1) -> Task:
        """Create a new task."""
        task = Task(title=title, description=description, priority=priority)
        return self.task_manager.create(task)

    def spawn_teammate(self, name: str, role: str = "worker",
                       system_prompt: str = "") -> Teammate:
        """Create a new AI teammate."""
        try:
            role_enum = TeammateRole(role)
        except ValueError:
            role_enum = TeammateRole.WORKER
        return self.team_manager.create(name, role_enum, system_prompt)

    def run_background(self, command: str, description: str = "",
                       timeout: int = 120) -> str:
        """Execute a command in the background. Returns task ID or an error."""
        if getattr(self, "_checkpoint_store", None) is not None:
            return "错误: Checkpoint 模式不支持后台任务执行或恢复"
        capability_error = self._background_capability_error()
        if capability_error:
            return capability_error
        return self.background.run(
            command,
            description,
            timeout,
            cwd=str(self.runtime_context.cwd),
        )

    def run_subagent(self, prompt: str, agent_type: str = "general-purpose"
                     ) -> SubAgentResult:
        """Run a sub-agent with the given prompt."""
        if getattr(self, "_checkpoint_store", None) is not None:
            return "错误: Checkpoint 模式不支持子 Agent 执行栈恢复"
        try:
            atype = SubAgentType(agent_type)
        except ValueError:
            atype = SubAgentType.GENERAL
        return self.subagent_manager.run(prompt, atype)

    def send_message(self, recipient: str, content: str,
                     priority: str = "normal") -> str:
        """Send a direct message to a teammate."""
        try:
            p = MessagePriority[priority.upper()]
        except KeyError:
            p = MessagePriority.NORMAL
        msg = BusMessage(sender=self.config.agent.name, recipient=recipient,
                         content=content, priority=p)
        return self.message_bus.send(msg)

    # ═══════════════════════════════════════════════════════════
    # Lifecycle
    # ═══════════════════════════════════════════════════════════

    def shutdown(self):
        """Graceful shutdown of all systems."""
        logger.info("正在关闭 MiniClaudeAgent...")
        self.background.stop()
        self.team_manager.shutdown_all()
        self._running = False
        store = getattr(self, "_checkpoint_store", None)
        if store is not None:
            store.close()
        logger.info("关闭完成。")
