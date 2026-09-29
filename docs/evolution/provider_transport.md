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

## 2026-09-29：为默认推理强度下的输出截断增加一次恢复机会

Commit: `aa062a7`
Commit Description: `fix(runtime): 修复工具结果状态与 Provider 截断恢复`

### Description

后续同 Case 的 Memory ON 试跑在第 34 轮再次耗尽 16384 个 completion tokens，全部是 reasoning tokens。百炼当前文档说明 `deepseek-v4-flash-0731` 默认 `reasoning_effort=high`，支持 `low`，推理与可见输出共享模型输出上限。仅把 `finish_reason=length` 标为失败可以避免假成功，却没有给一次过度推理的请求修正机会。

本阶段只对 DashScope 的 DeepSeek V4 请求在没有显式配置 reasoning effort 时增加一次恢复调用：不把第一份被截断响应写入对话、不执行其中任何工具调用，以相同消息重试并指定 `reasoning_effort=low`，提示模型尽快给出完整下一步动作或简短终答。重试仍然被截断就按 `PROVIDER_OUTPUT_LIMIT` 失败。两次 Provider usage 分别累计，Trace 标记恢复次数和 effort。显式配置的推理强度以及其他 Provider/model 不会被自动改写。

### Result / Evidence

确定性回归覆盖一次低推理恢复后成功、再次截断后停止、截断工具调用从未执行、显式 reasoning effort 不被覆盖，以及两次 usage 均计入 Trace。完整 unit/integration：362 passed、2 deselected；两个 deselected 是已确认的 Evaluation oracle hash 旧失败。提交前再次运行 Provider/Context 相关测试为 32 passed，`git diff --check` 通过。

随后获授权对同一个 `make-mips-interpreter` 执行 1 次 Memory ON Harbor trial。Agent 在第 32 轮原请求与一次 `reasoning_effort=low` 重试后仍以 `finish_reason=length` 结束；该轮两次调用共消耗 32,768 completion/reasoning tokens，没有 assistant 可见内容或工具调用。Agent 因 `PROVIDER_OUTPUT_LIMIT` fail-closed；Harbor 无 harness exception，但 verifier reward 为 0，目标文件未创建。该运行确认当前低推理重试仍不足以处理持续耗尽输出预算的情况；它不是 Memory ON/OFF 对照，不能用于判断 Memory 收益。

### Decision / Limitation

保留一次有界重试与 fail-closed，避免无限重试、执行不完整工具参数或报告假成功；但本次真实 Trace 表明仅降低到 `low` 仍可能把整份额度花在 reasoning 上。后续应评估真正改变推理模式的 Provider 支持参数，而不是单纯增加 `max_tokens`；任何新恢复机制都必须保留截断拒绝执行，并用 deterministic tests 与获授权的单次 Provider 验证。
