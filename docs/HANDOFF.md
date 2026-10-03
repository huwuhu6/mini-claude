
## 2026-10-03 当前现场

- 当前有独立实验分支 `experiment/pre-mutation-shadow-detector`，基线为 `9c79b6f`。实现提交 `5a2dde3` 增加首次 workspace mutation 前的只观测 detector 和历史 Trace replay；不修改 Prompt、工具暴露、RuntimeDecision 或终止行为。离线证据目前只支持继续 Shadow，不能据此引入执行承诺。实现与回放结果记录在 `docs/evolution/anti_loop_benchmark_audit.md`；文档回填已完成，实验分支待推送。

## 2026-10-01 当前现场

- System Prompt 第一阶段重构已进入 main：新增纯函数 `src/core/prompt_builder.py`，Agent 只通过 `refresh_system_prompt()` 刷新；CLI 改用公开方法。Prompt 收敛为 identity、environment、execution policy、skills 区块，回答语言策略已从 Preflight context 移到 identity；动态 hot context 和请求消息构造未变。代表性输入下旧/新 Prompt 为 5,788/2,174 字符，估算 1,231/438 tokens。详见 `docs/evolution/context_management.md`。
- CLI 命令边界重构已提交并推送：Console parser 与 Agent-bound 命令位于 `src/cli/`，REPL 入口负责 slash command dispatch，`MiniClaudeAgent.chat()` 不解析命令；移除了 Agent 底部重复的旧 main/demo 入口。同步修复 `/clear`、malformed command、`/tasks` 非法状态，以及 `/features` 状态切换后的 skills discovery / system prompt 更新。详见 `docs/evolution/cli_command_boundary.md`。

- Tool 层共享 schema 已收敛到 `src/core/tools/definitions.py`；SubAgent 使用独立 `ToolRegistry`，EXPLORE 只读，GENERAL/PLAN/REVIEW 保持四个基础工具且没有 `task`。TodoWrite 第一阶段已恢复到 MainAgent registry：只作为每次 `run()` 范围内的轻量进度状态，动态状态临时注入，不再每三轮重复催更。稳定 System Prompt 已加入复杂多步骤任务的 TodoWrite 使用策略；定向 Prompt、Todo runtime 与 Registry 测试共 32 项通过。之后对同一个 Terminal-Bench case 的唯一 trial 达到 Harbor 1800 秒上限，32 次请求开始、49 次工具调用，仍为 0 TodoWrite、0 workspace mutation，reward 0.0。该样本没有显示 Prompt 策略促使模型调用 TodoWrite；Token usage 未从超时 Trace 中取得。详见 `docs/evolution/tool_registry.md`。

- 已将上下文与工具输出核心修复移入 `main`：完整请求预算估算、显式 1M/250K/500K 配置、Provider usage 与缓存命中 Trace、压缩事务和有界工具输出。
- Agent Note 继续每轮临时注入；自动压缩先提醒再执行。整合时修复了提醒状态与本轮动态上下文不同步的问题。
- Provider 输出达到上限时，Runtime 记录 `finish_reason` 和实际 usage，以 `PROVIDER_OUTPUT_LIMIT` 结束，不发布截断文本或执行截断工具调用。
- Harbor 适配器和 headless 入口已移入 `main`，可直接从当前 checkout 运行 Terminal-Bench 单任务。默认 Provider 请求超时为 60 秒，OpenAI-compatible 客户端允许一次重试并正确分类超时。
- OpenAI-compatible 主 Provider 现默认使用流式请求；响应仍在 Provider 内完整组装后再交给 Agent，Trace 记录每轮是否启用流式传输。此功能不是 UI 逐 Token 展示。`llm.stream: false` 可切回非流式。
- 2026-09-29 的 `make-mips-interpreter` 单次试跑使用整合前的 20 秒配置，第 10 次请求超时，Harbor reward 0.0；前 9 次未见输出上限截断或压缩。原始结果在 `sandbox/terminal_bench_runs/main-context-mips-20260929-1/`。新超时配置尚未进行付费 Provider 复跑。
- `configs/default.yaml` 的 `llm.max_tokens` 已从 8000 调到 32768。同一 `make-mips-interpreter` case 单次重跑 reward 0：32 轮、50 次工具调用，末轮 `finish_reason=length` 且 completion/reasoning 均为 32768；全程 706051 prompt、83530 completion tokens，无压缩和文件写入。Verifier 3 项均失败，因 30 秒内未生成 `/tmp/frame.bmp`。Job 为 `benchmark/harbor/jobs/main-max-tokens-32768-mips-20260929/`。单次结果只说明增加上限未解决该任务，不代表成功率结论。压缩后尚无实际窗口余量硬性检查。
- 随后只修改了 `src/agent/mini_claude_agent.py` 的 System Prompt，加入通用的“缺失信息是否阻塞下一步实现”判断，并调整行为任务的 Runtime Verification 表述；其他 Runtime、模型和压缩配置未动。Prompt 策略测试及 Agent Note 相关测试 4 项通过，`git diff --check` 通过。同一 case 只复跑 1 次：37 轮、55 次工具调用、997825 prompt、176112 completion、169721 reasoning tokens，无压缩、无 mutation、无 `write_file` / `edit_file`，末轮 `finish_reason=length`，Runtime `PROVIDER_OUTPUT_LIMIT`，Harbor 0 exception / reward 0.0。前 36 轮约 136953 reasoning tokens 均在检查；没有实现或运行反馈。Verifier 3 项失败，均因 `/tmp/frame.bmp` 未生成。说明本次单次样本没有显示 Prompt-level guidance 改善 Analysis → Action 转换；不要据此推断总体成功率。Trace 在 `benchmark/harbor/jobs/main-implementation-strategy-mips-20260929/make-mips-interpreter__uKey5ci/agent/mini-claude/traces/task_13ff2b2c.json`，演进记录见 `docs/evolution/context_management.md`。
- 应用 `feat/structured-context-memory` 上的实现后，当前 main 默认开启结构化近期文件记忆：只合并记忆模块，不合并分支上其他改动；Agent Note、压缩提醒和流式请求保留。memory、Prompt 与 Agent Note 相关确定性测试 21 项通过。第一次 Harbor 启动在依赖安装时失败，Agent 未启动；之后仅有 1 个有效 trial。结果：34 轮、50 次工具调用、940572 prompt、175034 completion、163360 reasoning tokens，压缩 0，末轮输出上限，Harbor 0 exception / reward 0。9 次 read_file 成功；第 23/25/27 轮写入并运行了三个分析脚本，未生成目标 `vm.js`，也未运行 `node vm.js`。Verifier 3 项因 `/tmp/frame.bmp` 缺失失败。相较 memory-off Prompt-only 的单次样本少 3 轮和 5 次工具调用，但未完成目标，不能把差异归因于记忆。Trace 在 `benchmark/harbor/jobs/main-context-memory-on-mips-20260930b/make-mips-interpreter__SK7VwAN/agent/mini-claude/traces/task_1889d301.json`。详情见 `docs/evolution/context_management.md`。
- 长期背景与证据见 `docs/evolution/context_management.md`。
- Provider 传输演进见 `docs/evolution/provider_transport.md`；当前流式请求尚未在 main 上做真实 Provider Benchmark。
- 循环治理已降低归一化意图导致的误拦截；不同 grep/sed 范围可以继续执行，只有状态振荡在给过重规划机会后仍会硬停止。治理 Trace 区分 observation 变化、实际 workspace/verification 进展，并记录工具耗时。
- Bash 工具结果现在记录可见性元数据；阈值为 200 行/4000 字符。Task Trace 记录请求配置，`--debug flow` 概括请求轮次、工具结果和输出截断比例，不展开输出内容。
- 本轮相关循环治理、flow、工具可见性、Trace 与流式 Provider 测试合计 101 passed，8 个依赖 pytest 临时目录的用例未运行；完整 `test_runtime_context` 集成测试受 Windows pytest 临时目录 ACL 阻挡，不能声称全套通过。无付费 Provider 运行。
- Tool Observation Reliability 已提交为 `8c7af3e`：POSIX 单行 `;` 链仅在保守语法门槛下采集分段退出码；Runtime 用结构化 segment code 向模型展示部分失败。`search_code` 的路径权限、确定性顺序、exact-cap 判断、整块输出和跳过/截断可见性已加强；MainAgent 文件观察 handler 保留 ToolResult。Windows 定向回归 117 passed、10 skipped、7 deselected，另直接验证 Windows CMD 状态掩盖；Docker Python 3.12.15 Linux 回归 160 passed、1 skipped、1 deselected，POSIX shell 与 symlink 场景均通过。一个既有 Provider parse-error 测试 fixture 缺当前配置字段，单独 deselect；Windows Context audit 仍受 pytest Temp ACL 阻挡。详见 `docs/evolution/tool_registry.md`。
- Evolution 记录已分别补到 `tool_deduplication.md`、`context_management.md` 和 `session_trace_evolution.md`；对应实现 Commit 尚待用户创建，标记为 PENDING。
