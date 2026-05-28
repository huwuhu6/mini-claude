"""
Mini Claude Agent - Unified agent integrating all systems.
"""
from __future__ import annotations
import logging
import os
import sys
import json
import re
import uuid
import time
import shutil
from collections import Counter
from pathlib import Path
from typing import List, Dict, Any, Optional

# Ensure src is in path
_project_root = Path(__file__).parent.parent.parent
_src_path = str(_project_root / 'src')
if _src_path not in sys.path:
    sys.path.insert(0, _src_path)

from models.config import ConfigManager, Config
from models.task import Task, TaskManager, TaskStatus
from models.teammate import Teammate, TeammateStatus, TeammateRole
from models.todo import TodoManager

from providers.base import Message, ToolDefinition as ProviderToolDef
from providers.manager import ProviderManager

from core.tools.base_tools import BaseTools, ToolResult
from core.features import FeatureManager, FeatureDefinition, FeatureDependency
from core.messaging import MessageBus, Message as BusMessage, MessagePriority
from core.teammate_manager import TeammateManager, TeammateConfig
from core.background import BackgroundProcessor, BackgroundTaskStatus
from core.subagent import SubAgentManager, SubAgentType, SubAgentResult
from core.console import ConsoleCommandSystem, Command
from core.compression import Compressor
from core.loop_guard import LoopGuard, canonicalize_args
from core.loop_controller import LoopController, RuntimeEscalationException, CommandNormalizer
from core.tracing import TraceManager
from core.failure_intelligence import FailureAnalyzer, FailureMemory, FailureEscalationPolicy, build_escalation_message
from core.runtime_context import RuntimeContext
from cli.authority import WorkspaceAuthority
from skills import SkillLoader

logger = logging.getLogger(__name__)


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
    - Console commands
    - Context compression
    """

    def __init__(self, config_path: Optional[Path] = None,
                 workspace_root: Optional[Path] = None,
                 workdir: Optional[Path] = None,
                 workspace_confirmed: bool = False):
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

        self._setup_logging()
        logger.info(f"正在初始化 MiniClaudeAgent v{self.config.agent.version}")

        # Workspace Authority — unified permission boundary
        self.workspace_authority = WorkspaceAuthority(primary_root=self.workdir)

        # Core tools (delegates whitelist to WorkspaceAuthority when available)
        self.tools = BaseTools(self.workdir, authority=self.workspace_authority)

        # Runtime context — workspace binding, path resolution, shell session
        self.runtime_context = RuntimeContext(workspace_root=self.workdir)

        # Tool dispatcher — dict-based routing bound once at init (s_full.py TOOL_HANDLERS pattern)
        self.tool_dispatcher = {
            "bash": self._handle_bash,
            "read_file": self._handle_read_file,
            "write_file": self._handle_write_file,
            "edit_file": self._handle_edit_file,
            "load_skill": self._handle_load_skill_dispatch,
            "task": self._handle_task,
            "TodoWrite": self._handle_todo_write,
            "search_code": self._handle_search_code,
            "count_occurrences": self._handle_count_occurrences,
            "syntax_check": self._handle_syntax_check,
            "verify_symbol_rename": self._handle_verify_symbol_rename,
        }

        # In-memory todo tracker (s_full.py s03 Nag system)
        self.todo = TodoManager()

        # Feature management
        self.feature_manager = FeatureManager()
        self._register_features()

        # LLM Providers
        self.provider_manager = ProviderManager()
        self._setup_providers()

        # Task management
        tasks_dir = self.workdir / self.config.tasks.directory
        self.task_manager = TaskManager(tasks_dir)

        # Team management
        team_config = TeammateConfig(
            directory=str(self.workdir / self.config.team.directory),
            idle_timeout=self.config.team.idle_timeout,
            auto_claim_tasks=self.config.team.auto_claim_tasks,
        )
        self.team_manager = TeammateManager(team_config)

        # Message bus
        bus_dir = self.workdir / ".inbox"
        self.message_bus = MessageBus(storage_dir=bus_dir)

        # Background processing
        self.background = BackgroundProcessor(
            max_concurrent=self.config.background.max_concurrent,
            notification_queue_size=self.config.background.notification_queue_size,
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

        # Console commands
        self.console = ConsoleCommandSystem()
        self._register_commands()

        # Compression
        compression_config = {
            'token_threshold': self.config.compression.token_threshold,
            'max_transcripts': self.config.compression.max_transcripts,
            'microcompact_threshold': self.config.compression.microcompact_threshold,
            'transcript_dir': str(self.workdir / '.transcripts'),
        }
        self.compressor = Compressor(compression_config)

        # Inject LLM provider for real AI-powered summarization
        primary = self.provider_manager.get_primary_provider()
        if primary:
            self.compressor.set_provider(primary)

        # Conversation state
        self.messages: List[Message] = []
        self._running = False

        # Failure Intelligence Layer (must init before V3 Loop Controller)
        self.failure_analyzer = FailureAnalyzer()
        self.failure_memory = FailureMemory()
        self.failure_policy = FailureEscalationPolicy()

        # V3 Loop Controller — combined intent normalization + guard + circuit breaker
        self.loop_controller = LoopController(
            failure_memory=self.failure_memory,
            strike_limit=5,
        )
        # Legacy loop guard kept for backward compatibility (trace schema)
        self.loop_guard = LoopGuard()
        # Runtime trace system — append-only, hook-based observability
        self.trace = TraceManager(trace_dir=self.workdir / ".traces")
        # Benchmark metrics — reset each _llm_tool_cycle call
        self.last_metrics: Dict[str, int] = {"turns": 0, "total_tokens": 0, "api_errors": 0}
        # Tracks consecutive identical command executions for soft prompting
        # (Replaced by V3 intent-based LoopGuard in loop_controller.py)
        self._cmd_history: List[tuple] = []


        # Tracks current user prompt for trace recording
        self._current_user_prompt: str = ""
        # Workspace confirmation state for trace schema
        self._workspace_confirmed = workspace_confirmed

        # Start background processor
        self.background.start()

        # Load system prompt
        self._load_system_prompt()

    def _setup_logging(self):
        """Configure logging based on config."""
        log_level = getattr(logging, self.config.logging.level.upper(), logging.INFO)
        log_file = self.workdir / self.config.logging.file

        log_file.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=log_level,
            format=self.config.logging.format,
            handlers=[
                logging.FileHandler(str(log_file), encoding='utf-8'),
                logging.StreamHandler(sys.stdout),
            ]
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
            name='tasks', description='Task management system',
            category='core', enabled=features_config.tasks,
        ))
        self.feature_manager.register_feature(FeatureDefinition(
            name='compression', description='Context compression',
            category='core', enabled=features_config.compression,
        ))
        self.feature_manager.register_feature(FeatureDefinition(
            name='background', description='Background command execution',
            category='core', enabled=features_config.background,
        ))
        self.feature_manager.register_feature(FeatureDefinition(
            name='team', description='AI teammate system',
            category='advanced', enabled=features_config.team,
            dependencies=[FeatureDependency('tasks')],
        ))
        self.feature_manager.register_feature(FeatureDefinition(
            name='skills', description='Skill modules',
            category='advanced', enabled=features_config.skills,
        ))
        # Register tool→feature mapping for feature-aware tool filtering
        self.feature_manager.register_tool_for_feature('load_skill', 'skills')

    def _setup_providers(self):
        """Setup LLM providers from config."""
        llm = self.config.llm

        # Resolve api_key and base_url: env var → config → default
        if llm.provider == 'deepseek':
            api_key = os.getenv('DEEPSEEK_API_KEY', llm.api_key or '')
            base_url = os.getenv('DEEPSEEK_BASE_URL',
                                 llm.base_url or 'https://api.deepseek.com')
        elif llm.provider == 'anthropic':
            api_key = os.getenv('ANTHROPIC_API_KEY', llm.api_key or '')
            base_url = os.getenv('ANTHROPIC_BASE_URL',
                                 llm.base_url or 'https://api.anthropic.com')
        else:
            api_key = ''
            base_url = ''

        provider_config = {
            'model': llm.model,
            'max_tokens': llm.max_tokens,
            'temperature': llm.temperature,
            'api_key': api_key,
            'base_url': base_url,
        }

        if api_key:
            try:
                self.provider_manager.create_provider(
                    llm.provider, provider_config, is_primary=True
                )
                logger.info(f"提供者 '{llm.provider}' 创建成功")
            except Exception as e:
                logger.warning(f"创建提供者 '{llm.provider}' 失败: {e}")
                logger.info("正在无提供者模式下运行（功能受限）")

    def _load_system_prompt(self):
        """Load or generate the system prompt."""
        features = self.feature_manager.get_enabled_features()
        skills_text = ""
        if self.feature_manager.is_enabled('skills') and hasattr(self, 'skill_loader'):
            desc = self.skill_loader.descriptions()
            if desc:
                skills_text = f"\nAvailable skill modules (use load_skill to access):\n{desc}"
        self.system_prompt = (
        f"You are {self.config.agent.name} v{self.config.agent.version}, "
        f"an AI assistant with tools and team capabilities.\n"
        f"Enabled features: {', '.join(features) if features else 'base'}.\n"
        f"Working directory: {self.workdir}"
        f"{skills_text}\n"
        "You can use tools to read/write files, run commands, and manage tasks.\n"
        "\n"
        "CRITICAL RULES FOR WINDOWS ENVIRONMENT:\n"
        "1. NO INLINE SCRIPTS: Never use `python -c \"...\"` or `node -e \"...\"` "
        "in the bash tool. Windows CMD cannot handle nested quotes and newlines properly.\n"
        "2. SCRIPT WORKFLOW: If you need to run complex logic or multi-line code, "
        "you MUST first use `write_file` to save the code to a temporary file "
        "(e.g., script.py), and then use `bash` to execute that file "
        "(e.g., `python script.py`).\n"
        "3. VERIFY RESULTS STRICTLY: Do not assume a task is complete just because "
        "a file exists or an exit code is 0. Verify the content or check the latest "
        "modification timestamp to ensure your action actually succeeded.\n"
        "4. USE SEARCH_CODE FOR FILE SEARCHING: When you need to search for patterns "
        "in code or files, always use the `search_code` tool. Do NOT call grep, "
        "findstr, or Select-String via the bash tool for file content searching.\n"
        "\n"
        "TASK TRACKING WITH TodoWrite:\n"
        "- For any multi‑step task, update your plan via `TodoWrite` BEFORE taking action.\n"
        "- Every entry must describe the GOAL (e.g., \"Rename parameter X to Y in all files\", "
        "\"Confirm no remaining old symbols\"). Never describe the tool or method.\n"
        "- Mark the current step as `in_progress`, update it to `completed` when done, "
        "and move to the next.\n"
        "\n"
        "VERIFICATION STRATEGY (MUST FOLLOW):\n"
        "\n"
        "1. CATEGORIZE THE TASK:\n"
        "   - Purely structural changes (rename, import cleanup, dead code removal, "
        "signature migration): these require only static verification.\n"
        "   - Logic changes, bug fixes, new features: may need runtime verification.\n"
        "\n"
        "2. FOR STRUCTURAL CHANGES, USE STATIC VERIFICATION ONLY:\n"
        "   - `verify_symbol_rename` (scope=code_only) for Python rename/refactor tasks.\n"
        "   - `syntax_check` to ensure files still parse correctly.\n"
        "   - `count_occurrences` to confirm old patterns are gone (especially non‑Python).\n"
        "   - When these all pass, the task is COMPLETE. Stop immediately – no further actions.\n"
        "\n"
        "3. RUNTIME VERIFICATION IS ALLOWED ONLY WHEN:\n"
        "   - The user explicitly requests it, OR the task cannot be validated statically "
        "(e.g., runtime behavior, integration, bug fixes).\n"
        "   - In those rare cases, keep any temporary file small and self‑contained.\n"
        "\n"
        "4. SUCCESS DEFINITION FOR STRUCTURAL TASKS:\n"
        "   - All requested files modified correctly.\n"
        "   - `verify_symbol_rename` returns confidence=high and task_complete_likely=true "
        "(or `count_occurrences` returns 0 for removed patterns).\n"
        "   - `syntax_check` reports no errors.\n"
        "   - No further steps required.\n"
    )

    def _register_commands(self):
        """Register console commands."""
        self.console.register(Command(
            'status', 'Show system status', self._cmd_status, category='system'
        ))
        self.console.register(Command(
            'stats', 'Show performance statistics', self._cmd_stats, category='system'
        ))
        self.console.register(Command(
            'config', 'Show current configuration', self._cmd_config, category='system'
        ))
        self.console.register(Command(
            'tasks', 'List all tasks', self._cmd_tasks,
            args_help='[status]', aliases=['task'], category='tasks'
        ))
        self.console.register(Command(
            'team', 'List all teammates', self._cmd_team,
            args_help='[status]', category='team'
        ))
        self.console.register(Command(
            'inbox', 'Read messages from inbox', self._cmd_inbox, category='team'
        ))
        self.console.register(Command(
            'features', 'List feature flags and status', self._cmd_features,
            args_help='[enable/disable] [name]', category='system'
        ))
        self.console.register(Command(
            'compact', 'Manually compress conversation', self._cmd_compact, category='general'
        ))
        self.console.register(Command(
            'providers', 'Show provider information', self._cmd_providers, category='system'
        ))
        self.console.register(Command(
            'add_workdir', 'Add a directory to path validation whitelist',
            self._cmd_add_workdir,
            args_help='<path>', aliases=['addpath'], category='system'
        ))
        self.console.register(Command(
            'whitelist', 'List all paths in the validation whitelist',
            self._cmd_whitelist,
            aliases=['workdirs'], category='system'
        ))
        self.console.register(Command(
            'skills', 'List all available skill modules',
            self._cmd_skills,
            args_help='[name]', aliases=['skill'], category='system'
        ))

    # ═══════════════════════════════════════════════════════════
    # Main Agent Loop
    # ═══════════════════════════════════════════════════════════

    def run(self, user_input: str) -> str:
        """Process user input and return agent response."""
        self._current_user_prompt = user_input
        # Check for compressed mode
        if not self.feature_manager.is_enabled('tasks'):
            return self._run_simple(user_input)

        return self._run_with_tasks(user_input)

    def _run_simple(self, user_input: str) -> str:
        """Simple execution without task management."""
        self.messages.append(Message(role='user', content=user_input))
        return self._llm_tool_cycle()

    def _run_with_tasks(self, user_input: str) -> str:
        """Execution with task management."""
        self.messages.append(Message(role='user', content=user_input))

        # Check for compression
        if self.feature_manager.is_enabled('compression'):
            if self.compressor.should_compress(self.messages):
                self.messages = self.compressor.compress(self.messages)
            elif self.compressor.should_microcompact(self.messages):
                self.messages = self.compressor.microcompact(self.messages)

        return self._llm_tool_cycle()

    def _get_llm_tools(self) -> List[Dict[str, Any]]:
        """Get tool definitions filtered by feature flags."""
        all_tools = [
            {
                'name': 'bash',
                'description': 'Run a shell command.',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'command': {'type': 'string', 'description': 'The command to run'},
                    },
                    'required': ['command'],
                },
            },
            {
                'name': 'read_file',
                'description': '读取文件的指定行范围（视窗读取）。对长文件请务必使用 start_line/end_line 限定行号范围，以节省 Token 并提升聚焦度。不传参则读取全文。行号从 1 开始计数。',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'path': {'type': 'string', 'description': '文件路径'},
                        'start_line': {'type': 'integer', 'description': '起始行号（包含），从 1 开始。不传则从头读取'},
                        'end_line': {'type': 'integer', 'description': '结束行号（包含）。不传则读到文件末尾'},
                    },
                    'required': ['path'],
                },
            },
            {
                'name': 'write_file',
                'description': 'Write content to a file.',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'path': {'type': 'string', 'description': 'Path to the file'},
                        'content': {'type': 'string', 'description': 'Content to write'},
                    },
                    'required': ['path', 'content'],
                },
            },
            {
                'name': 'edit_file',
                'description': 'Apply multiple precise text replacements in a single file atomically. Use this for batch edits — all replacements commit together or none at all.',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'path': {'type': 'string', 'description': 'Path to the file'},
                        'edits': {
                            'type': 'array',
                            'items': {
                                'type': 'object',
                                'properties': {
                                    'search': {'type': 'string', 'description': 'Exact text fragment to find (single occurrence)'},
                                    'replace': {'type': 'string', 'description': 'Replacement text'},
                                },
                                'required': ['search', 'replace'],
                            },
                            'description': 'Array of search/replace pairs. Applied in order, atomically. If ANY search fails, ALL edits roll back.',
                        },
                    },
                    'required': ['path', 'edits'],
                },
            },
            {
                'name': 'load_skill',
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
                'description': '在指定文件或目录中搜索正则表达式模式。跨平台纯 Python 实现，自动忽略 .git/__pycache__/node_modules 等目录。优先使用此工具进行代码搜索，而不是编写 Python 脚本或调用 grep/findstr。',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'paths': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': '文件或目录路径列表，支持通配符（*、**）。例如 ["*.py", "src/"]',
                        },
                        'patterns': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': '要搜索的正则表达式列表（OR关系）。例如 ["user_id", "user_ids"]',
                        },
                        'context_lines': {
                            'type': 'integer',
                            'description': '显示匹配行前后各 N 行上下文（类似 grep -C）。默认 0',
                        },
                        'case_sensitive': {
                            'type': 'boolean',
                            'description': '是否区分大小写。默认 false',
                        },
                        'max_matches': {
                            'type': 'integer',
                            'description': '最大匹配行数，超过则截断并提示。默认 50',
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
                    'required': ['paths', 'patterns'],
                },
            },
            {
                'name': 'count_occurrences',
                'description': '统计正则表达式模式在多个文件中的出现次数。返回每个 pattern 的总匹配数和文件级分布。比 search_code 更轻量，适合验证 rename/refactor 的完成度。',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'paths': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': '文件或目录路径列表，支持通配符',
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
                    'required': ['paths', 'patterns'],
                },
            },
            {
                'name': 'syntax_check',
                'description': '快速检查 Python 源文件是否存在语法错误。使用 ast.parse 进行轻量结构验证。用于 rename/refactor 后确认文件仍可被正确解析。',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'paths': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': '要检查的 Python 文件或目录路径列表',
                        },
                    },
                    'required': ['paths'],
                },
            },
            {
                'name': 'verify_symbol_rename',
                'description': '验证符号重命名/重构是否结构性完成。使用 AST 进行语义分析，支持 scope（code_only/all）和 targets（指定函数级验证范围）。对于简单 rename 任务优先使用此工具而非生成 verify.py。',
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'old_symbols': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': '不应再作为标识符出现的旧符号列表',
                        },
                        'new_symbols': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': '预期出现的新符号列表（正向确认）',
                        },
                        'paths': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': '要检查的 Python 文件或目录路径列表',
                        },
                        'scope': {
                            'type': 'string',
                            'enum': ['code_only', 'all'],
                            'description': "验证范围: code_only（默认）忽略 docstring/注释/字符串字面量中的残留；all 将全部视作有意义残留",
                        },
                        'targets': {
                            'type': 'array',
                            'items': {
                                'type': 'object',
                                'properties': {
                                    'file': {'type': 'string', 'description': '目标文件路径'},
                                    'function': {'type': 'string', 'description': '目标函数名'},
                                    'old': {'type': 'string', 'description': '旧符号'},
                                    'new': {'type': 'string', 'description': '新符号'},
                                },
                                'required': ['file', 'function', 'old', 'new'],
                            },
                            'description': '目标函数列表（可选）。提供后仅验证指定函数内的符号重命名，其他函数中的旧符号不影响 verdict。',
                        },
                    },
                    'required': ['old_symbols', 'new_symbols', 'paths'],
                },
            },
            {
                'name': 'TodoWrite',
                # 外层 description 保持精简，定下“目标导向”的基调
                'description': (
                    'Update the task tracking list. Describe GOALS, not implementations. '
                    'Use to plan and track progress through complex multi-step tasks. '
                    'Max 20 items, only one in_progress at a time.'
                ),
                'input_schema': {
                    'type': 'object',
                    'properties': {
                        'items': {
                            'type': 'array',
                            'items': {
                                'type': 'object',
                                'properties': {
                                    # 【核心修改 1】在 content 字段直接拦截 verify.py
                                    'content': {
                                        'type': 'string', 
                                        'description': (
                                            'Task goal. Prefer: "Verify refactor", "Check consistency". '
                                            'AVOID: "Write verify.py", "Run test script" for simple tasks '
                                            '(renames/cleanups). Only plan runtime tests for bugs/features.'
                                        )
                                    },
                                    'status': {
                                        'type': 'string', 
                                        'enum': ['pending', 'in_progress', 'completed'], 
                                        'description': 'Task status'
                                    },
                                    # 【核心修改 2】防止 activeForm 出现 "Writing verify.py"
                                    'activeForm': {
                                        'type': 'string', 
                                        'description': (
                                            'Present continuous form of the GOAL (e.g. "Verifying refactor", '
                                            'NOT "Writing verify.py")'
                                        )
                                    },
                                },
                                'required': ['content', 'status', 'activeForm'],
                            },
                            'description': 'List of todo items',
                        },
                    },
                    'required': ['items'],
                },
            },
        ]

        return self.feature_manager.filter_tools(all_tools)

    # ── Tool Handler Methods (bound once in __init__.tool_dispatcher) ──

    def _handle_bash(self, command: str) -> str:
        # Block direct grep/findstr/Select-String — redirect to search_code
        cmd_stripped = command.strip().lower()
        for blocked in ('grep ', 'findstr ', 'select-string '):
            if cmd_stripped.startswith(blocked):
                return (
                    "[Tip: 请使用 search_code 工具进行文件内容搜索，"
                    "而非 bash 命令中的 grep/findstr。\n"
                    f"search_code 是跨平台纯 Python 实现，"
                    f"且具有路径安全保护。\n"
                    f"被拦截的命令: {command[:200]}]"
                )
        result = self.runtime_context.shell_session.execute(command)
        return result["content"]

    def _handle_read_file(self, path: str, start_line: int = None, end_line: int = None) -> str:
        result = self.tools.read_file(path, start_line, end_line)
        return result.content

    def _handle_write_file(self, path: str, content: str) -> str:
        result = self.tools.write_file(path, content)
        return result.content

    def _handle_edit_file(self, path: str, edits: list) -> str:
        result = self.tools.edit_file(path, edits)
        return result.content

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
        shadow_root = self.workdir / ".claude" / "shadow"
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
        """Update the in-memory todo list and return its rendered state."""
        return self.todo.update(items)

    def _handle_search_code(self, paths: list, patterns: list,
                             context_lines: int = 0,
                             case_sensitive: bool = False,
                             max_matches: int = 50,
                             include_filename: bool = True,
                             include_line_number: bool = True) -> str:
        """Handle search_code tool calls — delegate to BaseTools."""
        result = self.tools.search_code(
            paths=paths, patterns=patterns,
            context_lines=context_lines,
            case_sensitive=case_sensitive,
            max_matches=max_matches,
            include_filename=include_filename,
            include_line_number=include_line_number,
        )
        return result.content

    def _handle_count_occurrences(self, paths: list, patterns: list,
                                   case_sensitive: bool = False) -> str:
        """Handle count_occurrences tool calls — delegate to BaseTools."""
        result = self.tools.count_occurrences(
            paths=paths, patterns=patterns,
            case_sensitive=case_sensitive,
        )
        return result.content

    def _handle_syntax_check(self, paths: list) -> str:
        """Handle syntax_check tool calls — delegate to BaseTools."""
        result = self.tools.syntax_check(paths=paths)
        return result.content

    def _handle_verify_symbol_rename(self, old_symbols: list,
                                      new_symbols: list,
                                      paths: list,
                                      scope: str = "code_only",
                                      targets: list = None) -> str:
        """Handle verify_symbol_rename tool calls — delegate to BaseTools."""
        result = self.tools.verify_symbol_rename(
            old_symbols=old_symbols, new_symbols=new_symbols,
            paths=paths, scope=scope, targets=targets,
        )
        return result.content

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

    def _execute_tool(self, name: str, args: Dict[str, Any]) -> str:
        """Execute tool via pre-bound dispatch dict (s_full.py TOOL_HANDLERS pattern)."""
        handler = self.tool_dispatcher.get(name)
        if not handler:
            return f"错误: 未知工具 '{name}'"
        try:
            return handler(**args)
        except TypeError as e:
            logger.error(f"工具 '{name}' 参数错误: {e}")
            return f"错误: 工具 '{name}' 参数无效。详情: {str(e)}"
        except Exception as e:
            logger.error(f"执行工具 '{name}' 出错: {e}")
            return f"错误: {str(e)}"

    # ── Pre-LLM Processing (mirrors s_full.py agent_loop) ────────────

    def _is_tool_error(self, result_text: str) -> bool:
        """Detect whether a tool result indicates failure.

        Checks two patterns:
          - "错误:" prefix (from _execute_tool exception handling)
          - "[Exit Code: N]" with N != 0 (from bash tool return)
        """
        if not result_text:
            return False
        if result_text.startswith("错误:"):
            return True
        if result_text.startswith("[Exit Code: ") and "]" in result_text[:20]:
            try:
                code_start = len("[Exit Code: ")
                code_end = result_text.index("]")
                return int(result_text[code_start:code_end]) != 0
            except (ValueError, IndexError):
                pass
        return False

    def _microcompact(self) -> bool:
        """Lightweight compression: clear old tool result content (s_full.py s06).

        Returns:
            True if any tool result was actually cleared.
        """
        tool_result_indices = []
        for i, msg in enumerate(self.messages):
            if msg.role == 'tool':
                tool_result_indices.append(i)
        # Keep last 3 tool results, clear content of older ones
        if len(tool_result_indices) > 3:
            cleared = False
            for idx in tool_result_indices[:-3]:
                if len(self.messages[idx].content) > 100:
                    self.messages[idx].content = "[已清除]"
                    cleared = True
            return cleared
        return False

    def _check_auto_compress(self) -> bool:
        """Auto-compress if token threshold exceeded.

        Returns:
            True if compression (full or micro) was actually triggered.
        """
        if self.feature_manager.is_enabled('compression'):
            if self.compressor.should_compress(self.messages):
                logger.info("触发自动压缩")
                self.messages = self.compressor.compress(self.messages)
                return True
            elif self.compressor.should_microcompact(self.messages):
                logger.info("触发微压缩")
                self.messages = self.compressor.microcompact(self.messages)
                return True
        return False

    def _drain_background_notifications(self):
        """Drain background task notifications and inject into messages."""
        if not self.feature_manager.is_enabled('background'):
            return
        notifs = self.background.check_notifications()
        if notifs:
            lines = []
            for tid in notifs:
                task = self.background.get(tid)
                if task:
                    lines.append(f"[bg:{tid}] {task.status.value}: {task.result or task.error}")
            if lines:
                text = "\n".join(lines)
                self.messages.append(Message(
                    role='user',
                    content=f"<background-results>\n{text}\n</background-results>"
                ))

    def _check_inbox(self):
        """Check lead inbox for messages from teammates."""
        inbox = self.message_bus.read_inbox(self.config.agent.name)
        if inbox:
            import json as _json
            text = _json.dumps([{'from': m.sender, 'type': m.msg_type.value, 'content': m.content[:200]}
                               for m in inbox], indent=2, ensure_ascii=False)
            self.messages.append(Message(
                role='user',
                content=f"<inbox>{text}</inbox>"
            ))

    # ── Core LLM + Tool Cycle ──────────────────────────────────────

    def _llm_tool_cycle(self, max_iterations: int =50) -> str:
        """Core LLM + tool execution cycle, looping tools back to LLM (s_full.py s02 pattern).

        Args:
            max_iterations: Maximum number of LLM calls to prevent infinite loops.
        """
        provider = self.provider_manager.get_primary_provider()
        if not provider:
            return "错误: 没有可用的 LLM 提供者。请配置 API 密钥。"

        tools = self._get_llm_tools()
        tool_defs = [ProviderToolDef(**t) for t in tools]

        # ── Pre-loop: drain background notifications and inbox ──
        # (must run before the tool loop, not inside it, because Deepseek
        #  requires tool messages to immediately follow the tool_calls
        #  assistant message without intervening user messages)
        self._drain_background_notifications()
        self._check_inbox()

        # ── Reset benchmark metrics ──
        self.last_metrics = {"turns": 0, "total_tokens": 0, "api_errors": 0}

        # ── Nag tracking (s_full.py s03) ──
        rounds_without_todo = 0

        # ── Start task-level trace for this agent.chat() call ──
        tid = self.trace.start_task(
            user_prompt=self._current_user_prompt,
            workspace_root=str(self.runtime_context.workspace_root),
            workspace_confirmed=self._workspace_confirmed,
        )
        self.runtime_context.current_task_id = tid
        self.failure_memory.set_task(tid)
        self.loop_controller.clear()

        for iteration in range(max_iterations):
            try:
                # ── Start turn-level trace for this iteration ──
                self.trace.start_turn(iteration)
                # ── Pre-LLM: compression pipeline (s_full.py s06) ──
                # (safe inside the loop — only modifies existing messages)
                if self._microcompact():
                    self.trace.record_compression()
                if self._check_auto_compress():
                    self.trace.record_compression()

                # ── Safety net: normalize tool chains before API call ──
                # Ensures no orphaned tool messages or broken tool_calls
                self.messages = Compressor._clean_tool_chains(self.messages)

                # ── Log message structure for debugging ──
                role_counts = Counter(m.role for m in self.messages)
                logger.debug(f"[LLM#{iteration}] 发送 {len(self.messages)} 条消息: {dict(role_counts)}")

                # ── Update trace with current message count ──
                self.trace.set_message_count(len(self.messages))

                # ── LLM call with system prompt ──
                response = provider.create_message(
                    self.messages,
                    tool_defs,
                    system=self.system_prompt,
                    max_tokens=self.config.llm.max_tokens,
                    temperature=self.config.llm.temperature,
                )
                parsed = self._parse_response(provider, response)
                content = parsed.get('content', '')
                tool_calls = parsed.get('tool_calls', [])

                # ── Accumulate benchmark metrics ──
                usage = parsed.get('usage', {})
                self.last_metrics["total_tokens"] += usage.get('total_tokens', 0)
                self.last_metrics["turns"] = iteration + 1

                # ── Trace: token usage ──
                self.trace.record_tokens(usage.get('total_tokens', 0))

                if not tool_calls:
                    # Store final assistant response
                    self.messages.append(Message(role='assistant', content=content))
                    self.trace.end_task("SUCCESS")
                    return content

                # ── Store assistant message with tool calls ──
                self.messages.append(Message(
                    role='assistant',
                    content=content,
                    tool_calls=tool_calls,
                ))

                # ── Deduplicate: skip identical tool+args in the same response ──
                seen_tool_sigs: set = set()
                used_todo = False

                # ── Execute each tool and store results ──
                for tc in tool_calls:
                    fn = tc.get('function', {})
                    tname = fn.get('name', '')
                    args_raw = fn.get('arguments', '{}')
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw

                    # Duplicate detection within same LLM response
                    sig = (tname, args_raw)
                    if sig in seen_tool_sigs:
                        logger.warning(f"检测到重复工具调用【{tname}】，已自动跳过")
                        continue
                    seen_tool_sigs.add(sig)

                    if tname == "TodoWrite":
                        used_todo = True

                    # ── Trace + V3 Defense + Execute + Failure Intelligence ──
                    t_start = time.time()
                    args_hash = canonicalize_args(args)
                    v3_block_msg = None
                    failure_sig = None

                    try:
                        # ── V3 Layer 2+3: intent-based dedup → circuit breaker ──
                        v3_block_msg = self.loop_controller.check(tname, args)

                        if v3_block_msg:
                            result_text = v3_block_msg
                            # Compatibility: record in legacy guard as well
                            self.loop_guard.record(tname, args)
                        else:
                            result = self._execute_tool(tname, args)
                            result_text = str(result)

                            # ── Failure Intelligence Analysis ──
                            if self._is_tool_error(result_text):
                                failure_sig = self.failure_analyzer.analyze(
                                    tool_name=tname,
                                    tool_args=args,
                                    result_text=result_text,
                                )
                                self.failure_memory.record(
                                    category=failure_sig.category.value,
                                    strategy_fp=failure_sig.strategy_fingerprint,
                                )

                                # ── V3 Layer 3: circuit breaker registration ──
                                self.loop_controller.register_failure(
                                    tname, args, failure_sig.category.value,
                                )

                                cat = failure_sig.category.value
                                cat_count = self.failure_memory.get_category_count(cat)
                                stg_div = self.failure_memory.get_strategy_diversity(cat)
                                should_esc, esc_reason = self.failure_policy.should_escalate(
                                    failure_sig, cat_count, stg_div,
                                )
                                if should_esc:
                                    logger.warning(
                                        f"FailureEscalation: {cat} x{cat_count}, "
                                        f"strategies={stg_div}, reason={esc_reason}"
                                    )
                                    result_text = build_escalation_message(failure_sig, esc_reason)
                                    failure_sig.escalated = True

                            self.loop_guard.record(tname, args)

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
                        # Hard terminate the loop
                        self.trace.end_task("CIRCUIT_BROKEN")
                        return str(e)

                    t_end = time.time()

                    t_success = not v3_block_msg
                    self.trace.record_tool_call(
                        tool_name=tname, args_hash=args_hash,
                        success=t_success, loop_guard_blocked=bool(v3_block_msg),
                        error_message="" if t_success else result_text[:200],
                        result_preview=result_text[:200],
                        started_at=t_start, finished_at=t_end,
                        failure_category=failure_sig.category.value if failure_sig else "",
                        recoverability=failure_sig.recoverability.value if failure_sig else "",
                        strategy_fingerprint=failure_sig.strategy_fingerprint if failure_sig else "",
                        escalated=failure_sig.escalated if failure_sig else False,
                        circuit_breaker_triggered=False,
                        cwd=str(self.runtime_context.cwd),
                        workspace_root=str(self.runtime_context.workspace_root),
                        session_id=self.runtime_context.shell_session.session_id,
                    )

                    # Log with clean format
                    result_preview = result_text[:200].replace('\n', ' ').strip()
                    if tname == 'bash':
                        cmd = args.get('command', '')[:120].replace('\n', '\\n')
                        logger.info(f"[工具] {tname} | 命令: \"{cmd}\" | 结果: {result_preview}")
                    else:
                        logger.info(f"[工具] {tname} | 结果: {result_preview}")

                    # Store structured tool result (s_full.py style)
                    self.messages.append(Message(
                        role='tool',
                        content=result_text,
                        tool_call_id=tc.get('id', ''),
                    ))

                # ── Nag reminder (s_full.py s03) ──────────────
                rounds_without_todo = 0 if used_todo else rounds_without_todo + 1
                if self.todo.has_open_items() and rounds_without_todo >= 3:
                    self.messages.append(Message(
                        role='user',
                        content='<reminder>Update your todos.</reminder>',
                    ))

            except Exception as e:
                self.last_metrics["api_errors"] += 1
                logger.error(f"LLM 循环出错: {e}")
                self.trace.end_task("FAILED")
                return f"错误: {str(e)}"

        logger.warning(f"工具循环已达最大次数 ({max_iterations})，强制终止")
        self.trace.end_task("LOOP_ABORTED")
        return "错误: 工具执行次数过多，已自动终止。"

    def _parse_response(self, provider, response: Any) -> Dict[str, Any]:
        """Parse provider response safely."""
        if hasattr(provider, 'parse_response'):
            return provider.parse_response(response)
        return {'content': str(response), 'tool_calls': [], 'usage': {}}

    # ═══════════════════════════════════════════════════════════
    # Console Command Handlers
    # ═══════════════════════════════════════════════════════════

    def _cmd_status(self, args: List[str], ctx: Dict[str, Any]) -> str:
        lines = [f"=== {self.config.agent.name} v{self.config.agent.version} ==="]
        # Provider status
        for name, available in self.provider_manager.check_health().items():
            lines.append(f"  提供者 [{name}]: {'正常' if available else '失败'}")
        # Conversation stats
        lines.append(f"  消息数: {len(self.messages)}")
        lines.append(f"  估计 tokens: {self.compressor.estimate_tokens(self.messages)}")
        # Tasks
        task_counts = self.task_manager.count()
        lines.append(f"  任务数: {sum(task_counts.values())} 个")
        # Teammates
        team_stats = self.team_manager.get_stats()
        lines.append(f"  队友数: {team_stats['total']} 活跃")
        # Features
        enabled = self.feature_manager.get_enabled_features()
        lines.append(f"  已启用功能: {len(enabled)}/{len(self.feature_manager.list_features())}")
        return '\n'.join(lines)

    def _cmd_stats(self, args: List[str], ctx: Dict[str, Any]) -> str:
        lines = ["=== 统计信息 ==="]
        lines.append(f"对话: {len(self.messages)} 条消息")
        lines.append(f"  估计 tokens: {self.compressor.estimate_tokens(self.messages)}")
        task_counts = self.task_manager.count()
        lines.append(f"任务: {task_counts}")
        lines.append(f"队友: {self.team_manager.get_stats()}")
        lines.append(f"后台: {self.background.get_stats()}")
        lines.append(f"总线: {self.message_bus.get_stats()}")
        return '\n'.join(lines)

    def _cmd_config(self, args: List[str], ctx: Dict[str, Any]) -> str:
        lines = ["=== 配置信息 ==="]
        lines.append(f"  代理: {self.config.agent.name} ({self.config.agent.version})")
        lines.append(f"  LLM: {self.config.llm.provider} / {self.config.llm.model}")
        lines.append(f"  最大 tokens: {self.config.llm.max_tokens}")
        lines.append(f"  温度: {self.config.llm.temperature}")
        lines.append(f"  功能: subagent={self.config.features.subagent}, "
                      f"tasks={self.config.features.tasks}, "
                      f"compression={self.config.features.compression}, "
                      f"background={self.config.features.background}, "
                      f"team={self.config.features.team}, "
                      f"skills={self.config.features.skills}")
        return '\n'.join(lines)

    def _cmd_tasks(self, args: List[str], ctx: Dict[str, Any]) -> str:
        status_filter = TaskStatus(args[0]) if args else None
        tasks = self.task_manager.list(status=status_filter)
        if not tasks:
            return "没有找到任务。"
        # Sort by priority then creation
        tasks.sort(key=lambda t: (-t.priority, t.created_at))
        return '\n'.join(t.to_short_string() for t in tasks)

    def _cmd_team(self, args: List[str], ctx: Dict[str, Any]) -> str:
        status_filter = None
        if args:
            try:
                status_filter = TeammateStatus(args[0])
            except ValueError:
                return f"未知状态: {args[0]}。可用值: idle, working, busy, error, shutdown"
        teammates = self.team_manager.list(status=status_filter)
        if not teammates:
            return "没有队友。"
        return '\n'.join(t.to_short_string() for t in teammates)

    def _cmd_inbox(self, args: List[str], ctx: Dict[str, Any]) -> str:
        messages = self.message_bus.read_inbox(self.config.agent.name)
        if not messages:
            return "收件箱为空。"
        lines = []
        for m in messages:
            lines.append(f"[{m.sender}] ({m.msg_type.value}): {m.content[:200]}")
        return '\n'.join(lines)

    def _cmd_features(self, args: List[str], ctx: Dict[str, Any]) -> str:
        if not args:
            lines = ["=== 功能列表 ==="]
            for feat in self.feature_manager.list_features():
                status = '开' if self.feature_manager.is_enabled(feat.name) else '关'
                lines.append(f"  {feat.name:20s} [{status:3s}]  {feat.description}")
            return '\n'.join(lines)

        action = args[0]
        if action in ('enable', 'disable') and len(args) >= 2:
            name = args[1]
            if action == 'enable':
                ok = self.feature_manager.enable(name)
            else:
                ok = self.feature_manager.disable(name)
            if ok:
                return f"功能 '{name}' 已{action}。"
            return f"无法{action}功能 '{name}'。"
        return "用法: /features [enable|disable <名称>]"

    def _cmd_compact(self, args: List[str], ctx: Dict[str, Any]) -> str:
        if len(self.messages) < 4:
            return "消息数量不足以进行压缩（至少需要 4 条）。"
        old_count = len(self.messages)
        old_tokens = self.compressor.estimate_tokens(self.messages)
        self.messages = self.compressor.compress(self.messages)
        new_tokens = self.compressor.estimate_tokens(self.messages)
        return (
            f"已压缩: {old_count} -> {len(self.messages)} 条消息 "
            f"({old_tokens} -> {new_tokens} 估计 tokens)"
        )

    def _cmd_providers(self, args: List[str], ctx: Dict[str, Any]) -> str:
        lines = ["=== 提供者 ==="]
        info = self.provider_manager.get_provider_info()
        if not info:
            return "未配置任何提供者。"
        for name, p in info.items():
            status = '正常' if p['available'] else '失败'
            lines.append(f"  {name}: {p['type']} ({p['model']}) [{status}]")
        return '\n'.join(lines)

    def _cmd_add_workdir(self, args: List[str], ctx: Dict[str, Any]) -> str:
        """Add a directory to the path validation whitelist."""
        if not args:
            return "用法: /add_workdir <路径>\n添加工作目录到路径校验白名单"
        return self.tools.add_allowed_path(args[0])

    def _cmd_whitelist(self, args: List[str], ctx: Dict[str, Any]) -> str:
        """List all paths in the validation whitelist."""
        return self.tools.list_allowed_paths()

    def _cmd_skills(self, args: List[str], ctx: Dict[str, Any]) -> str:
        """List or load skill modules."""
        if not self.feature_manager.is_enabled('skills'):
            return "技能系统未启用。通过 /features enable skills 启用。"
        if not args:
            all_skills = self.skill_loader.get_all()
            if not all_skills:
                return "没有可用的技能模块。请在 skills/ 目录中创建 SKILL.md 文件。"
            lines = ["=== 技能模块 ==="]
            for s in sorted(all_skills, key=lambda x: x.name):
                desc = s.description or '(无描述)'
                cat = f"[{s.category}]" if s.category else ""
                lines.append(f"  {s.name:20s} {cat:12s} {desc}")
            return '\n'.join(lines)
        # Load specific skill
        name = args[0]
        content = self.skill_loader.get_skill_content(name)
        if content is None:
            available = ', '.join(s.name for s in self.skill_loader.get_all()) or '(无)'
            return f"未找到技能: {name}\n可用: {available}"
        return f"=== {name} ===\n{content}"

    # ═══════════════════════════════════════════════════════════
    # High-Level API
    # ═══════════════════════════════════════════════════════════

    def chat(self, message: str) -> str:
        """Simple chat interface (auto-detects console commands)."""
        # Check for console command
        if message.startswith('/'):
            result = self.console.execute(message, {'agent': self})
            return result

        return self.run(message)

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
        """Execute a command in the background. Returns task ID."""
        return self.background.run(command, description, timeout)

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

    def print_status(self):
        """Print current system status to stdout."""
        print(self._cmd_status([], {}))


def main():
    """Main entry point for the agent CLI."""
    import argparse

    parser = argparse.ArgumentParser(description='Mini Claude 代理')
    parser.add_argument('--config', type=str, help='配置文件路径')
    parser.add_argument('--one-shot', type=str, help='运行单次查询后退出')
    parser.add_argument('--demo', action='store_true', help='运行演示模式')
    args = parser.parse_args()

    config_path = Path(args.config) if args.config else None
    agent = MiniClaudeAgent(config_path=config_path)

    if args.one_shot:
        print(agent.chat(args.one_shot))
        agent.shutdown()
        return

    if args.demo:
        _run_demo(agent)
        agent.shutdown()
        return

    # Interactive mode
    print(f"\n=== {agent.config.agent.name} v{agent.config.agent.version} ===")
    print("输入 'exit' 或 '/exit' 退出，'/help' 查看命令\n")

    agent.print_status()

    while True:
        try:
            user_input = input("\n你: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ('exit', 'quit'):
                print("再见！")
                break

            response = agent.chat(user_input)
            if response:
                print(f"\n代理: {response}")

        except (KeyboardInterrupt, EOFError):
            print("\n\n再见！")
            break
        except SystemExit:
            break
        except Exception as e:
            print(f"\n错误: {e}")
            logger.exception("主循环出错")

    agent.shutdown()


def _run_demo(agent: MiniClaudeAgent):
    """Run a demonstration of agent capabilities."""
    print("\n=== Mini Claude 代理演示 ===\n")

    # Show status
    agent.print_status()

    # Demo conversation
    queries = [
        "你好，你启用了哪些功能？",
        "创建一个名为 hello_agent.txt 的文件，内容为 '来自 Mini Claude 代理的问候！'",
        "读取 hello_agent.txt 文件",
        "创建一个名为 '测试任务' 的任务，优先级为 2",
    ]

    for q in queries:
        print(f"\n你: {q}")
        print(f"代理: {agent.chat(q)}")

    print("\n=== 演示结束 ===")


if __name__ == "__main__":
    main()
