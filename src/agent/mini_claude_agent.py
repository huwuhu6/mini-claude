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
from core.run_snapshot import (
    SCHEMA_VERSION, SNAPSHOT_FIELDS, decode_messages, decode_run, encode_run,
    fields as snapshot_fields, json_copy, require as snapshot_require, text as snapshot_text,
)
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
                 runtime_data_root: Optional[Path] = None):
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
        current = getattr(self, "_run_state", None)
        if current and current.status in {RunStatus.RUNNING, RunStatus.PAUSED}:
            return "错误: 当前运行尚未完成；请先继续或终止该运行。"
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
        sources = ["agent/mini_claude_agent.py", "core/run_state.py", "core/run_snapshot.py",
                   "core/loop_controller.py", "core/tools/base_tools.py", "core/tools/registry.py",
                   "core/runtime_context/shell_session.py", "core/runtime_context/command_policy.py",
                   "core/runtime_context/workspace_authority.py", "core/runtime_context/path_resolver.py",
                   "core/features/manager.py", "core/compression.py",
                   "core/runtime_context/workspace_state.py", "core/runtime_context/observation.py",
                   "core/failure_intelligence/analyzer.py", "core/failure_intelligence/signatures.py",
                   "core/failure_intelligence/models.py", "core/agent_note.py", "models/todo.py"]
        source_hash = hashlib.sha256()
        for relative in sources:
            source_hash.update(relative.encode("utf-8"))
            source_hash.update((runtime_root / relative).read_text(encoding="utf-8").encode("utf-8"))
        return {
            "runtime_source_hash": source_hash.hexdigest(),
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

    def _validate_run_snapshot(self, raw: dict):
        """Pure preparation: no live state, files, trace or authorization is overwritten."""
        snapshot = json_copy(raw)
        snapshot_fields(snapshot, SNAPSHOT_FIELDS, "Snapshot")
        snapshot_require(type(snapshot["schema_version"]) is int
                         and snapshot["schema_version"] == SCHEMA_VERSION, "快照版本不兼容")
        snapshot_require(snapshot["compatibility"] == self._snapshot_compatibility(),
                         "工具实现、Schema 或关键运行配置不兼容")
        workspace = snapshot["workspace"]
        snapshot_fields(workspace, {"root", "cwd", "content_digest"}, "Workspace")
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
        state = decode_run(snapshot["run"])
        snapshot_require(state.workspace_checkpoint_digest in {None, workspace["content_digest"]},
                         "Run 的工作区前置条件与快照冲突")
        messages = decode_messages(snapshot["messages"], state)
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
        from dataclasses import asdict
        snapshot = json_copy({
            "schema_version": SCHEMA_VERSION,
            "workspace": {"root": str(self.runtime_context.workspace_root),
                          "cwd": str(self.runtime_context.cwd.resolve()),
                          "content_digest": self.workspace_state_guard.content_digest()},
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
        self._validate_run_snapshot(snapshot)  # Refuse inconsistent live state, never repair it.
        return snapshot

    def restore_run_snapshot(self, snapshot: dict) -> None:
        """Install validated detached state; caller explicitly continues the paused Run."""
        snapshot_require(not self.has_active_run, "已有未完成 Run，不能被快照覆盖")
        snapshot_require(not self.background.list(), "恢复目标有独立后台任务")
        data, state, messages, todo, history, cwd = self._validate_run_snapshot(snapshot)
        seen_ids = getattr(self, "_seen_run_ids", set())
        snapshot_require(state.run_id not in seen_ids
                         and (self._run_state is None or self._run_state.run_id != state.run_id),
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
    def has_pending_tool_batch(self) -> bool:
        state = getattr(self, "_run_state", None)
        return bool(state and state.has_pending_tool_batch)

    @property
    def has_active_run(self) -> bool:
        state = getattr(self, "_run_state", None)
        return bool(state and state.status in {RunStatus.RUNNING, RunStatus.PAUSED})

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
        if state.status in {
            RunStatus.COMPLETED,
            RunStatus.TERMINATED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            return state.terminal_result or ""
        if state.status is not RunStatus.PAUSED:
            return "错误: 当前运行未处于可继续的暂停状态。"

        try:
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
            raise
        result = self._llm_tool_cycle(
            max_iterations=state.max_iterations,
            require_tool_call=state.require_tool_call,
            _continuing=True,
        )
        if state.status is not RunStatus.PAUSED:
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

    def _finish_run(self, status: RunStatus, result: str) -> str:
        state = getattr(self, "_run_state", None)
        if state is not None:
            if state.has_pending_tool_batch and state.tool_batch is not None:
                state.tool_batch.mark_terminal(failed=status is RunStatus.FAILED)
            state.finish(status, result)
        return result

    def set_ui_event_handler(
        self, handler: Optional[Callable[[str, Dict[str, Any]], None]]
    ) -> None:
        """Attach a presentation callback without coupling the runtime to a UI."""
        self._ui_event_handler = handler

    def _emit_ui_event(self, event: str, **data: Any) -> None:
        if self._ui_event_handler is None:
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
            return str(target)[:100] if target else ""
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
        return self.feature_manager.filter_tools(self.tool_registry.definitions())

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

    def _execute_tool(self, name: str, args: Dict[str, Any]) -> ToolDispatchResult:
        """Check current capability, then dispatch through the Main Agent registry."""
        if not self.tool_registry.has_handler(name):
            return ToolDispatchResult(
                ToolDispatchStatus.UNKNOWN_TOOL,
                f"错误: 未知工具 '{name}'",
            )
        if not self.feature_manager.is_tool_enabled(name):
            return ToolDispatchResult(
                ToolDispatchStatus.FEATURE_DISABLED,
                ToolResult(
                    content=f"错误: 工具 '{name}' 所属功能当前未启用",
                    success=False,
                    execution_success=False,
                ),
            )
        try:
            value = self.tool_registry.execute(name, args)
            return ToolDispatchResult(ToolDispatchStatus.DISPATCHED, value)
        except RuntimeEscalationException:
            # Runtime control signals must reach the loop's control handler.
            raise
        except ToolArgumentBindingError as e:
            logger.error(f"工具 '{name}' 参数错误: {e}")
            return ToolDispatchResult(
                ToolDispatchStatus.INVALID_ARGUMENTS,
                f"错误: 工具 '{name}' 参数无效。详情: {str(e)}",
            )
        except Exception as e:
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

    def _llm_tool_cycle(
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
                    active_batch_assistant_index = batch.assistant_message_index
                active_tool_calls = batch.tool_calls
                resolved_tool_call_ids = batch.resolved_call_ids

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
                    )
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
                        if advance_current_call("SKIPPED_DUPLICATE", result_text):
                            return "任务已在安全工具边界暂停，可在同一进程继续。"
                        continue
                    seen_tool_sigs.add(sig)
                    logger.info("TOOL_REQUEST: name=%s raw_args=%s", tname, args_raw)

                    try:
                        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    except (json.JSONDecodeError, TypeError) as exc:
                        # A malformed model argument should become tool feedback,
                        # not terminate the entire agent task.
                        result_text = f"工具参数 JSON 无效，请重新生成合法 JSON: {exc}"
                        tool_call_outcome = "INVALID_ARGUMENTS"
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
                            current_tool_result_available = True
                            append_tool_result(tc, result_text)
                            current_tool_result_received = True
                            if decision.should_terminate:
                                state.pause_requested = False
                                state.record_tool_result(
                                    tc["id"], "BLOCKED_PRECHECK", result_text,
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
                            tool_result = result if isinstance(result, ToolResult) else ToolResult(
                                content=str(result), success=not self._is_tool_error(str(result), tname)
                            )
                            result_text = tool_result.content
                            not_dispatched = dispatch_status in {
                                ToolDispatchStatus.FEATURE_DISABLED,
                                ToolDispatchStatus.UNKNOWN_TOOL,
                                ToolDispatchStatus.INVALID_ARGUMENTS,
                            }
                            cancelled = False
                            if not_dispatched:
                                current_tool_execution_started = False
                                v3_block_msg = result_text
                                tool_call_outcome = (
                                    "INVALID_ARGUMENTS"
                                    if dispatch_status is ToolDispatchStatus.INVALID_ARGUMENTS
                                    else f"BLOCKED_{dispatch_status.value.upper()}"
                                )
                            else:
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
        if state and state.status in {RunStatus.RUNNING, RunStatus.PAUSED}:
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
            "status": "PAUSED" if state and state.status is RunStatus.PAUSED else "RESPONSE",
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
        logger.info("关闭完成。")
