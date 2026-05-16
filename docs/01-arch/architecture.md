# Architecture

> 本文描述当前 mini-claude 项目的架构初版。当前 repo 审计结果优先；历史模型总结仅作为补充。未经当前代码和测试确认的历史内容不会进入正式架构主线，而是标记为 `Needs verification` 或放入 `Obsolete / Conflict`。

## 1. 系统定位

mini-claude 是一个本地运行的 AI Coding Agent Runtime。它的目标不是只调用 LLM 生成代码，而是在用户指定的工作目录中运行一个可控、可观察、可评测的 coding agent。

核心问题包括：Agent 在哪个 workspace 中工作；如何安全读写文件；如何执行 shell 命令；如何保持 cwd 等运行时状态；失败后如何避免无脑重试；做过什么如何被 trace；行为质量如何被 evaluation 衡量。

## 2. 当前推荐架构主线

```text
CLI / User
  -> Workspace Confirmation
  -> WorkspaceAuthority
  -> MiniClaudeAgent
      -> ProviderManager
      -> RuntimeContext
          -> ShellSession
          -> PathResolver
          -> CommandPolicy
      -> BaseTools
      -> TraceManager
      -> Failure Intelligence
      -> LoopGuard
      -> Evaluation
      -> Optional / Experimental Modules
          -> SubAgent
          -> TeammateManager
          -> MessageBus
          -> BackgroundProcessor
          -> SkillsLoader
```

## 3. 分层架构

### 3.1 CLI 与 Workspace 层

相关模块：`src/cli/entrypoint.py`、`src/cli/confirmation.py`、`src/cli/authority.py`。

职责：解析 `mini-claude [path] [-y]`；将用户传入路径解析为 workspace；在需要时进行 workspace confirmation；建立 workspace ownership boundary；创建或注入 `WorkspaceAuthority`。

当前状态：repo 审计显示 CLI 入口和 WorkspaceAuthority 已实现，但 CLI 自动化测试不足。clean environment 下 `pip install -e .` 后能否直接运行仍需验证。非 TTY 行为需要测试。

正式架构结论：CLI 是推荐产品入口。后续文档和面试展示应以 `mini-claude [path] [-y]` 为主，不再以 `s_full.py` 作为主入口。

### 3.2 Agent 编排层

相关模块：`src/agent/mini_claude_agent.py`、`src/agent/minimal_agent.py`。

职责：初始化子系统；驱动 LLM loop；构造上下文；调用 provider；分发工具；记录 trace；接入 failure intelligence 和 loop guard；输出任务结果。

当前状态：`MiniClaudeAgent` 是主 Agent 类，文件较大，约 1280 行，可能成为新的局部单体。`minimal_agent.py` 可能用于简化场景或测试，具体边界需要确认。

架构风险：Agent loop、tool dispatch、prompt/context、trace、failure handling 职责可能混在一个大文件中。后续应逐步拆分，但不应在近期做大规模重构，避免影响稳定性。

### 3.3 RuntimeContext 层

相关模块：`src/core/runtime_context/workspace.py`、`src/core/runtime_context/shell_session.py`、`src/core/runtime_context/path_resolver.py`、`src/core/runtime_context/command_policy.py`。

职责：维护 workspace root；维护 shell session 的逻辑 cwd；解析相对路径和绝对路径；执行命令安全策略。

核心设计：`RuntimeContext` 让 Agent 从“无状态函数调用者”演进为“IDE-style persistent runtime”。这对 Coding Agent 很重要，因为开发任务天然依赖当前目录、文件系统上下文和命令历史。

当前状态：repo 审计显示该层已实现；`ShellSession.reset()` 有已确认 bug；CommandPolicy 已实现，但 policy bypass 风险需测试；PathResolver 与所有工具的接入情况需验证。

### 3.4 Tool 层

相关模块：`src/core/tools/base_tools.py`。

职责：bash 执行、文件读取、文件写入、文件编辑、文件列表、工具调度。

当前状态：repo 审计显示工具调度与文件工具已实现，通过 `safe_path` 做路径验证，但是否完整委托到 WorkspaceAuthority 仍需验证。

工具层当前主要风险：文件访问越权测试不足；bash 是否严格受 workspace 限制需验证；`read_file` 可能缺少 offset/limit；`edit_file` 依赖 old_text 精确匹配，容易脆弱；工具 `success` 语义可能需要拆分。

### 3.5 Provider 层

相关模块：`src/providers/base.py`、`src/providers/manager.py`、`src/providers/deepseek.py`、`src/providers/anthropic.py`。

职责：抽象 LLM provider；支持 DeepSeek 和 Anthropic；通过配置和环境变量选择模型。

当前状态：repo 审计显示双 provider 已实现。测试是否覆盖 provider 切换仍需验证。建议后续引入 FakeProvider，用于 CI 和 agent loop 测试，避免依赖真实 API key。

### 3.6 Trace / Observability 层

相关模块：`src/core/tracing/models.py`、`src/core/tracing/manager.py`、`src/core/tracing/writer.py`。

设计：`TaskTrace -> TurnTrace -> ToolTrace`。

职责：记录任务级、轮次级、工具级执行过程；将 trace 持久化为 JSON；为 failure intelligence 和 evaluation 提供数据基础。

当前状态：repo 审计显示三层 trace 和 JSON 持久化已实现。历史总结提到 trace 中包含 cwd、workspace_root、session_id、failure_category 等字段；当前字段需以源码为准。trace schema 与 evaluation metrics 是否一致需验证。

后续要求：固化 schema；增加 schema version；增加脱敏策略；保留一份当前版本 trace fixture。

### 3.7 Failure Intelligence 层

相关模块：`src/core/failure_intelligence/models.py`、`src/core/failure_intelligence/signatures.py`、`src/core/failure_intelligence/analyzer.py`、`src/core/failure_intelligence/memory.py`、`src/core/failure_intelligence/policy.py`。

职责：分类失败；判断 recoverability；生成 strategy fingerprint；记录同一 task 下失败历史；判断是否 escalation。

当前状态：repo 审计显示该层已实现，有 28 个相关测试。历史总结中提到 retry storm 被 FI 缓解，但需要当前 repo trace 重新验证。分类过粗和 escalation 被 LLM 忽略是潜在风险。

正式架构结论：Failure Intelligence 是当前项目的核心亮点之一，但面试中应强调它是规则式、可解释、可测试的 runtime layer，不应夸大为通用智能错误诊断系统。

### 3.8 LoopGuard 层

相关模块：`src/core/loop_guard.py`。

职责：检测连续重复工具调用；检测高频重复调用；注入强制反思或阻断信息。

当前状态：repo 审计显示 LoopGuard 已实现。历史总结提到 args_hash、forced meta-reflection、合法重试被误杀等问题；具体当前状态需验证。

定位：LoopGuard 关注行为重复；Failure Intelligence 关注失败语义。两者互补。

### 3.9 Evaluation 层

相关模块：`src/core/evaluation/metrics.py`、`src/core/evaluation/analyzer.py`、`eval_runner.py`。

职责：从 trace 中计算过程质量指标；运行 benchmark task；输出评测结果。

当前状态：repo 审计显示有 6 个质量指标和 eval runner；`logs/eval_results.csv` 首次写入可能失败；指标定义和 trace schema 一致性需验证。

正式架构结论：Evaluation 是面试主线的一部分。项目应强调“Agent 不只看最终成功，还看过程质量”。

### 3.10 Experimental / Optional 层

相关模块：SubAgentManager、TeammateManager、MessageBus、BackgroundProcessor、SkillsLoader、`skills/pdf/`。

定位：这些模块当前不作为第一架构主线。原因是 repo 审计确认它们存在，但真实边界、端到端价值和测试充分性需验证。多 Agent、PDF skill 等容易分散主线。面试中应先讲 runtime、safety、failure、trace、eval。

## 4. 数据流

### 4.1 任务执行数据流

```text
User command
  -> CLI 解析 workspace
  -> Workspace confirmation
  -> WorkspaceAuthority 建立权限边界
  -> MiniClaudeAgent 创建任务
  -> TraceManager 创建 TaskTrace
  -> ProviderManager 调用 LLM
  -> LLM 返回文本或工具调用
  -> Tool dispatcher 执行 BaseTools
  -> RuntimeContext 提供 cwd/path/policy
  -> ToolResult 返回
  -> ToolTrace 写入
  -> FailureAnalyzer 处理失败
  -> LoopGuard 检查重复
  -> Agent 继续、升级或结束
  -> TraceWriter 持久化 JSON
  -> TraceAnalyzer 计算指标
```

### 4.2 文件访问数据流

```text
Tool path input
  -> PathResolver 解析路径
  -> WorkspaceAuthority 检查边界
  -> BaseTools.safe_path 验证
  -> 文件读写编辑
  -> ToolResult
  -> ToolTrace
```

注意：这条链路是期望架构。当前需要通过测试确认所有工具都实际走完整链路。

### 4.3 命令执行数据流

```text
bash tool input
  -> CommandPolicy 检查危险命令
  -> ShellSession 提供 cwd
  -> subprocess 执行或 cd 状态更新
  -> stdout/stderr/exit code
  -> ToolResult
  -> FailureAnalyzer
  -> ToolTrace
```

需要验证：`cd` 到 workspace 外是否被禁止；非零 exit code 是否被视为失败；timeout 是否有独立状态；policy block 是否进入 failure intelligence。

## 5. Obsolete / Conflict

以下内容来自历史模型总结，但当前 repo 审计没有确认，不能进入正式架构主线。

### 5.1 Shadow Workspace + 2PC

历史内容：SubAgent 的文件修改先进入 `.claude/shadow/<task_id>`，成功后提交到主工作区，失败删除 shadow workspace 并回滚 messages。

当前处理：标记为 `Needs verification / Conflict`。当前 repo 审计没有将其列为明确当前核心模块，不写入正式架构，除非源码审计确认。

### 5.2 MessageBus 强一致锁边界

历史内容：MessageBus 在内存字典和磁盘写入时使用同一把锁，牺牲并发换强一致。

当前处理：需要源码确认。历史中还提到 `_notify_subscribers` 在锁内可能死锁。暂不作为正式架构卖点。

### 5.3 Fault-Injection Eval Harness 的完整 Task A-J

历史内容：Gemini 总结提到 10 个 fault-injection tasks A-J。

当前处理：当前 repo 审计确认存在 `eval_runner.py` 和 10 个基准任务，但具体是否等同 Task A-J 需验证。不直接写成已完成事实。

### 5.4 Teammate 多线程执行

历史内容：早期可能有关于 Teammate 独立线程执行的推断。

当前处理：当前不宣称已实现独立多线程 Agent loop。Teammate 暂定位为 control/data plane，具体需验证。

## 6. 架构主线建议

面试中建议用四层讲：

```text
1. Workspace-bound Runtime
   CLI + WorkspaceAuthority + RuntimeContext + ShellSession

2. Tool-using Agent Loop
   MiniClaudeAgent + ProviderManager + BaseTools

3. Control and Recovery
   CommandPolicy + LoopGuard + Failure Intelligence

4. Observability and Evaluation
   TraceManager + TraceAnalyzer + eval_runner
```

这比罗列功能更有说服力，因为它直接回答 Coding Agent 的核心工程问题。
