# Roadmap

> 本路线图按 AI 应用开发岗位的面试价值排序，而不是按功能数量排序。目标不是继续堆功能，而是把当前项目中最能证明工程能力的部分做稳、测实、讲清楚。

## 1. 总体目标

最终希望 mini-claude 能展示一个清晰闭环：用户在本地 repo 启动 mini-claude，Agent 绑定 workspace，读取和理解代码，安全地修改文件，执行测试，遇到失败时进行失败分类和策略调整，生成结构化 trace，并通过 eval 指标复盘执行质量。

这条主线最符合 AI 应用开发岗位：LLM 应用编排、工具调用、本地 runtime 安全、状态管理、失败恢复、可观测性、测试和评测、面试可解释性。

## 2. P0：先修复可信度问题

### P0.1 修复并测试 `ShellSession.reset()`

面试价值：高  
类型：Bug fix + regression story

任务：修复 `ShellSession.reset()` 不回到初始 workspace root 的问题，保存 `original_root`，添加回归测试。

验收标准：`cd` 到子目录后 reset，cwd 回到 workspace root；reset 幂等；reset 后下一条命令在正确目录执行；测试通过。

为什么优先：ShellSession 是“Agent 具备 IDE-style persistent runtime”的核心。这个 bug 能形成很好的面试故事：审计发现状态泄漏风险，通过测试驱动修复。

### P0.2 补齐 `WorkspaceAuthority + BaseTools` 权限链测试

面试价值：极高  
类型：安全边界验证

任务：测试 primary root 内路径允许、additional roots、工作区外路径拒绝、`..` escape、绝对路径、symlink 策略；验证 read/write/edit/list 是否统一经过 authority；验证 SubAgent 是否继承 authority，如果未实现，标记 `Needs verification`，不要强行补大功能。

验收标准：新增 pytest 覆盖；越权访问返回结构化错误；文档更新权限边界。

为什么优先：安全是 Coding Agent 面试中的高频追问点。没有测试时不应把 WorkspaceAuthority 作为强卖点。

### P0.3 为 CLI entrypoint 添加 smoke tests

面试价值：高  
类型：产品入口稳定性

任务：覆盖 `mini-claude [path] [-y]`、无参数行为、相对路径、绝对路径、非 TTY 环境；验证 `pip install -e .` 后 console script 可用。

验收标准：CLI 可在 clean environment 中运行；docs 明确唯一推荐入口；旧入口标记 legacy/dev/eval。

为什么优先：面试展示应该从稳定入口开始，而不是直接运行内部 Python 文件。

## 3. P1：形成可展示闭环

### P1.1 构建端到端 demo repo

面试价值：极高  
类型：Demo

任务：创建一个小型 demo repo，让 Agent 完成读取项目结构、修改一个小 bug、运行测试、遇到一次真实失败、Failure Intelligence 分类失败、Agent 调整策略、测试通过、输出 trace、TraceAnalyzer 生成指标。

验收标准：有固定 demo 命令；有脱敏 trace 样例；有 demo README；能在 2-5 分钟内讲清楚。

为什么优先：功能列表没有 demo 说服力。这个闭环能把 runtime、tool、failure、trace、eval 串起来。

### P1.2 跑通并文档化 Evaluation

面试价值：极高  
类型：Eval

任务：修复 `logs/eval_results.csv` 目录问题；跑通 `eval_runner.py`；固化 6 个过程指标定义；生成一份 Markdown eval summary；说明 pass/fail 与过程指标的区别。

验收标准：首次运行无 `logs/` 目录也不失败；生成 CSV；有一份样例结果；`docs/testing_plan.md` 和 `docs/interview_notes.md` 中引用 eval 叙事。

为什么优先：AI 应用开发岗位很重视 evaluation。没有 eval 证据时，Agent 项目容易显得只是 demo。

### P1.3 验证 Failure Intelligence 在真实 agent loop 中生效

面试价值：极高  
类型：Reliability / Runtime intelligence

任务：构造真实失败场景，包括命令不存在、测试失败、缺依赖、权限拒绝、工具参数不支持。验证 FailureAnalyzer 被调用、FailureMemory 更新、strategy_fingerprint 有值、escalation policy 生效、trace 中有对应字段。

验收标准：不只依赖 mock；至少 3 类真实失败被正确分类；LLM 忽略 escalation 时有明确当前行为或硬终止计划；文档记录 trade-off：软提示 vs 硬终止。

为什么优先：Failure Intelligence 是项目最强差异化能力之一，但必须用真实场景证明。

### P1.4 固化 Trace schema 与脱敏样例

面试价值：高  
类型：Observability

任务：梳理 `TaskTrace`、`TurnTrace`、`ToolTrace` 字段；增加 schema version；放入一份脱敏 trace fixture；确认 TraceAnalyzer 能读取当前 schema；检查是否泄露用户代码或 secret。

验收标准：`docs/tracing.md` 或 architecture 中记录 trace schema；有 trace fixture；有 analyzer 测试；敏感信息策略明确。

为什么优先：Trace 是连接 failure 和 eval 的基础。没有 trace，项目很难证明 Agent 行为质量。

## 4. P2：提升真实 Coding Agent 能力

### P2.1 改进 `read_file` 和 `edit_file`

面试价值：中高  
类型：Tool runtime upgrade

任务：`read_file` 支持 offset/limit；`edit_file` 对 old_text mismatch 返回更可恢复的错误；增加 nearby context；评估 range edit；后续考虑 symbol navigation 或 semantic patch。

验收标准：长文件阅读不需要 python 脚本绕路；edit 失败后 Agent 能基于错误信息恢复；长文件任务轮数下降；测试覆盖大文件场景。

### P2.2 收敛入口和清理仓库

面试价值：中高  
类型：Project hygiene

任务：更新 `.gitignore`；清理 `build/`、`egg-info/`；`.team/`、`.tasks/` 标记运行时状态或转 fixtures；`s_full.py` 标记 legacy；`ARCHITECTURE_SNAPSHOT.md` 标记 obsolete 或迁移内容。

验收标准：git status 干净；文档中清楚说明入口；目录结构不误导面试官。

### P2.3 拆分 `MiniClaudeAgent`

面试价值：中  
类型：Architecture refinement

任务：逐步拆出 ToolDispatcher、AgentLoopController、PromptContextBuilder、ResultHandler、FailureHandlingMiddleware、TraceIntegration。原则是不一次性大重构，每次拆一个职责，每步有测试，不改变行为。

### P2.4 整理 Feature flags 和实验能力边界

面试价值：中  
类型：Productization

任务：标记 Stable / Experimental / Legacy。SubAgent、Teammate、MessageBus、PDF Skill 等能力不要混入主线。默认配置只开启稳定能力。

## 5. P3：可选增强

### P3.1 多 Agent 协作层产品化

只有当 SubAgent、Teammate、MessageBus 的真实边界被验证后，才继续投入。可能任务包括 Reviewer/Coder 分工 demo、消息确认机制、并发安全测试、Shadow Workspace 验证。

### P3.2 Skills 系统收敛

确认 `skills/pdf/` 是否仍有价值。若保留，标记为 experimental。更推荐新增 coding-oriented skill，例如 repo audit skill、test generation skill。

### P3.3 报告输出

将 trace/eval 输出为 Markdown 报告，生成面试展示用摘要。HTML 可选，不优先。

## 6. 不建议近期投入

近期不要优先做：增加更多 provider、复杂 UI、深化 PDF 技能、扩大多 Agent 并发系统、添加大量新工具、追求“Claude Code clone”式功能覆盖。

原因：当前项目的瓶颈不是功能少，而是需要把已有核心能力验证、收敛、讲清楚。
