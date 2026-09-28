# Provider 传输可靠性

## 2026-09-28

Commit: `2edcd0b`
Commit Description: `fix(runtime): 收敛循环拦截并提升 Provider 与工具反馈可靠性`

### Description

Terminal-Bench 的 `make-mips-interpreter` 单次运行在第 29 轮因 DashScope 读取超时结束。此前的非流式请求必须等待完整回答才返回；60 秒读超时加一次 SDK 重试后，该轮约 121 秒仍未收到可用回答。这个现象不能证明模型无法完成任务，也不能仅凭客户端异常确定是模型、服务端还是代理造成的等待。

本阶段让主 Agent 的 OpenAI-compatible Provider 默认使用流式请求。Provider 在内部拼合正文、思考内容和按 index 分组的工具调用，并等待完整的结束标记后，才把与原先相同格式的响应交给 Agent；不把分片直接送给工具执行。请求使用 `include_usage` 争取取得最终 Provider token 用量；如 Provider 不返回 usage，继续按“不可用”处理。`llm.stream: false` 可恢复原非流式路径，Trace 记录本次实际选择的模式。

第一次真实 smoke 在首轮暴露了组装器的兼容性错误：它要求同一工具调用的后续 ID 必须与第一片完全相同，导致 `tool_call id 不一致`，零次工具执行。百炼[官方 Function Calling 示例](https://help.aliyun.com/zh/model-studio/qwen-function-calling)按 `index` 拼接非空 ID 片段；后续参数片也可能携带空 ID。本阶段因此改为拼接非空 ID 片段、忽略空占位，并在完整响应形成后拒绝缺失或重复的最终 ID。没有为特定 Benchmark Case 增加特例。

### Result / Evidence

确定性测试覆盖本地模拟 SSE 经 OpenAI SDK 解码、交错工具调用、分段/空占位 ID、正文与思考内容、usage，以及中途连接中断时不执行不完整工具调用。Provider/Context 直接回归 `120 passed`，Agent/CLI/Context 扩展回归 `46 passed`，`git diff --check` 通过。

修复后只重跑一次 Terminal-Bench 2.0 `make-mips-interpreter`：Harbor 1 trial、0 exception；MiniClaude 实际完成 24 轮、41 次工具调用，未再发生流式解析或传输异常。Verifier reward 为 0。最后一轮 `finish_reason=length`，8000 个 completion tokens 全部是 reasoning tokens，Agent 按已有 fail-closed 合约以 `PROVIDER_OUTPUT_LIMIT` 结束。Trace 中 Provider-reported 用量为 470715 prompt、38092 completion、508807 total，其中 428032 prompt tokens 命中缓存；工作区无文件变更。

### Decision / Limitation

流式传输可以避免为了等待完整回答而持续没有响应数据，但不能保证首个分片一定在 60 秒内到达，也不能消除中间代理缓冲或网络中断。SDK 的自动重试主要发生在建立流之前；流已经开始后若中断，本实现丢弃半截回答并使当前任务失败，不自动重放可能产生费用的请求。修复后的单次 smoke 证明工具调用链路可用，却不能证明旧的第 29 轮超时已经从统计意义上消除：新轨迹在第 24 轮因另一类输出预算问题结束。Reasoning 消耗输出上限属于后续独立问题，不通过本轮调整 prompt、模型参数或 Benchmark 来掩盖。

## 2026-09-28：提高默认模型的单次输出余量

Commit: `2edcd0b`
Commit Description: `fix(runtime): 收敛循环拦截并提升 Provider 与工具反馈可靠性`

### Description

上一次同 Case Trace 的第 24 轮以 `length` 结束：8000 completion tokens 全用于 reasoning，没有可见回复或工具调用。第 14 轮也用到 8000 tokens，其中约 7901 为 reasoning，但刚好返回了工具调用。这说明现有 8000 单次输出预算对于当前默认推理模型偏紧，不等于模型本身已达到输出上限。[百炼官方模型页](https://help.aliyun.com/zh/model-studio/deepseek-v4-flash)将 `deepseek-v4-flash-0731` 最大输出列为 393216 tokens；本轮保守地把默认配置的 `llm.max_tokens` 提高到 16384，保持现有推理强度、任务轮次和 Benchmark Case 不变。

### Result / Evidence

输出预算、截断保护、流式 Provider、Trace 和 Context 定向回归 `66 passed`；Agent/CLI/Runtime 与 Provider diagnostics 集成回归 `86 passed`；`git diff --check` 通过。

随后获授权只运行一次 Terminal-Bench 2.0 `terminal-bench/make-mips-interpreter`，Harbor job 为 `output-budget-mips-20260928-1`。本次使用 `max_tokens=16384`，第 24 轮不再截断，但第 43 轮仍以 `length` 结束，16384 completion tokens 全为 reasoning，MiniClaude 为 `FAILED / PROVIDER_OUTPUT_LIMIT`。43 次模型调用、60 次工具调用（52 次 Bash、6 次 read_file、1 次后台启动、1 次后台状态），没有 write/edit，也没有产生 `/app/vm.js`；Context Compression 和 LoopGuard 拦截均为 0。Provider 报告累计 prompt 1077418、completion 133993（其中 reasoning 126043）、total 1211411、cached prompt 1012736 tokens。Harbor 1 trial、0 exception、reward 0，verifier 3 项失败；总耗时约 26 分 46 秒。单次试跑只能证明旧截断点被越过，不能证明 16384 是足够的最终预算或任务能力提升。

### Decision / Limitation

`finish_reason=length` 继续 fail-closed，不自动重放一次已返回且已计费的请求；部分正文和部分工具调用都不能当作有效执行计划。截断时只保留 Trace 诊断，不再把可能不完整的正文发布为 `assistant_note`。实跑说明增加输出预算只是把失败推迟到另一轮；更大的单次预算可能进一步增加长推理耗时与费用，但不能自动解决“反复准备写文件却没有交付”的任务行为。后续应将输出预算可靠性和任务执行能力分开研究，不为这一个 Case 继续盲目加大预算。
