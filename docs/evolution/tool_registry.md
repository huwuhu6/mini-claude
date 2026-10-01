## 2026-10-01：统一 MainAgent 与 SubAgent 的共享工具定义

Commit: `330e11d`
Commit Description: `refactor(tools): 统一主代理与子代理工具定义`

### Description

第一阶段让 MainAgent 用 `ToolRegistry` 将 schema 与 handler 绑定在一起，但共享基础工具的 schema 仍只写在 MainAgent；SubAgent 又单独维护了一份 schema 和 handler 字典。两份定义已经发生实际漂移：SubAgent 的 `read_file` schema 使用无效的 `limit`，handler 却传 `start_line` / `end_line`；SubAgent 的 `edit_file` schema 使用 `old_text` / `new_text`，handler 却传 `edits`。

本阶段新增轻量的共享 ToolSpec factory，集中定义 `bash`、`read_file`、`write_file`、`edit_file` 的 schema。MainAgent 和每个 SubAgent 实例仍各自创建 Registry，并自行选择工具、绑定 handler。MainAgent 的 15 个工具名称与顺序保持不变；只有四个基础工具共享定义。SubAgent 的 EXPLORE 仍只暴露 `read_file`；GENERAL、PLAN、REVIEW 继续暴露相同的四个基础工具，且所有 SubAgent 都没有 `task`。

TodoWrite 的 handler、TodoManager 以及 RuntimePolicy / Compression 对历史 TodoWrite 消息的兼容逻辑仍保留。当前 LLM registry 没有注册 TodoWrite（旧 schema 是注释代码），README 和演进文档中也没有明确要求将其启用，因此本阶段保持 dormant 状态，并删除 System Prompt 中让模型调用未暴露工具的说明。

### Result / Evidence

新增回归测试检查 MainAgent 工具名及 Provider 格式、四个共享 schema 的相等性、各 SubAgent 类型的工具集合、无 `task`、Registry 实例隔离、read/edit 实际 dispatch 参数、未知工具兼容行为、FeatureManager 过滤以及 System Prompt 不再提及 TodoWrite。

工具注册与提示词单测 16 项通过；SubAgent 集成测试 1 项通过；LoopController / TodoWrite 兼容测试 35 项通过。尝试运行完整 `test_all_modules.py` 时，若干与本次无关的用例因 Windows 临时目录 ACL 或外部 `mini-claude-project-data` 路径权限错误失败；针对 SubAgent 的集成用例独立通过。未运行真实 Provider。

### Decision / Limitation

共享层只包含当前确实由 MainAgent 与 SubAgent 共用的四个工具，不引入 BaseAgent、profile、能力框架或新的 Runtime 抽象。MainAgent 的工具顺序与 feature filtering 留在原有职责中。TodoWrite 仍未向模型暴露；若将来要启用，应单独确认产品意图并补齐对外契约测试。
