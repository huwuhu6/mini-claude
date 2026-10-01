
## 2026-09-29 当前现场

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
- Evolution 记录已分别补到 `tool_deduplication.md`、`context_management.md` 和 `session_trace_evolution.md`；对应实现 Commit 尚待用户创建，标记为 PENDING。
