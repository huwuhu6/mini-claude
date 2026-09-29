# 上下文管理演进记录

这份文档记录读取输出、对话压缩和动态上下文注入对 Agent 成本与稳定性的影响。

## 1. read_file 输出信息量

### 尝试

在 `b3f9e71` 中为 `read_file` 增加行号和上下界，目的是帮助 Agent 精确编辑并减少重新定位。

### 反效果

行号会增加每次读取的上下文长度。提交记录显示，该方案带来了 Token 和执行轮数上涨，尤其在需要多次读取的任务中成本明显。

### 最终取舍

在 `ee24a6b` 中移除默认行号输出，保留按需指定行范围的能力。默认返回更紧凑的代码内容，需要精确定位时再显式请求范围。

## 2. 对话压缩与 Stub 替换

### 问题

完全保留历史会持续增加 Token；简单截断又可能丢失任务目标、关键工具结果和失败上下文。

### 当前方案

在 `4d8cc16` 中引入基于 Token 阈值的分层压缩，并使用 Stub 替换部分已经完成或低价值的历史内容，尽量保留结构而不是保留全部原文。

## 当前结论

上下文管理的目标不是单纯压缩 Token，而是在成本、记忆完整性和重复读取之间取得平衡。任何压缩策略都必须同时观察任务成功率和重复工具调用。

## 2026-09-11

Commit: `402f474`
Commit Description: `fix(context): 修复上下文压缩与工具输出基础缺陷`

### Description

本阶段修复了 Context Management 的基础正确性问题：Token 估算补齐持久化的 `tool_calls`，微压缩不再凭工具名猜测成功，Full Compression 的 tail 边界不再切断 Tool Call / Tool Result Chain，`read_file` 增加统一的行数、字符数和字节数硬上限，并清理无效的 `microcompact_threshold` 配置。自动微压缩只有实际改变消息时才会被视为触发。

### Result / Evidence

新增 deterministic regression tests 覆盖上述不变量；Context Foundation 测试 15 passed，受影响的 unit/integration 测试共 190 passed。未运行真实 Provider Benchmark。

### Decision / Limitation

本阶段保留现有 0.7 micro-compaction 阈值和统计摘要策略，没有引入 Provider-aware Budget、Project Context、长期 Memory、Value-aware Retention 或新的 Summary 架构。

## 2026-09-11

Commit: `08fd7cf`
Commit Description: `fix(context): 修复压缩事务与跨进程 Transcript retention`

### Description

本阶段确认并修复两类可靠性错误。配置了 Summary Provider 时，Provider 异常、返回空内容或返回不可解析结果不再降级为统计摘要并覆盖原历史；Full Compression 只有在摘要有效且候选消息构造完成后才写入 Transcript。未配置 Provider 时仍保留原有 deterministic 统计摘要路径。

Transcript retention 现在会在 Compressor 初始化时加载目录中的有效 `transcript_*.json`，因此新进程创建 Transcript 时也会把上一进程留下的记录纳入 retention 排序和淘汰。损坏的 Transcript 文件被跳过，非 Transcript 文件不参与删除。

### Result / Evidence

新增 deterministic regression tests 覆盖 Provider 失败关闭、自动压缩不误报、统计摘要兼容、跨进程加载、最旧记录淘汰、损坏文件和无关文件保护；本轮相关测试通过。未运行真实 Provider Benchmark。

### Decision / Limitation

根据当前项目主线，本轮不修改 Anthropic 等非 OpenAI-compatible Provider 适配，不实现 Hot Context peek/deliver/ack，也不修改 MessageBus、Inbox、Team 或 Background 投递语义。CTX-006 留作 follow-up；Provider-aware Budget、Summary 架构和长期 Memory 同样不在本轮范围内。

## 2026-09-11

Commit: `a543b62`
Commit Description: `fix(context): 收敛上下文与工具链基础可靠性问题`

### Description

Final Audit 发现了三组会直接破坏 Context correctness 的问题。压缩在没有可压缩 middle segment 时仍会插入摘要并制造 Transcript，OpenAI-compatible Provider 的 malformed response 会被伪装成空 assistant 响应；工具输出还可能被巨型单行或过大的搜索上下文绕过边界，`read_file` 则会在返回小窗口前全量读入文件。Transcript 初始化、重复 ID 以及保存/删除 I/O 失败也可能让内存索引与磁盘事实静默分叉。

本阶段在现有机制上做最小修复：空 middle 直接 no-op；Deepseek parser 对缺少 choices、非法 message 和 malformed tool call 抛出明确错误，但合法 response 缺失 usage 时使用零值；Bash/search_code 使用共享的输出边界，`read_file` 改为有界分块扫描并只保留请求窗口。Transcript 在加载后立即执行 retention，重复 ID 保留最新有效记录并清理重复文件，持久化写入成功后才发布到内存索引，删除失败则保留记录并记录警告。

### Result / Evidence

新增 deterministic regression tests 覆盖 Audit 001~009：空 middle 的 4/10/15/17 条消息连续压缩保持 no-op；Provider malformed response 在 assistant 写入前进入 Agent FAILED 路径；工具巨型单行、超大 `context_lines` 和超长文件均受字符/字节边界约束；read_file 使用固定大小分块读取；初始化 retention、重复 Transcript ID 以及 save/unlink 失败均可观察。相关 Context 测试通过；未运行真实 Provider Benchmark 或 Evaluation。

Audit 009 不再在 Agent 主循环中建设第二套 validator，而是由 parser 在 durable message append 前拒绝 malformed response，并补充 sanitizer 的少量类型防御，因此标记为 `RESOLVED_BY_OTHER_FIX`。

### Decision / Limitation

本阶段保留 recent 15、0.7 micro-compaction 阈值和现有摘要策略。删除 Transcript 遇到文件系统拒绝时，索引保留该 durable record，因而 retention 只能保持可观察的一致性而不能绕过外部 I/O 故障。当前剩余 Context 问题主要进入 Architecture / Benchmark 阶段：Provider-aware Context Budget、Summary 12K tail-only limitation、Recent 15 retention strategy、Summary Trust Boundary / Prompt Injection、Value-aware Retention 和 Project Context Discovery。没有引入事务日志、Summary 新算法、长期 Memory、Anthropic compatibility、Multi-Agent、Team、Inbox/MessageBus 或 Background delivery 机制；Hot Context 的 CTX-006 继续作为 follow-up。
## 2026-09-11

Commit: `1986265`
Commit Description: `refactor(context): 建立 Context Baseline Modernization`

### Description

Foundation correctness 修复后，基线仍有四个会直接影响后续 Context Benchmark 的缺口。默认模型的 Context Window 为 1M tokens，但原来的 70K/100K 隐式压缩关系只使用了很小的输入比例；请求前估算也只覆盖 durable messages，不能反映 system prompt、tool definitions 和临时 hot context 的真实请求组成。Provider 已返回实际 usage，但 Runtime 只保留笼统的 token 总数，Context Cache 的命中量因此不可观察。与此同时，Summary Provider 在看到 middle history 之前还会先执行每条 2000 字符和整体 12K tail-only 截断。

本阶段将 Compression 配置改为显式的 `context_window_tokens=1000000`、`microcompact_token_threshold=250000` 和 `full_compression_token_threshold=500000`，并校验三者关系。请求前使用 `estimated_prompt_tokens` 统计实际待发送 messages（包括临时 hot context）、system prompt 与稳定序列化的 tool definitions；请求后保留 Provider-reported `actual_prompt_tokens` / usage。OpenAI-compatible usage parsing 增加 `cached_tokens`，Runtime/Trace 增加 uncached tokens、cache hit rate 以及 Main/Summary Provider usage 的区分，任务级命中率按累计 token 加权计算。

Summary baseline 不再静默保留最后 12K chars，也不再对每条 message 做默认 2000 字符截断；完整 middle 会进入 Summary Provider。若完整 Summary 输入超过配置的 Context Window，则 fail-closed 并保留原始历史，而不是猜测性地删除一段输入后继续报告成功。

### Result / Evidence

新增 deterministic tests 覆盖显式阈值边界与非法配置、完整 request estimate、Provider usage/cache 字段缺失与存在、按 token 加权的任务级 cache hit rate、Summary 完整 middle 输入、Summary usage 统计，以及 tiktoken encoding 初始化失败时的安全 fallback。当前已运行新增和既有 Context Foundation/Reliability 测试，以及 Trace/Provider 和 Compression smoke 测试；未运行真实 Provider Benchmark 或 Evaluation。

### Decision / Limitation

250K/500K 是基于当前 1M Context Window 的最低合理 baseline，不是 Benchmark 得出的最优阈值；整体 Summary 也是可解释的单次 baseline，不代表最终 Summary 策略。Provider-aware Context Budget、Summary 分块或多级算法、Recent 15 retention、Summary Trust Boundary / Prompt Injection、Value-aware Retention、Project Context Discovery、Long-term Memory 和其他 Context Strategy 留待后续 Architecture / Benchmark 阶段。本阶段不引入 Explicit Cache，也不修改 Multi-Agent、Team、Inbox/MessageBus 或 Background delivery 语义。

## 2026-09-29

Commit: `06b28ea`
Commit Description: `feat: 压缩前提醒 Agent 保存关键工作记忆`

### Description

旧方案在微压缩时会直接丢弃较早的长命令输出；全量压缩则可能把中间的精确约束概括掉。Agent 已读过这些内容，并不代表下一轮仍能看到它们。本阶段增加一个会话级笔记，保存在工作区外的运行数据目录。Agent 可以通过专用工具覆盖笔记；运行时在每次模型调用前把有限长度的笔记临时注入上下文。

达到自动压缩阈值时，运行时先保留原始消息，在下一次模型调用中提醒 Agent 检查并保存关键事实。再下一次模型调用前才执行压缩。这样即使单次工具输出跨过阈值，也有一轮整理机会。没有可替换内容时不发微压缩提醒，以免反复打断任务。

### Result / Evidence

确定性测试覆盖笔记隔离与大小限制、微压缩和全量压缩前的提醒顺序，以及笔记在存根替换后继续注入；本阶段运行的相关测试为 4 项通过。尚未运行真实 Provider Benchmark，因此不能据此断言任务成功率或总 Token 已改善。

### Decision / Limitation

先采用显式提醒加 Agent 主动维护笔记，避免每次压缩都增加一次独立的模型摘要调用。模型仍可能忽略提醒或记录错误事实；笔记是工作线索，涉及文件现状时仍需重新验证。手动 `compact` 命令仍由用户直接触发，不经过这个自动压缩提醒流程。后续应比较关键约束保留率、重复读取次数和总 Token，再决定是否需要强制的压缩前检查。

## 2026-09-29

Commit: `PENDING`
Commit Description: `fix(context): 合并请求预算观测并阻止截断响应误报完成`

### Description

当前 `main` 已有压缩前笔记提醒，却仍以持久消息正文估算请求大小；另一分支已经修复了工具输出和压缩事务的基础问题，并能统计实际待发送的 system、工具定义、tool call 参数与临时上下文。本阶段将这些修复移回主线，保留压缩前提醒，同时把默认配置显式设为 1M Context Window、250K 微压缩和 500K 全量压缩基线。Provider 返回的输入、输出及缓存 Token 与本地估算分开写入 Trace。

整合时发现：完整请求估算需要先生成动态上下文，但提醒状态会在估算后的压缩判断中才改变。因此同一轮发送前同步更新提醒片段，避免提醒晚一轮出现；通知只读取一次。Provider 的 `finish_reason=length` 等输出截断现在直接使任务以 `PROVIDER_OUTPUT_LIMIT` 失败，丢弃可能不完整的文本和工具调用，并记录该轮真实 usage、reasoning Token 与结束原因。

### Result / Evidence

本轮相关上下文、Agent Note、Provider 与集成测试通过；全部单元测试也通过。没有运行真实付费 Provider。此前在其他分支的 Harbor Trace 中，存在 `completion_tokens` 正好达到 8000 且 `finish_reason=length` 的样本，因此截断路径有现实依据；但本轮没有测量新的成功率或压缩成本。

### Decision / Limitation

沿用静态 250K/500K 基线，并保留 `max_tokens=8000`，直到真实 Trace 和 Benchmark 能支持调整。请求前估算仍使用近似 tokenizer；Provider usage 用于观测，不直接驱动压缩。完整请求估算在压缩后会重新计算，但目前没有针对实际 Context Window 余量的硬性 invariant。Harbor 适配器与独立 Provider 重试策略留在各自分支，本阶段不随上下文核心修复一起并入。
