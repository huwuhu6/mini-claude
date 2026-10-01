# Provider 传输可靠性

## 2026-09-29：为 OpenAI-compatible Provider 启用流式请求

Commit: `PENDING`
Commit Description: `feat(provider): 启用流式响应并记录传输模式`

### Description

此前 OpenAI-compatible Provider 使用非流式请求，Runtime 必须等完整回答生成后才收到响应。慢速推理可能在中间代理或客户端读取超时前一直没有数据。候选分支 `refactor/loop-governance-simplification` 的 `2edcd0b` 加入了流式接收和完整响应组装；本次把这项传输改动择取到 `main`，没有带入该提交中的循环治理、工具输出和输出预算改动。

请求通过 `llm.stream` 配置控制，默认开启，也可设为 `false` 恢复非流式请求。Provider 在内部按顺序拼接正文、推理内容和按 index 分组的工具调用；只有收到完整结束标记且最终工具调用 ID 有效时，才把整份响应交给 Agent。流中途断开或结构不完整时，请求失败，部分工具调用不会进入执行链。流式请求会要求 Provider 返回 usage；没有 usage 时仍保留现有的零值/不可用处理，不自行补造实际 Token 数。

新增的 Trace 字段记录每轮请求是否启用了流式传输。该字段表示 Runtime 请求的传输模式，不表示 UI 正在逐 Token 展示；Agent 仍在完整响应组装后才处理正文和工具调用。

### Result / Evidence

候选分支在 Terminal-Bench `make-mips-interpreter` 上做过一次 Harbor smoke：流式解析和传输链路没有异常，但该次任务仍以输出上限结束且 verifier reward 为 0。单次试跑只能证明链路可以运行，不能证明任务成功率或超时率已经改善。本次合并不调整 `max_tokens`，也不自动重放流中断的请求。

本地确定性 Provider 测试中，流式组装与现有 Provider 诊断定向测试共 `19 passed`。覆盖真实 OpenAI SDK 对本地 SSE 的解析、正文/推理内容拼接、交错工具调用、usage、结束原因、非流式退出开关、配置贯通和 Trace 标记。依赖 pytest 临时目录的 Agent 端到端中断用例因当前环境拒绝枚举临时目录而未能运行；没有运行真实 Provider 或新的 Harbor trial。

### Decision / Limitation

流式传输允许客户端在模型仍生成回答时持续收到数据，能减少“等待整份响应期间没有数据”的情况；它不能保证首个分片及时到达，也不能避免网络断开或代理缓冲。当前实现把流完整缓冲后再交给 Agent，因此保留了 tool call 的原子执行边界，但不提供 UI 逐字输出。流中途失败不自动重试，以免把可能已计费的请求静默重放。

`stream_options.include_usage` 只在流式请求时发送。DeepSeek Chat Completions 文档规定该选项需与 `stream=true` 同用，并说明 usage 可位于最终内容 chunk；DashScope OpenAI-compatible 示例也使用相同选项。[DeepSeek Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)；[Alibaba Cloud Model Studio 流式输出说明](https://help.aliyun.com/en/model-studio/stream)。

## 2026-10-01：将 Provider bootstrap 从 Agent 中抽离

Commit: `741c442`
Commit Description: `refactor: extract provider bootstrap from agent runtime`

### Description

`MiniClaudeAgent` 原先同时创建 `ProviderManager`、按 Provider 读取环境变量、解析 API key 与 endpoint、组装配置并注册 primary provider。环境变量名称和默认 endpoint 属于 Provider 配置边界，将这些逻辑留在 Agent 会让 Runtime orchestration 依赖每个 Provider 的部署细节。

本阶段将现有解析与注册代码移到 `src/providers/bootstrap.py` 的 `configure_primary_provider()` 普通函数。Agent 仍创建并持有 `ProviderManager`，然后交由 bootstrap 配置 primary provider；ProviderManager 架构和现有环境变量优先级不变。配置加载器的环境变量替换与显式 Provider 环境覆盖仍然并存，本次不重新定义这两层配置语义。

### Result / Evidence

Provider bootstrap、诊断和流式定向测试共 `32 passed`。测试只使用本地对象、mock 传输与临时工作区，没有运行真实 Provider API。

### Decision / Limitation

保留轻量函数，不增加 Provider factory 或 resolver 抽象。环境变量解析现在集中在 `src/providers/`，但 ConfigManager 的 `${ENV_NAME}` 替换仍可能与显式环境覆盖重叠；该配置体系问题留待独立任务处理。
