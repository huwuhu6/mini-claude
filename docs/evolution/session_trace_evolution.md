# 会话记录演进：从混乱日志到可分析的交互轨迹

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
