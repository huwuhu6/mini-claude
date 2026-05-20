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

## 2.1 项目文件树

```text
mini-claude/
├── pyproject.toml              # src-layout 打包配置，console_scripts = mini-claude
├── CLAUDE.md                   # Claude Code 项目指引（精简版）
├── eval_runner.py              # 基准评测运行器（10 个任务）
├── README.md                   # 项目 README
├── ARCHITECTURE_SNAPSHOT.md    # 旧架构快照（已过时，待清理）
├── INTERVIEW_CHEAT_SHEET.md    # 面试速查表
│
├── configs/
│   └── default.yaml            # 默认配置（LLM 选择、功能开关等）
│
├── docs/                       # 项目文档
│   ├── architecture.md         # ← 本文档
│   ├── decision_log.md         # 架构决策记录（ADR）
│   ├── roadmap.md              # 优先级路线图
│   ├── testing_plan.md         # 测试与评测策略
│   ├── benchmarks.md           # 评测对照账本
│   ├── evolution/
│   │   └── tool_deduplication.md  # 防重复调用演进史
│   ├── chat-logs/              # Claude Code 聊天记录（gitignored）
│   ├── pre-workflow-records/   # 早期模型对话摘要（gitignored）
│   └── legacy/                 # 归档文档（gitignored）
│
├── src/
│   ├── cli/                    # 【CLI 与 Workspace 层】
│   │   ├── entrypoint.py       #   mini-claude [path] [-y] 入口
│   │   ├── confirmation.py     #   工作区确认提示
│   │   └── authority.py        #   WorkspaceAuthority 权限模型
│   │
│   ├── agent/                  # 【Agent 编排层】
│   │   ├── mini_claude_agent.py  # 主 Agent 类（~1280 行）
│   │   └── minimal_agent.py    #   精简 Agent
│   │
│   ├── core/                   # 【核心系统】
│   │   ├── runtime_context/    #   Runtime 上下文
│   │   │   ├── workspace.py    #     RuntimeContext 聚合
│   │   │   ├── shell_session.py#     持久化 cwd 跟踪
│   │   │   ├── path_resolver.py#     路径解析
│   │   │   └── command_policy.py#    命令安全策略
│   │   ├── tools/
│   │   │   └── base_tools.py   #   文件读写编辑 + bash 工具
│   │   ├── tracing/            #   三层次追踪
│   │   │   ├── models.py       #     TaskTrace/TurnTrace/ToolTrace
│   │   │   ├── manager.py      #     TraceManager 编排
│   │   │   └── writer.py       #     JSON 持久化
│   │   ├── failure_intelligence/ # 故障智能分析
│   │   │   ├── models.py       #     FailureCategory、FailureSignature
│   │   │   ├── signatures.py   #     规则引擎（11 分类）
│   │   │   ├── analyzer.py     #     FailureAnalyzer 门面
│   │   │   ├── memory.py       #     按 task_id 隔离的失败记忆
│   │   │   └── policy.py       #     升级策略（3 条规则）
│   │   ├── evaluation/         #   追踪驱动评测
│   │   │   ├── metrics.py      #     6 个质量指标
│   │   │   └── analyzer.py     #     TraceAnalyzer
│   │   ├── loop_guard.py       #   死循环检测
│   │   ├── subagent.py         #   SubAgent 管理
│   │   ├── background.py       #   后台任务处理器
│   │   ├── compression.py      #   上下文压缩
│   │   ├── console.py          #   控制台命令系统
│   │   ├── teammate_manager.py #   队友管理
│   │   ├── messaging/
│   │   │   └── bus.py          #   消息总线
│   │   └── features/
│   │       └── manager.py      #   功能开关管理
│   │
│   ├── models/                 # 【数据模型】
│   │   ├── config.py           #   配置模型
│   │   ├── task.py             #   任务模型
│   │   ├── teammate.py         #   队友模型
│   │   └── todo.py             #   TODO 模型
│   │
│   ├── providers/              # 【LLM Provider 层】
│   │   ├── base.py             #   抽象基类
│   │   ├── manager.py          #   Provider 管理
│   │   ├── deepseek.py         #   DeepSeek 实现
│   │   └── anthropic.py        #   Anthropic 实现
│   │
│   └── skills/
│       └── loader.py           #   技能加载器
│
├── tests/
│   └── integration/            # 集成测试（68 个）
│       ├── test_all_modules.py
│       ├── test_failure_intelligence.py
│       └── test_runtime_context.py
│
├── skills/
│   └── pdf/                    # PDF 技能模块（实验性）
│
├── scripts/
│   └── test_llm.py             # LLM 测试脚本（孤立）
│
├── s_full.py                   # 原始单体实现（待清理）
├── sandbox/                    # 沙箱测试数据
├── logs/                       # 运行时日志
├── .team/                      # 队友状态文件（运行时）
├── .tasks/                     # 持久化任务文件（运行时）
└── .traces/                    # 追踪 JSON 输出（运行时）
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

---

## 附录: 审计快照（2026-05 归档）

> 以下内容来自模块化重构完成时的静态审计，已归档至本附录。详细原始数据见 `03-audit/` 档案（已移入 legacy）。

### 已知问题

| 功能/模块 | 问题 | 优先级 |
| --- | --- | --- |
| ShellSession.reset() | `_original_root` 未保存初始根目录，reset 后 cwd 不变 | P0 |
| eval_runner.py CSV 输出 | `logs/eval_results.csv` 追加未验证 `logs/` 存在 | P1 |
| src/core/__init__.py 导出 | evaluation 等子包未统一重新导出 | P2 |
| Rollback State Leak fault injection | mock.patch 较脆弱 | P1 |
| 跨平台路径 | BaseTools.safe_path() 在 POSIX 上行为可能不同 | P1 |
| CLI entrypoint 测试 | argparse、非 TTY 守卫、确认逻辑无 pytest 覆盖 | P0 |
| WorkspaceAuthority 测试 | 只有手动验证脚本，无 pytest | P0 |

### 未验证功能

CLI fresh install 可直接运行、WorkspaceAuthority 真正拦截外部路径、BaseTools + Authority 委托链完整、Trace 持久化完整记录生命周期、Failure Intelligence 在真实失败中被调用、Eval Runner 10 基准任务稳定运行、SubAgent 接入主循环等——均需添加端到端验证。

### 测试状态

68 个集成测试，分布在 3 个文件。运行：`py -m pytest tests/integration/ -v`

### 入口点

| 入口 | 命令 | 说明 |
| --- | --- | --- |
| CLI（推荐） | mini-claude [path] [-y] | cli.entrypoint:main |
| 传统 REPL | py s_full.py | 原始单体（待清理） |
| 模块直接运行 | py -m src.cli.entrypoint . --yes | 开发用 |
| 评测运行器 | py eval_runner.py | 10 基准任务 |
| 测试 | py -m pytest tests/integration/ | 68 测试 |

### 主要风险

1. **安全模型未验证**：WorkspaceAuthority、safe_path()、CLI 工作区确认缺少测试，削弱核心卖点可信度。
2. **入口路径过多**：CLI、s_full.py、模块直跑、评测入口并存，需明确唯一推荐入口。
3. **缺少演示闭环**：需构建从"用户任务"到"Agent 读文件→执行命令→失败分类→调整策略→写 trace→生成指标"的可演示链路。
