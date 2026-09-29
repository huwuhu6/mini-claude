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

本阶段保留现有 0.7 micro-compaction 阈值和统计摘要策略，没有引入 Provider-aware Budget、Project Context、长期 Memory、Value-aware Retention 或新的 Summary 架构。实现 Commit Hash 已在后续 docs 提交中回填。

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

新增 deterministic regression tests 覆盖 Audit 001~009：空 middle 的 4/10/15/17 条消息连续压缩保持 no-op；Provider malformed response 在 assistant 写入前进入 Agent FAILED 路径；工具巨型单行、超大 `context_lines` 和超长文件均受字符/字节边界约束；read_file 使用固定大小分块读取并验证 UTF-8/CRLF 跨 chunk、EOF 和范围边界；初始化 retention、重复 Transcript ID 以及 save/unlink 失败均可观察。当前实现排除既有评测变体后的 unit/integration deterministic suite 为 `253 passed`。

Audit 009 不再在 Agent 主循环中建设第二套 validator，而是由 parser 在 durable message append 前拒绝 malformed response，并补充 sanitizer 的少量类型防御，因此标记为 `RESOLVED_BY_OTHER_FIX`。

此前全量 unit/integration 检查中的两个失败已在 clean detached `5ccecf8` baseline 独立复现，均为 `PRE_EXISTING / BASELINE FAILURE`：`test_node_behavioral_alternate_shape_passes_dynamic_hidden_verifier` 与 `test_java_behavioral_alternate_shape_passes_hidden_verifier`，原因分别是评测自有 `tests/run_tests.js` 和 `src/test/java/com/example/InvoiceTest.java` 被检测为 modified。未运行真实 Provider Benchmark 或 Evaluation。

### Decision / Limitation

本阶段保留 recent 15、0.7 micro-compaction 阈值和现有摘要策略。删除 Transcript 遇到文件系统拒绝时，索引保留该 durable record，因而 retention 只能保持可观察的一致性而不能绕过外部 I/O 故障。当前剩余 Context 问题主要进入 Architecture / Benchmark 阶段：Provider-aware Context Budget、Summary 12K tail-only limitation、Recent 15 retention strategy、Summary Trust Boundary / Prompt Injection、Value-aware Retention 和 Project Context Discovery。没有引入事务日志、Summary 新算法、长期 Memory、Anthropic compatibility、Multi-Agent、Team、Inbox/MessageBus 或 Background delivery 机制；Hot Context 的 CTX-006 继续作为 follow-up。
## 2026-09-11

Commit: `1986265`
Commit Description: `refactor(context): 建立完整请求预算与基线观测能力`

本项目的 Context Foundation Baseline 0 为 `9a437c2`；本阶段实现与本次 Evolution 回填完成后的最终 HEAD 定义为 Context Benchmark Baseline 0。由于 Git Commit 不能在自身内容中可靠记录自身 Hash，最终 Benchmark Baseline revision 以本次文档提交后的真实 HEAD 为准，并在交付报告中记录。

### Description

Foundation correctness 修复后，基线仍有四个会直接影响后续 Context Benchmark 的缺口。默认模型的 Context Window 为 1M tokens，但原来的 70K/100K 隐式压缩关系只使用了很小的输入比例；请求前估算也只覆盖 durable messages，不能反映 system prompt、tool definitions 和临时 hot context 的真实请求组成。Provider 已返回实际 usage，但 Runtime 只保留笼统的 token 总数，Context Cache 的命中量因此不可观察。与此同时，Summary Provider 在看到 middle history 之前还会先执行每条 2000 字符和整体 12K tail-only 截断。

本阶段将 Compression 配置改为显式的 `context_window_tokens=1000000`、`microcompact_token_threshold=250000` 和 `full_compression_token_threshold=500000`，并校验三者关系。请求前使用 `estimated_prompt_tokens` 统计实际待发送 messages（包括临时 hot context）、system prompt 与稳定序列化的 tool definitions；请求后保留 Provider-reported `actual_prompt_tokens` / usage。OpenAI-compatible usage parsing 增加 `cached_tokens`，Runtime/Trace 增加 uncached tokens、cache hit rate 以及 Main/Summary Provider usage 的区分，任务级命中率按累计 token 加权计算。

Summary baseline 不再静默保留最后 12K chars，也不再对每条 message 做默认 2000 字符截断；完整 middle 会进入 Summary Provider。若完整 Summary 输入超过配置的 Context Window，则 fail-closed 并保留原始历史，而不是猜测性地删除一段输入后继续报告成功。

### Result / Evidence

新增 deterministic tests 覆盖显式阈值边界与非法配置、完整 request estimate、Provider usage/cache 字段缺失与存在、cached 超界 clamp、按 token 加权的任务级 cache hit rate、Summary 完整 middle 输入、Summary output reserve、Summary usage 统计、旧配置迁移报错，以及 tiktoken encoding 初始化失败时的安全 fallback。完整 unit/integration deterministic suite 为 `277 passed, 2 deselected`；其中两个 deselected case 是已经在 clean `5ccecf8` baseline 独立确认的 pre-existing Evaluation oracle mutation tests。聚焦 Context/Trace/Provider 测试为 `65 passed`，直接受影响测试为 `57 passed`，`git diff --check` 为 PASS。未运行真实 Provider、Context Benchmark 或 Evaluation。

### Decision / Limitation

250K/500K 是基于当前 1M Context Window 的最低合理 baseline，不是 Benchmark 得出的最优阈值；整体 Summary 也是可解释的单次 baseline，不代表最终 Summary 策略。Provider-aware Context Budget、Summary 分块或多级算法、Recent 15 retention、Summary Trust Boundary / Prompt Injection、Value-aware Retention、Project Context Discovery、Long-term Memory 和其他 Context Strategy 留待后续 Architecture / Benchmark 阶段。本阶段不引入 Explicit Cache，也不修改 Multi-Agent、Team、Inbox/MessageBus 或 Background delivery 语义。

## 2026-09-27

Commit: `2edcd0b`
Commit Description: `fix(runtime): 收敛循环拦截并提升 Provider 与工具反馈可靠性`

### Description

一次 Harbor Coding Agent 轨迹中，模型反复缩小源码读取范围。调查发现，Bash 输出只要超过 40 行或 2000 字符就仅向模型返回首尾预览；即使 56 行、1656 字符的普通源码窗口也会隐藏中间内容。完整输出虽保存到 `.agent/logs`，模型在该次轨迹中没有读取保存的日志。工具执行成功与模型看见完整结果是不同事实，不能把随后的补读直接判为死循环。

本阶段复用已有的工具预览字符预算与 `read_file` 行数上限，允许范围内的 Bash 输出完整内联；真正超限的输出继续保存日志、返回有界预览。Bash 工具描述说明可按行读取日志，Trace 和 Session 增加结构化输出可见性信息，简明调试视图只显示截断比例，不回显输出或日志路径。

### Result / Evidence

确定性回归覆盖中等源码窗口完整返回、超限输出仍可分页读取、Agent 到 Trace/Session 的可见性记录和调试视图的信息隔离。非评测变体 unit/integration suite：283 passed、1 skipped；`git diff --check` 通过。按旧 Harbor 轨迹记录的原始行数/字符数静态重算，原先 11 次被截断的 Bash 输出中有 7 次会完整内联，余下 4 次仍超限。这只说明工具反馈改变，不能证明模型在重跑任务时会更早写文件或完成任务。

随后获授权对同一个 Harbor `terminal-bench/make-mips-interpreter` 只运行 1 次真实 trial。新轨迹为 24 轮、37 次工具调用、6 次 Bash 输出截断、4 次读取已保存日志、0 次写文件、0 次 Context Compression 和 0 次 LoopGuard 拦截；Provider usage 为 458099 prompt、30709 completion、488808 total tokens。Harbor 无异常，但 verifier reward 为 0。旧轨迹对应 29 轮、46 次工具调用、11 次 Bash 输出截断、0 次读取日志和 0 次写文件；两次 trial 不能作为统计显著的 A/B，也不能把轮数或 Token 差值归因于本次修改。新轨迹只能支持“结果可见性和日志续读改善”，尚不支持“Coding 任务完成率改善”。

### Decision / Limitation

保留输出绝对硬上限和超限落盘，不增加针对任务名、源码文件、重复读取次数或固定轮次的策略。单次真实 trial 不足以证明模型行为收益可稳定复现；Provider timeout、任务环境缺少 MIPS 反汇编能力与工具可见性问题须分别归因。

本次 trial 另外暴露一个独立的终止语义缺口：末轮没有工具调用也没有可见回复，completion usage 恰为配置的 `max_tokens=8000`，Runtime 仍把空回复标记为 `SUCCESS`，而 Harbor verifier 失败。当前 parser/Trace 不记录 Provider 的 `finish_reason`，因此不能确认是否因为输出预算耗尽；后续应独立处理空回复与 Provider 结束原因，不为了让这一任务通过而在本批工具输出修改里加入特例。

## 2026-09-27：Provider 不完整回复的终止语义

Commit: `2edcd0b`
Commit Description: `fix(runtime): 收敛循环拦截并提升 Provider 与工具反馈可靠性`

### Description

上述 Harbor trial 的最后一轮消耗了配置的全部 8000 completion tokens，却没有可见回复或工具调用。Agent Loop 仅凭“没有工具调用”进入成功分支，追加空 assistant 消息并将 Trace 标记为 `SUCCESS`；OpenAI-compatible parser 同时丢弃 `finish_reason`，使后来无法判断 Provider 是否因输出预算耗尽而提前停止。

本阶段保留 Provider 的结束原因到每轮 Trace；当结束原因为输出上限时，不提交可能不完整的回复或 tool call，也不把它当作任务成功。即使 Provider 没返回结束原因，只要既没有可见回复也没有工具调用，同样以明确原因失败关闭。实际发生的 Provider usage 仍计入任务累计。

### Result / Evidence

新增 Fake Provider 回归覆盖正常结束、缺失结束原因、空回复、输出上限下的空回复和部分回复，以及不执行可能不完整的 tool call。完整非评测变体 unit/integration suite：289 passed、1 skipped。没有为了验证本修复再次运行真实 Provider；原 trial 的原始 `finish_reason` 未被旧版本保存，因此“末轮确实由预算耗尽导致”仍是推断。

### Decision / Limitation

不自动重试同一请求，也不因为单个 Benchmark 任务而提高默认 `max_tokens` 或强迫模型在固定轮次写文件。继续保留 Runtime 成功终止与独立 verifier 任务成功之间的区别。该 trial 中的源码读取多为新范围或正常分页；模型在某次读取保存日志时抄错随机文件名，这属于工具引用可用性的后续问题，不应伪称为死循环或日志丢失。

## 2026-09-29：结构化文件记忆集成后的单 Case 试跑

Commit: `e8aa1ec`
Commit Description: `feat(context): 集成结构化文件记忆与当前 Agent Runtime`

### Description

将既有 `feat/structured-context-memory` 合入隔离分支，并与 Harbor 试跑所用的 Runtime 变更汇合。Memory 只保存最近文件、局部读取范围和有界观察，在每次请求前作为临时上下文注入；生产默认仍为关闭。本次仅在隔离工作树的 Harbor 配置中临时启用，然后恢复默认。合并后确定性 unit/integration 为 358 passed、2 deselected（两个已知 Evaluation oracle hash 旧失败）。

### Result / Evidence

获授权只运行一次 Terminal-Bench 2.0 `make-mips-interpreter`，Harbor 1 trial、0 exception、reward 0。Trace 确认 `memory=true`、`stream=true`、`max_tokens=16384`；34 次模型调用、51 次工具调用、9 次 `read_file` 调用（其中一次文件不存在，8 次进入范围统计）、0 次压缩、0 次工作区变更。请求累计 714074 prompt、110851 completion（其中 105548 reasoning）、824925 total tokens，649216 prompt tokens 命中缓存。末轮 `finish_reason=length`，16384 completion tokens 全用于 reasoning，Agent 以 `PROVIDER_OUTPUT_LIMIT` 结束；verifier 三项失败，未生成目标交付物。

前一次 Memory OFF 的同 Case 试跑为 43 轮、60 次工具调用、6 次 `read_file`、0 次压缩、0 次工作区变更，同样以 `PROVIDER_OUTPUT_LIMIT` 结束。两次不是相同 revision 上的多 trial 随机对照，不能把轮次或 token 差异归因于 Memory。本次读取主要是不同文件范围及日志分页，未观察到相同范围的重复成功读取；该 Case 仍没有形成记忆所需的压缩/遗忘压力。

### Decision / Limitation

本次只证明 Memory 与当前 Harbor Runtime 能共同运行，不能证明它减少重复探索或提高任务成功率。它没有解决模型长期只读、迟迟不创建 `vm.js` 和推理输出预算耗尽的问题；不因此默认打开 Memory，也不再为单个 Case 修改 Prompt、压缩器或治理规则。

另发现一个与 Memory 合并无关的 Trace/工具状态缺口：`read_file` 的文件不存在错误被 Handler 解包为 `Error: File not found` 字符串后，通用错误识别没有识别该前缀，ToolTrace 因而标成 `success=true`；范围指标没有把它计为成功读取。后续应按 ToolResult 的结构化成功状态修复，而不是通过这个 Case 的文件名特判。本次不扩张实现。

2026-09-29 后续修复（Commit: `aa062a7`）：`read_file` Handler 现在直接返回带有 `success/execution_success` 的 `ToolResult`，避免把文件不存在错误降成纯文本；字符串形式的 `Error:` 也进入失败识别。Fake Provider 回归确认 Trace 将缺失文件读取标为失败且不计入成功范围读取。该工具状态修复与上面的单次 Memory 试跑结果分开。
