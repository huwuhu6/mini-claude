
## 2026-09-29 当前现场

- 已将上下文与工具输出核心修复移入 `main`：完整请求预算估算、显式 1M/250K/500K 配置、Provider usage 与缓存命中 Trace、压缩事务和有界工具输出。
- Agent Note 继续每轮临时注入；自动压缩先提醒再执行。整合时修复了提醒状态与本轮动态上下文不同步的问题。
- Provider 输出达到上限时，Runtime 记录 `finish_reason` 和实际 usage，以 `PROVIDER_OUTPUT_LIMIT` 结束，不发布截断文本或执行截断工具调用。
- Harbor 适配器和 headless 入口已移入 `main`，可直接从当前 checkout 运行 Terminal-Bench 单任务。默认 Provider 请求超时为 60 秒，OpenAI-compatible 客户端允许一次重试并正确分类超时。
- 2026-09-29 的 `make-mips-interpreter` 单次试跑使用整合前的 20 秒配置，第 10 次请求超时，Harbor reward 0.0；前 9 次未见输出上限截断或压缩。原始结果在 `sandbox/terminal_bench_runs/main-context-mips-20260929-1/`。新超时配置尚未进行付费 Provider 复跑。
- 250K/500K 和 8000 输出上限仍需更多 Trace 验证，压缩后尚无实际窗口余量硬性检查。
- 长期背景与证据见 `docs/evolution/context_management.md`。
