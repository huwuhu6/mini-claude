
## 2026-09-29 当前现场

- 已将 `refactor/context-baseline-modernization` 的上下文与工具输出核心修复移入 `main`：完整请求预算估算、显式 1M/250K/500K 配置、Provider usage 与缓存命中 Trace、压缩事务和有界工具输出。Harbor 接入仍在独立分支。
- Agent Note 继续每轮临时注入；自动压缩先提醒再执行。整合时修复了提醒状态与本轮动态上下文不同步的问题。
- Provider 输出达到上限时，Runtime 记录 `finish_reason` 和实际 usage，以 `PROVIDER_OUTPUT_LIMIT` 结束，不发布截断文本或执行截断工具调用。
- 确定性测试覆盖上述路径；尚未运行真实付费 Provider Benchmark。250K/500K 和 8000 输出上限仍需用新 Trace 验证，压缩后尚无实际窗口余量硬性检查。
- 长期背景与证据见 `docs/evolution/context_management.md`。
