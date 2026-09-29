
## 2026-09-29 当前现场

- 已将上下文与工具输出核心修复移入 `main`：完整请求预算估算、显式 1M/250K/500K 配置、Provider usage 与缓存命中 Trace、压缩事务和有界工具输出。
- Agent Note 继续每轮临时注入；自动压缩先提醒再执行。整合时修复了提醒状态与本轮动态上下文不同步的问题。
- Provider 输出达到上限时，Runtime 记录 `finish_reason` 和实际 usage，以 `PROVIDER_OUTPUT_LIMIT` 结束，不发布截断文本或执行截断工具调用。
- Harbor 适配器和 headless 入口已移入 `main`，可直接从当前 checkout 运行 Terminal-Bench 单任务。默认 Provider 请求超时为 60 秒，OpenAI-compatible 客户端允许一次重试并正确分类超时。
- OpenAI-compatible 主 Provider 现默认使用流式请求；响应仍在 Provider 内完整组装后再交给 Agent，Trace 记录每轮是否启用流式传输。此功能不是 UI 逐 Token 展示。`llm.stream: false` 可切回非流式。
- 2026-09-29 的 `make-mips-interpreter` 单次试跑使用整合前的 20 秒配置，第 10 次请求超时，Harbor reward 0.0；前 9 次未见输出上限截断或压缩。原始结果在 `sandbox/terminal_bench_runs/main-context-mips-20260929-1/`。新超时配置尚未进行付费 Provider 复跑。
- 250K/500K 和 8000 输出上限仍需更多 Trace 验证，压缩后尚无实际窗口余量硬性检查。
- 长期背景与证据见 `docs/evolution/context_management.md`。
- Provider 传输演进见 `docs/evolution/provider_transport.md`；当前流式请求尚未在 main 上做真实 Provider Benchmark。
- 循环治理已降低归一化意图导致的误拦截；不同 grep/sed 范围可以继续执行，只有状态振荡在给过重规划机会后仍会硬停止。治理 Trace 区分 observation 变化、实际 workspace/verification 进展，并记录工具耗时。
- Bash 工具结果现在记录可见性元数据；阈值为 200 行/4000 字符。Task Trace 记录请求配置，`--debug flow` 概括请求轮次、工具结果和输出截断比例，不展开输出内容。
- 本轮相关循环治理、flow、工具可见性、Trace 与流式 Provider 测试合计 101 passed，8 个依赖 pytest 临时目录的用例未运行；完整 `test_runtime_context` 集成测试受 Windows pytest 临时目录 ACL 阻挡，不能声称全套通过。无付费 Provider 运行。
- Evolution 记录已分别补到 `tool_deduplication.md`、`context_management.md` 和 `session_trace_evolution.md`；对应实现 Commit 尚待用户创建，标记为 PENDING。
