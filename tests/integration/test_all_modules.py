#!/usr/bin/env python3
"""
Integration tests for all Mini Claude Agent modules.
Tests all systems without requiring an API key (uses isolated tests).
"""
import sys
import os
import json
import tempfile
import time
from pathlib import Path

# Add src to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root / 'src'))

# Test counters
passed = 0
failed = 0


def _test_result(name: str, success: bool, detail: str = ""):
    global passed, failed
    status = "PASS" if success else "FAIL"
    if success:
        passed += 1
    else:
        failed += 1
    print(f"  [{status}] {name}" + (f" - {detail}" if detail else ""))


def _test_section(name: str):
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")


# ═══════════════════════════════════════════════════════════════
# Test: Config System
# ═══════════════════════════════════════════════════════════════
def test_config_system():
    _test_section("Configuration System")

    from models.config import ConfigManager, Config, LLMConfig

    # Test 1: Default config
    cm = ConfigManager()
    config = cm.get_config()
    _test_result("Default config loads", isinstance(config, Config))
    _test_result("Default provider", config.llm.provider == "deepseek")
    _test_result("Default model", config.llm.model == "deepseek-chat")
    _test_result("Features enabled", config.features.subagent and config.features.tasks)

    # Test 2: Config update
    orig_temp = config.llm.temperature
    config.llm.temperature = 0.5
    _test_result("Config update", config.llm.temperature == 0.5)
    config.llm.temperature = orig_temp  # restore

    # Test 3: LLMConfig creation
    llm = LLMConfig(provider="test", model="test-model", api_key="key")
    _test_result("LLMConfig creation", llm.provider == "test" and llm.model == "test-model")


# ═══════════════════════════════════════════════════════════════
# Test: Base Tools
# ═══════════════════════════════════════════════════════════════
def test_base_tools():
    _test_section("Base Tools")

    from core.tools.base_tools import BaseTools, ToolResult

    with tempfile.TemporaryDirectory() as tmpdir:
        tools = BaseTools(Path(tmpdir))

        # Test 1: Write file
        result = tools.write_file("test.txt", "Hello, World!")
        _test_result("write_file", result.success and ("Successfully" in result.content or "成功" in result.content))

        # Test 2: Read file
        result = tools.read_file("test.txt")
        _test_result("read_file", result.success and "Hello, World!" in result.content)

        # Test 3: Edit file (batch atomic edits)
        result = tools.edit_file("test.txt", [{"search": "Hello", "replace": "Hi"}])
        _test_result("edit_file", result.success)
        result = tools.read_file("test.txt")
        _test_result("edit_file content", "Hi, World!" in result.content)

        # Test 3b: Read file with line range window
        tools.write_file("multi_line.txt", "line1\nline2\nline3\nline4\nline5\n")
        result = tools.read_file("multi_line.txt", start_line=2, end_line=4)
        _test_result("read_file window", (
            result.success
            and "line2" in result.content
            and "line4" in result.content
            and "line1" not in result.content
            and "line5" not in result.content
        ))

        # Test 4: Safe path - within workspace
        result = tools.read_file("test.txt")
        _test_result("safe path (valid)", result.success)

        # Test 5: Safe path - escapes workspace (should fail gracefully)
        try:
            tools.safe_path("../etc/passwd")
            _test_result("safe path (escape rejected)", False)
        except ValueError:
            _test_result("safe path (escape rejected)", True)

        # Test 6: List files
        result = tools.list_files(".")
        _test_result("list_files", result.success and "test.txt" in result.content)

        # Test 7: Read non-existent file
        result = tools.read_file("nonexistent.txt")
        _test_result("read_file (missing)", not result.success)

        # Test 8: Write nested file
        result = tools.write_file("sub/dir/nested.txt", "nested content")
        _test_result("write_file (nested)", result.success)
        result = tools.read_file("sub/dir/nested.txt")
        _test_result("read_file (nested)", result.success)


# ═══════════════════════════════════════════════════════════════
# Test: Feature Management
# ═══════════════════════════════════════════════════════════════
def test_feature_management():
    _test_section("Feature Management")

    from core.features import FeatureManager, FeatureDefinition, FeatureDependency

    fm = FeatureManager()

    # Register features
    fm.register_feature(FeatureDefinition(name="tools", description="Base tools", enabled=True))
    fm.register_feature(FeatureDefinition(name="bash", description="Bash execution", enabled=True,
                                           dependencies=[FeatureDependency("tools")]))
    fm.register_feature(FeatureDefinition(name="danger", description="Dangerous ops", enabled=False))

    _test_result("feature registered", fm.get_feature("tools") is not None)
    _test_result("feature enabled", fm.is_enabled("bash") is True)
    _test_result("feature disabled", fm.is_enabled("danger") is False)

    # Tool filtering
    tools = [
        {'name': 'read_file', 'category': 'tools'},
        {'name': 'danger_op', 'category': 'danger'},
    ]
    fm.register_tool_for_feature('danger_op', 'danger')
    filtered = fm.filter_tools(tools)
    _test_result("tool filtering (danger excluded)", len(filtered) == 1)
    _test_result("tool filtering (read_file included)", filtered[0]['name'] == 'read_file')

    # Enable/Disable
    fm.enable("danger")
    _test_result("feature enable", fm.is_enabled("danger") is True)
    fm.disable("danger")
    _test_result("feature disable", fm.is_enabled("danger") is False)

    # List features
    all_feats = fm.list_features()
    _test_result("list features", len(all_feats) == 3)

    # Dependency graph
    graph = fm.get_dependency_graph()
    _test_result("dependency graph", "tools" in graph.get("bash", []))


# ═══════════════════════════════════════════════════════════════
# Test: Task Management
# ═══════════════════════════════════════════════════════════════
def test_task_management():
    _test_section("Task Management")

    from models.task import Task, TaskManager, TaskStatus

    with tempfile.TemporaryDirectory() as tmpdir:
        tm = TaskManager(Path(tmpdir))

        # Create tasks
        task1 = tm.create(Task(title="Task 1", priority=2))
        task2 = tm.create(Task(title="Task 2", priority=1, blocked_by=[task1.id]))
        _test_result("create task", task1.id is not None)
        _test_result("create blocked task", task2.status == TaskStatus.BLOCKED)

        # Get task
        retrieved = tm.get(task1.id)
        _test_result("get task", retrieved is not None and retrieved.title == "Task 1")

        # Complete task 1 -> task 2 should be unblocked
        tm.complete(task1.id)
        task2_refetched = tm.get(task2.id)
        _test_result("unblock on completion", task2_refetched.status == TaskStatus.PENDING)

        # Task count
        counts = tm.count()
        _test_result("task counts", counts.get('completed', 0) >= 1)

        # List by status
        pending = tm.list(status=TaskStatus.PENDING)
        _test_result("list pending", len(pending) >= 1)

        # Delete
        tm.delete(task1.id)
        _test_result("delete task", tm.get(task1.id) is None)


# ═══════════════════════════════════════════════════════════════
# Test: Message Bus
# ═══════════════════════════════════════════════════════════════
def test_message_bus():
    _test_section("Message Bus")

    from core.messaging import MessageBus, Message, MessagePriority

    with tempfile.TemporaryDirectory() as tmpdir:
        bus = MessageBus(storage_dir=Path(tmpdir))

        # Send direct message
        msg = Message(sender="alice", recipient="bob", content="Hello Bob!")
        msg_id = bus.send(msg)
        _test_result("send message", msg_id is not None)

        # Read inbox
        inbox = bus.read_inbox("bob")
        _test_result("read inbox", len(inbox) == 1 and inbox[0].content == "Hello Bob!")

        # Empty after reading
        inbox2 = bus.read_inbox("bob")
        _test_result("inbox empty after read", len(inbox2) == 0)

        # Has messages
        bus.send(Message(sender="alice", recipient="bob", content="Message 2"))
        _test_result("has_messages", bus.has_messages("bob") is True)

        # Broadcast
        bus.send(Message(sender="system", recipient="alice", content="System msg"))
        bus.send(Message(sender="system", recipient="bob", content="System msg"))
        _test_result("broadcast", bus.get_inbox_size("alice") > 0)


# ═══════════════════════════════════════════════════════════════
# Test: Teammate System
# ═══════════════════════════════════════════════════════════════
def test_teammate_system():
    _test_section("Teammate System")

    from core.teammate_manager import TeammateManager, TeammateConfig
    from models.teammate import Teammate, TeammateStatus, TeammateRole

    with tempfile.TemporaryDirectory() as tmpdir:
        config = TeammateConfig(directory=tmpdir, idle_timeout=5)
        tm = TeammateManager(config)

        # Create teammates
        t1 = tm.create("Alice", TeammateRole.WORKER, skills=["python", "coding"])
        t2 = tm.create("Bob", TeammateRole.RESEARCHER)
        _test_result("create teammate", t1.id is not None and t2.id is not None)

        # Get by name
        alice = tm.get_by_name("Alice")
        _test_result("get by name", alice is not None and alice.name == "Alice")

        # Status change
        tm.set_status(t1.id, TeammateStatus.WORKING, "task_001")
        t1_refresh = tm.get(t1.id)
        _test_result("status change working",
                     t1_refresh.status == TeammateStatus.WORKING)

        # Release
        tm.release_task(t1.id)
        t1_refresh = tm.get(t1.id)
        _test_result("release task", t1_refresh.status == TeammateStatus.IDLE)

        # List by status
        idle = tm.list(TeammateStatus.IDLE)
        _test_result("list idle", len(idle) >= 2)

        # Get stats
        stats = tm.get_stats()
        _test_result("teammate stats", stats['total'] == 2)


# ═══════════════════════════════════════════════════════════════
# Test: Background Processing
# ═══════════════════════════════════════════════════════════════
def test_background_processing():
    _test_section("Background Processing")

    from core.background import BackgroundProcessor, BackgroundTaskStatus

    bp = BackgroundProcessor(max_concurrent=2)
    bp.start()

    # Run a simple command
    task_id = bp.run("echo 'hello world'", "test echo", timeout=10)
    _test_result("background task created", task_id is not None)

    # Wait for completion
    time.sleep(2)
    task = bp.get(task_id)
    _test_result("background task completed",
                 task is not None and task.status == BackgroundTaskStatus.COMPLETED)
    if task:
        _test_result("background task result", "hello world" in task.result)

    # Check notifications
    notifications = bp.check_notifications()
    _test_result("notifications received", task_id in notifications)

    bp.stop()


# ═══════════════════════════════════════════════════════════════
# Test: SubAgent System
# ═══════════════════════════════════════════════════════════════
def test_subagent_system():
    _test_section("SubAgent System")

    from core.subagent import SubAgent, SubAgentManager, SubAgentType
    from providers.manager import ProviderManager

    # Test SubAgent creation
    agent = SubAgent(agent_type=SubAgentType.EXPLORE)
    _test_result("subagent creation", agent.agent_type == SubAgentType.EXPLORE)

    # Test tool filtering for explore type
    tools = agent.get_tools()
    _test_result("explore tools (read only)",
                 all(t['name'] in ('read_file',) for t in tools))

    # Test general tools
    general = SubAgent(agent_type=SubAgentType.GENERAL)
    general_tools = general.get_tools()
    _test_result("general tools (has write)", any(t['name'] == 'write_file' for t in general_tools))

    # Test SubAgentManager without provider
    pm = ProviderManager()
    sm = SubAgentManager(pm)
    _test_result("subagent manager creation", sm is not None)

    # Spawn subagent
    spawned = sm.spawn(SubAgentType.EXPLORE)
    _test_result("spawn subagent", spawned is not None)


# ═══════════════════════════════════════════════════════════════
# Test: Console Commands
# ═══════════════════════════════════════════════════════════════
def test_console_commands():
    _test_section("Console Commands")

    from core.console import ConsoleCommandSystem, Command

    cc = ConsoleCommandSystem()

    # Parse commands
    parsed = cc.parse("/help")
    _test_result("parse /help", parsed is not None and parsed[0] == "help")

    parsed = cc.parse("/not a command")
    _test_result("parse not command", parsed is not None)

    parsed = cc.parse("hello world")
    _test_result("parse non-command", parsed is None)

    # Execute help
    result = cc.execute("/help")
    _test_result("execute help", "可用命令" in result or "Available" in result)

    # Execute help for specific command
    result = cc.execute("/help exit")
    _test_result("execute help exit", "exit" in result or "Exit" in result or "quit" in result)

    # Find similar
    cc.register(Command("test-command", "A test command", lambda a, c: "ok"))
    similar = cc._find_similar("test-comman")
    _test_result("find similar", similar == "test-command")

    # History
    cc.execute("/help")
    history = cc.get_history()
    _test_result("command history", len(history) > 0)


# ═══════════════════════════════════════════════════════════════
# Test: Compression
# ═══════════════════════════════════════════════════════════════
def test_compression():
    _test_section("Compression")

    from core.compression import Compressor, CompressedTranscript
    from providers.base import Message

    comp = Compressor({'token_threshold': 1000, 'max_transcripts': 5})

    # Token estimation
    msgs = [Message(role='user', content='hello world')]
    tokens = comp.estimate_tokens(msgs)
    _test_result("token estimation", tokens > 0)

    # Should compress (small messages shouldn't trigger)
    _test_result("should not compress (small)", not comp.should_compress(msgs))

    # Microcompact
    many_msgs = [Message(role='user', content='test') for _ in range(20)]
    compacted = comp.microcompact(many_msgs)
    _test_result("microcompact keeps first msgs",
                 len(compacted) > 0 and compacted[0].content == 'test')

    # Compress with enough messages
    conversation = (
        [Message(role='system', content='system prompt')] +
        [Message(role='user', content=f'user message {i}') for i in range(15)]
    )
    compressed = comp.compress(conversation)
    _test_result("full compress reduces messages",
                 len(compressed) < len(conversation))

    # Transcript management
    transcripts = comp.get_transcripts()
    _test_result("transcript created", len(transcripts) >= 1)

    # Stats
    stats = comp.get_compression_stats()
    _test_result("compression stats", stats['token_threshold'] == 1000)


# ═══════════════════════════════════════════════════════════════
# Test: Provider Base
# ═══════════════════════════════════════════════════════════════
def test_provider_base():
    _test_section("Provider Base")

    from providers.base import LLMProvider, Message, ToolDefinition, ToolResult

    # Message validation
    msg = Message(role='user', content='hello')
    _test_result("message creation", msg.role == 'user' and msg.content == 'hello')

    # ToolDefinition
    td = ToolDefinition(name='test_tool', description='A test tool',
                         input_schema={'type': 'object', 'properties': {}})
    _test_result("tool definition", td.name == 'test_tool')

    # ToolResult
    tr = ToolResult(tool_use_id='123', content='done')
    _test_result("tool result", tr.tool_use_id == '123' and tr.content == 'done')

    # Validate message
    class TestProvider(LLMProvider):
        def create_message(self, messages, tools=None, system=None, **kwargs):
            return None
        def get_cost_estimate(self, messages, model=None):
            return 0.0
        def is_available(self):
            return False
        def parse_response(self, response):
            return {'content': '', 'tool_calls': [], 'usage': {}}

    provider = TestProvider({'model': 'test', 'api_key': 'test'})
    _test_result("validate message (valid)", provider.validate_message(msg))
    _test_result("validate message (invalid)",
                 not provider.validate_message(Message(role='unknown', content='')))


# ═══════════════════════════════════════════════════════════════
# Test: Provider Manager
# ═══════════════════════════════════════════════════════════════
def test_provider_manager():
    _test_section("Provider Manager")

    from providers.manager import ProviderManager
    from providers.base import LLMProvider

    pm = ProviderManager()

    # Create a mock provider
    class MockProvider(LLMProvider):
        def create_message(self, messages, tools=None, system=None, **kwargs):
            return None
        def get_cost_estimate(self, messages, model=None):
            return 0.001
        def is_available(self):
            return True
        def parse_response(self, response):
            return {'content': '', 'tool_calls': [], 'usage': {}}

    pm.register_provider("mock", MockProvider({'model': 'mock-model'}), is_primary=True)
    _test_result("register provider", pm.get_primary_provider() is not None)

    # Check health
    health = pm.check_health()
    _test_result("check health", "mock" in health and health["mock"] is True)

    # Provider info
    info = pm.get_provider_info()
    _test_result("provider info", "mock" in info)


# ═══════════════════════════════════════════════════════════════
# Test: MiniClaudeAgent (without API key)
# ═══════════════════════════════════════════════════════════════
def test_agent_integration():
    _test_section("MiniClaudeAgent Integration")

    from agent.mini_claude_agent import MiniClaudeAgent

    agent = MiniClaudeAgent()

    # Agent should init without error
    _test_result("agent initialization", agent is not None)

    # Config loaded
    _test_result("config loaded", agent.config is not None)

    # Feature manager
    features = agent.feature_manager.list_features()
    _test_result("features registered", len(features) > 0)

    # Task manager
    task = agent.create_task("Integration Test Task", "Test task creation")
    _test_result("create task via agent", task is not None and task.id is not None)

    # Teammate
    teammate = agent.spawn_teammate("TestBot", "worker", "You are a test bot.")
    _test_result("spawn teammate", teammate is not None)

    # Console commands
    status = agent._cmd_status([], {})
    _test_result("status command", "mini-claude" in status.lower())

    tasks_output = agent._cmd_tasks([], {})
    _test_result("tasks command", "Integration Test Task" in tasks_output)

    team_output = agent._cmd_team([], {})
    _test_result("team command", "TestBot" in team_output)

    features_output = agent._cmd_features([], {})
    _test_result("features command", "bash" in features_output)

    config_output = agent._cmd_config([], {})
    _test_result("config command", "mini-claude" in config_output)

    # Inbox
    inbox_output = agent._cmd_inbox([], {})
    _test_result("inbox command", "空" in inbox_output or "empty" in inbox_output.lower() or not inbox_output)

    agent.shutdown()
    _test_result("shutdown", True)


# ═══════════════════════════════════════════════════════════════
# Run All Tests
# ═══════════════════════════════════════════════════════════════
def main():
    global passed, failed

    print("=" * 60)
    print("  Mini Claude Agent - Integration Tests")
    print("=" * 60)

    tests = [
        test_config_system,
        test_base_tools,
        test_feature_management,
        test_task_management,
        test_message_bus,
        test_teammate_system,
        test_background_processing,
        test_subagent_system,
        test_console_commands,
        test_compression,
        test_provider_base,
        test_provider_manager,
        test_agent_integration,
    ]

    for test in tests:
        try:
            test()
        except Exception as e:
            import traceback
            _test_result(test.__name__, False, f"Exception: {e}")
            traceback.print_exc()

    # Summary
    total = passed + failed
    print(f"\n{'='*60}")
    print(f"  Results: {passed}/{total} passed, {failed} failed")
    print(f"{'='*60}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
