# 会话记录演进：从混乱日志到可分析的交互轨迹

## 2026-10-28：外部超时后保留逐请求证据

Commit: `2124f03`
Commit Description: `fix: 实现模型输出截断的有界恢复与任务内降档`

### Description

跨轮降档后的首题实测达到 Harbor 的 1800 秒外部上限，归档只留下 Session，完整 Trace 和 headless 结果尚未写出。虽然能看到恢复启动，已经返回的每次请求参数及用量仍在内存中，无法完整核对降档是否沿用、哪些请求被截断及已报告的消耗。

本次复用 SessionRecorder，为主循环与压缩摘要增加 provider_request_started / provider_request_finished 两类事件，用 request_id 关联。每次调用开始、返回或抛异常时立即写入原有 JSONL 文件，不等任务结束；保留 Run、轮次、尝试序号、调用参数、结束原因与取得的用量。摘要通过一个可选观察回调接入同一记录方法，Compressor 不依赖日志文件或数据库；独立使用时保持原行为。恢复启动和停止事件另记录当时已用次数和已预留 Token。

DeepSeek 解析结果增加 reported_usage，单独保留服务实际提供的有效整数，原有归一化聚合指标不变。未报告、负数和布尔值不变成已知的零；部分字段缺失只保留已知部分。其他适配器的归一化值标为 normalized，不冒充完整原始报告。只记录参数白名单及错误类型，不保存提示、响应正文、私有思考或异常原文。交互日志的脱敏规则为新增的明确计数字段保留数值。

### Result / Evidence

在真实 Agent 子进程中使用假 Provider：第一次输出截断，第二次低档响应完成并经过工具执行，第三次调用阻塞时由父进程直接 kill。子进程没有最终 Trace，但两次完成记录及其 8125 个模拟报告 Token 仍可读取；第三次只有 started，配对结果将其保留为未确认完成、用量未知。这里验证的是正常文件写入后的进程终止，不是断电或磁盘损坏恢复。

记录专项还覆盖主请求异常、摘要截断与异常、缺失/零/部分用量、脱敏后计数保留，以及日志目录不可写时不改变恢复和预算。Windows Python 3.12.1 最终完整单测 951 项通过、13 项跳过、0 失败，含 13 项记录专项与 10 项恢复效果专项；CLI 输入和 LoopController 集成另有 37 项通过，Harbor 离线适配器 4 项单独通过。13 项跳过为 10 项 POSIX、2 项符号链接权限及缺少全局 Harbor 依赖而未收集的模块，后者四项已复用临时依赖补测。

初次完整回归在 CLI 阶段发现三个测试替身只有 record、没有新增 provider_request 接口；补齐替身协议，保留原执行分类与消息闭合断言。相关 124 项定向复测通过并纳入最终全量。沙箱内键盘模拟停顿的测试转到本机环境后通过，未因此修改生产控制逻辑。原始 XML 为 sandbox/output-limit-tests/evidence-complete.xml、evidence-integration-final.xml、evidence-harbor.xml；恢复对照及源码指纹见 recovery-proof-20261028/evidence.json。恢复机制的解释维护在 provider_transport.md 同日期的新阶段。

### Decision / Limitation

保留最终 Trace 的原格式，JSONL 作为及时的诊断证据，不成为执行恢复或返还预算的依据。不新增数据库、队列或恢复状态。只有 started 的调用不能计为已完成，已知用量的部分合计不能当成任务完整消耗；SDK 内部 HTTP 重试也不等于这些 Provider 调用次数。日志写入仍沿用原有尽力而为的文件行为，不改变工具审批、预算或请求重放规则。

本次旧真实试跑的完整用量无法补回；后续真实试跑可据新事件逐请求核验。具体记录字段与结果解释以 README 和 EVALUATION 为当前说明。

这份文档记录本轮围绕“会话记录应该如何保存和分析”所做的改造。重点不是简单把 `.log` 换成 `.jsonl`，而是明确一条 Agent 交互的结构：一次会话中可以有多次用户来回，每次来回又包含多轮模型调用、工具调用和最终回复。

## 一、背景：为什么要重新设计记录格式

最初使用普通日志文件记录 Agent 行为。实际观察后发现有几个问题：

1. 多次会话混在同一个文件里，需要依靠时间戳猜测哪些日志属于同一次交互。
2. 普通日志主要面向人阅读，无法稳定地按工具、失败类型或会话筛选。
3. 控制台输出和内部调试日志混在一起，既影响使用体验，也不方便定位 Agent 为什么失败。
4. 只有最终结果不够。优化 harness 时需要知道 Agent 先做了什么、调用了哪些工具、工具返回了什么、哪里触发了循环防护。
5. 如果未来支持异步或并发工具调用，只按照事件先后顺序配对工具请求和结果是不可靠的。

所以这次改造的目标是：

- 一个会话一个 JSONL 文件，支持多个会话并存；
- 每行是一个结构化事件，既能被人查看，也能被脚本分析；
- 明确“用户来回”和“模型轮次”的区别；
- 工具请求和工具结果使用唯一 ID 关联；
- 控制台负责简洁进度，JSONL 负责完整调试信息；
- 运行时文件放到项目外部，不污染 Agent 正在操作的 workspace。

## 二、最终的数据层级

```text
一个 session
├── round_id = 1：用户输入 -> Agent 完成回复
│   ├── step 1：user_input
│   ├── step 2：thinking，turn = 1
│   ├── step 3：assistant_note
│   ├── step 4：tool_call
│   ├── step 5：tool_result
│   ├── step 6：thinking，turn = 2
│   └── step 7：final
└── round_id = 2：下一次用户输入 -> Agent 完成回复
```

三种编号的含义不能混淆：

- `session_id`：整个 CLI 进程的一次会话。
- `round_id`：一次用户输入到 Agent 回复结束的完整来回。
- `turn`：某个 `round_id` 内第几次模型调用。
- `step`：某个 `round_id` 内所有事件的发生顺序，包括输入、工具和错误记录。

## 三、字段和事件的职责

每一行都有公共字段：

```json
{
  "time": "2026-08-03T20:51:52+0800",
  "timestamp": 1785751912.0,
  "session_id": "20260803_204635_8314a8",
  "round_id": 4,
  "step": 4,
  "type": "tool_call"
}
```

主要事件如下：

| 事件 | 作用 |
| --- | --- |
| `session_start` | 记录会话开始、工作区和运行时数据根目录。它发生在用户输入前，因此位置字段为 `null`。 |
| `user_input` | 保存用户本轮输入的完整内容，并开启一个新的 `round_id`。 |
| `thinking` | 标记 Agent 开始一次模型调用，`turn` 表示本轮内的模型调用次数。它不是模型隐藏思维内容。 |
| `assistant_note` | 保存模型准备调用工具时的行动说明，只记录带工具调用的中间状态。 |
| `tool_call` | 保存工具名、参数和 `call_id`。 |
| `tool_result` | 保存工具结果、成功状态、拦截状态和同一个 `call_id`。 |
| `final` | 保存本轮最终回复。`RESPONSE` 表示对话返回，不等于 Benchmark 成功。 |
| `log` | 保存 WARNING、ERROR、CRITICAL 等需要调试的信息。 |
| `runtime_error` | 保存 Agent 循环中的运行时异常。 |
| `command_result` | 保存 `/help`、`/status` 等本地命令的结果。 |

## 四、`assistant_note` 和 `final` 的重复问题

之前的逻辑会在每次模型返回文本时写入 `assistant_note`，即使这一轮已经没有工具调用、文本其实就是最终答案。随后 `chat()` 又会把同样内容写入 `final`，导致一份回复完整保存两次。

本轮的取舍是：

- 有工具调用的模型中间文本，记录为 `assistant_note`；
- 没有工具调用的最终文本，只记录为 `final`；
- 如果模型最终又重复了上一条 `assistant_note`，`final` 只写 `content_reused=true`，正文不再复制。

这样既能保留 Agent 的行动说明，又避免无意义地扩大会话文件。

## 五、工具调用 ID 的检查和补齐

模型返回的工具调用通常带有：

```json
{
  "id": "call_abc123",
  "function": {
    "name": "edit_file",
    "arguments": "{...}"
  }
}
```

Agent 会把这个 ID 保存在 assistant 消息的 `tool_calls[].id` 中，并把它写入工具结果消息的 `tool_call_id`。发送请求时：

- OpenAI/DeepSeek 格式使用 `tool_call_id`；
- Anthropic 格式转换为 `tool_use_id`。

会话 JSONL 的 `tool_call` 和 `tool_result` 也都写入 `call_id`。如果兼容 Provider 没返回 ID，Agent 会生成当前模型轮次内的本地 ID，避免出现没有 ID 的工具链。

这次没有引入复杂的并发调度，只提前补齐了未来并发所需要的最小关联字段。

## 六、运行时目录调整

会话文件、Trace、任务、队友状态等不再默认写到用户 workspace，而是写到：

```text
D:\02_study\code\mini-claude-project-data\<project-name>-<short-hash>\
├── sessions/
├── traces/
├── tasks/
├── team/
├── inbox/
└── transcripts/
```

这样 Agent 在工作区里搜索文件时不会把自己的 `.trace`、`.team` 等运行时目录当成项目内容。旧目录没有自动删除，因为其中可能有历史数据；新代码不会继续往旧目录写入。

## 七、当前验证结果和后续方向

本轮完成了：

- `round_id` 和 `step` 的统一生成；
- `assistant_note` / `final` 的重复控制；
- `tool_call` / `tool_result` 的 `call_id` 关联；
- Provider 发送链路中的 `tool_call_id` / `tool_use_id` 检查；
- 69 个核心集成测试通过。

仍未完成的方向：

- Provider 的真正 Token 流式输出；
- 并发工具执行本身；
- 旧运行时目录的一次性迁移或清理工具；
- 更方便的 JSONL 查询和统计工具。

下一步如果继续优化会话系统，建议先增加一个很小的 JSONL 查询命令，例如按 `round_id`、`call_id` 和失败状态过滤，而不是立即设计完整的日志平台。

## 2026-09-29：请求与工具执行的诊断摘要

Commit: `PENDING`
Commit Description: `feat(trace): 记录请求配置与工具输出可见性`

### Description

仅有工具结果正文无法判断 Agent 是否看到了完整输出，还是只拿到了截断预览。本阶段把输出是否截断、原始与可见字符数、保留的行范围和安全的相对日志路径附加到工具 Trace 与会话事件。Task Trace 同时记录 Provider、模型、`max_tokens`、温度和流式开关；工具执行时长写入治理事件。新增 `--debug flow` 视图概括模型请求、工具结果和截断情况，不展示工具输出或路径内容。

模型请求事件改名为 `model_request_started`，旧的 `thinking` 事件仍可读取。输出长度阈值从 40 行/2000 字符放宽到既有 `read_file` 硬上限 200 行/4000 字符，减少中等大小源码窗口被不必要文件化的问题。模型工具说明会告知长输出保存位置及分段读取方式。

### Result / Evidence

Trace 序列化、flow 摘要、输出可见性、循环治理和流式 Provider 相关确定性测试共 101 passed，8 个依赖 pytest 临时目录或 Agent 端到端临时 workspace 的用例未运行。Windows 沙箱限制了 pytest 临时目录的扫描与清理；涉及临时目录的完整工具集成测试未能在本轮全部通过，不能据此报告全量集成套件通过。没有运行 Provider Benchmark。

### Decision / Limitation

保存路径只作为诊断元数据记录，flow 视图不显示具体路径或输出正文。200 行/4000 字符是可见性阈值，不代表 Provider 的 Token 预算。真实任务中的重复读取或 Token 变化仍需后续 Trace 验证。
