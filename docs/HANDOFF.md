
## 2026-09-29 当前现场

- 已实现上下文压缩前的关键信息保留：会话级 Agent 笔记存放在工作区外的运行数据目录，通过 `update_agent_note` 工具更新，每轮临时注入。
- 自动微压缩和全量压缩在达到阈值时先提醒 Agent 整理笔记，下一次模型调用前再压缩；手动 `compact` 不走该提醒流程。
- 确定性测试覆盖提醒顺序和笔记保存。尚未运行真实 Provider Benchmark；需要进一步观察关键约束保留率、重复读取次数和总 Token。
- 对应长期记录见 `docs/evolution/context_management.md`，实现 Commit 为 `06b28ea`。
