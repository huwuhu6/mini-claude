# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This project is a **modular refactoring and optimization** of [s_full.py](s_full.py) — a comprehensive AI agent that combines multiple mechanisms from sessions s01-s11. The original `s_full.py` is a monolithic reference implementation (~740 lines), and this project splits it into a maintainable, pluggable module system while preserving all core capabilities.

### Design Principles
- **Tool Dispatch Pattern**: Use a **dictionary-based routing** (`TOOL_HANDLERS` dict mapping name → handler function) as demonstrated in `s_full.py`'s `TOOL_HANDLERS` (line 577). **Never use hardcoded if/elif chains** for tool dispatch — this pattern is brittle, hard to extend, and violates Open/Closed principle.
- All capabilities from `s_full.py` must be preserved and evolved in the modular version.

## Language Rules

- **所有日志输出必须使用中文** - 包括但不限于：logger 日志信息、print 输出、控制台提示、用户交互信息、错误消息等。
- **代码注释**不受此限制，可以根据需要继续使用英文。
- **变量名、函数名、类名**应保持英文命名规范。

## Key Architecture Components

### Core Systems
1. **Main Agent Loop** - The primary conversational agent with tool dispatch
2. **Teammate System** - Persistent autonomous AI teammates with auto-claiming capabilities
3. **Task Management** - File-based task tracking with dependencies and blocking
4. **Message Bus** - Inter-agent communication system with broadcast support
5. **Background Processing** - Asynchronous command execution with notifications
6. **Skills System** - Loadable specialized knowledge modules
7. **Context Compression** - Automatic and manual conversation compression
8. **Shutdown Protocol** - Graceful teammate shutdown coordination

### Directory Structure
- `.team/` - Team configuration and member state
- `.tasks/` - Persistent task files (task_{id}.json)
- `.transcripts/` - Compressed conversation history
- `skills/` - Specialized skill modules (SKILL.md files)

## Essential Commands

### Python 版本说明
- **使用 `py` 命令代替 `python`**
- `py` 命令使用 Python 3.14.3（高版本，SSL 兼容性好）
- `python` 命令使用 Python 3.8.6（低版本，存在 SSL/TLS 连接问题）
- 所有运行、测试命令都应使用 `py` 而非 `python`

### Running the Agent
```bash
py s_full.py
```

### REPL Commands
- `/compact` - Manually compress conversation context
- `/tasks` - List all tasks
- `/team` - List all teammates and their status
- `/inbox` - Read messages from teammates

### Core Tool Usage
- `TodoWrite` - For short-term task tracking (max 20 items, 1 in_progress)
- `task_create` / `task_get` / `task_update` / `task_list` - Persistent file-based tasks
- `spawn_teammate` - Create autonomous AI teammates
- `send_message` / `broadcast` - Team communication
- `background_run` / `check_background` - Async command execution
- `load_skill` - Access specialized knowledge
- `task` - Spawn subagents for isolated work

### Environment Setup
Required environment variables:
- `MODEL_ID` - Claude model identifier (e.g., "claude-3-sonnet-20240229")
- `ANTHROPIC_BASE_URL` - Optional API endpoint
- `ANTHROPIC_AUTH_TOKEN` - API token (if no base URL)

### Important Patterns
1. **Task Dependencies** - Use `add_blocked_by` when creating dependent tasks
2. **Team Communication** - Messages flow through inbox files, not direct calls
3. **Auto-Claiming** - Idle teammates automatically claim pending tasks
4. **Token Management** - Automatic compression at 100K tokens, manual with `/compact`
5. **Background Tasks** - Check results via `check_background` or monitor notifications

### Tool Safety
- Dangerous commands (`rm -rf /`, `sudo`, etc.) are blocked
- File paths are workspace-relative with safety checks
- Timeout protection on all subprocess calls

### Key Files
- `s_full.py` - Main agent implementation (700+ lines)
- Skills in `skills/*.md` - YAML-frontmatter format with name/description
- Team config: `.team/config.json`
- Tasks: `.tasks/task_{id}.json`