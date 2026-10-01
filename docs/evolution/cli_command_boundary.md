# CLI Command 边界演进

## 2026-10-01

Commit: `677c925`
Commit Description: `refactor: 将 CLI 命令职责从 Agent Runtime 剥离`

### Description

此前 `MiniClaudeAgent` 在初始化时创建 Console 并注册 `/status`、`/features` 等命令，`chat()` 又根据输入是否以 `/` 开头决定执行本地命令还是运行模型。Agent Runtime 因而依赖 CLI 语法。Agent 文件底部还保留另一套交互入口和演示入口，与正式的 `cli.entrypoint` 重复。

本阶段将通用解析、注册、帮助、退出、历史和补全放到 `src/cli/console.py`，将依赖 Agent 状态的命令放到 `src/cli/commands.py`，由 `src/cli/entrypoint.py` 路由 slash command。普通输入继续交给 `MiniClaudeAgent.chat()`，该 API 不再解析 CLI 命令。CLI 通过命令执行 helper 保留 `user_input` 和 `command_result` 会话事件。

同时修复了三项命令行为：`/clear` 实际清空对话历史及依赖该历史的临时标记；未闭合引号等解析错误返回可读信息；`/tasks <status>` 对无效状态列出可用值。`/features` 成功切换后重新构建 system prompt；启用 skills 时先执行发现，使提示中的功能和技能描述与运行状态同步。

### Result / Evidence

新增独立 CLI 命令单元测试，覆盖命令解析与路由、Agent 命令注册、会话事件、`/clear`、无效任务状态以及 skills 启用/禁用后的 prompt 更新。CLI 命令与 system prompt 单测 `10 passed`；Agent Note 单测 `3 passed`；排除依赖系统临时目录的 Agent 用例后，Provider streaming 测试 `16 passed, 1 deselected`；旧集成脚本的通用 Console 用例 `1 passed`。

Agent 集成用例在创建 task 时因 Windows ACL 拒绝写入仓库外既有 runtime-data 目录而失败。带 `tmp_path` 的 Provider/Agent 用例也受 pytest 临时目录 ACL 阻挡，未作为本次回归失败计入。没有运行真实 Provider。

### Decision / Limitation

命令 handler 仍直接读取 Agent 已有的管理器、消息和压缩器对象，以保留现有命令行为并避免为此次边界调整新增服务框架。它们只由 CLI 创建和调用；Agent Runtime 不持有 Console 或命令 handler。
