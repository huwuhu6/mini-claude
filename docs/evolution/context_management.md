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

Commit: `c682808`
Commit Description: `fix(context): 合并请求预算观测并阻止截断响应误报完成`

### Description

当前 `main` 已有压缩前笔记提醒，却仍以持久消息正文估算请求大小；另一分支已经修复了工具输出和压缩事务的基础问题，并能统计实际待发送的 system、工具定义、tool call 参数与临时上下文。本阶段将这些修复移回主线，保留压缩前提醒，同时把默认配置显式设为 1M Context Window、250K 微压缩和 500K 全量压缩基线。Provider 返回的输入、输出及缓存 Token 与本地估算分开写入 Trace。

整合时发现：完整请求估算需要先生成动态上下文，但提醒状态会在估算后的压缩判断中才改变。因此同一轮发送前同步更新提醒片段，避免提醒晚一轮出现；通知只读取一次。Provider 的 `finish_reason=length` 等输出截断现在直接使任务以 `PROVIDER_OUTPUT_LIMIT` 失败，丢弃可能不完整的文本和工具调用，并记录该轮真实 usage、reasoning Token 与结束原因。

### Result / Evidence

本轮相关上下文、Agent Note、Provider 与集成测试通过；全部单元测试也通过。没有运行真实付费 Provider。此前在其他分支的 Harbor Trace 中，存在 `completion_tokens` 正好达到 8000 且 `finish_reason=length` 的样本，因此截断路径有现实依据；但本轮没有测量新的成功率或压缩成本。

### Decision / Limitation

沿用静态 250K/500K 基线，并保留 `max_tokens=8000`，直到真实 Trace 和 Benchmark 能支持调整。请求前估算仍使用近似 tokenizer；Provider usage 用于观测，不直接驱动压缩。完整请求估算在压缩后会重新计算，但目前没有针对实际 Context Window 余量的硬性 invariant。Harbor 适配器与独立 Provider 重试策略留在各自分支，本阶段不随上下文核心修复一起并入。

### 工具输出可见性补充（2026-09-29）

主线现将 bash 输出的截断状态、原始/可见字符数、展示行范围和相对日志路径写入 Trace。只有超过 200 行或 4000 字符的结果才自动文件化，普通的中等源码窗口会完整返回。新增 flow 调试视图可以显示预览比例，但不显示日志路径和正文。该变化只解决“模型实际看到了多少工具输出”的可观测性与过早文件化；真实 Token 节省和读取行为仍需要运行 Trace 验证。

## 2026-09-29：将单次输出上限提高到 32768 的 Terminal-Bench 试跑

Commit: `dd7c200`
Commit Description: `feat(agent): 调整实现策略并提高输出预算`

### Description

先前 `make-mips-interpreter` 的单次 Harbor trial 在第 27 轮以 `finish_reason=length` 结束，最后一轮正好消耗 8000 completion tokens。为判断扩大单轮输出预算能否避免该截断，本次只把 `configs/default.yaml` 的 `llm.max_tokens` 从 8000 调到 32768，并对同一个 Terminal-Bench case 运行 1 次；该配置随本阶段实验记录提交。

### Result / Evidence

Harbor 1 trial、0 exception，reward `0.0`。MiniClaude 在第 32 轮、50 次工具调用后仍以 `PROVIDER_OUTPUT_LIMIT` 结束；最后一轮 `finish_reason=length`，completion 与 reasoning tokens 均为 32768。全程 Provider usage 为 706051 prompt、83530 completion、789581 total tokens；本地估算 prompt 为 689721。没有触发压缩，也没有 `write_file` 或 `edit_file` 调用。Verifier 的 3 项检查全部失败：`vm.js` 未能在 30 秒内生成 `/tmp/frame.bmp`，后续两项也因该文件缺失失败。

同一 case 前次 8000 配置试跑为 27 轮、40 次工具调用、reward 0.0，累计 420031 prompt 和 22428 completion tokens。提高上限让运行多进行了 5 轮并完成更多检查，但没有改变任务结果，且总 Token 明显增加。Harbor 结果与 Trace 分别保存在 `benchmark/harbor/jobs/main-max-tokens-32768-mips-20260929/result.json` 和该 job 的 `make-mips-interpreter__Ucb6mtp/agent/mini-claude/traces/task_4bbefdef.json`。

### Decision / Limitation

这个单次试跑确认当前 provider 会把 reasoning tokens 计入 32768 的 completion 上限；扩大预算只推迟了截断，没有让 Agent 完成任务。单个 case、单次运行不足以评估成功率或一般成本影响；是否长期保留该默认值仍需结合后续需求和更多运行证据决定。

## 2026-09-29：复杂 Coding Task 的探索到实现提示词实验

Commit: `dd7c200`
Commit Description: `feat(agent): 调整实现策略并提高输出预算`

### Description

上一轮把单轮输出上限提高到 32768 后，`make-mips-interpreter` 仍在 32 轮和 50 次工具调用后达到 Provider 输出上限。Trace 显示任务从未写入文件；模型多次表示要收敛调查，却继续检查源码和二进制。本次只改 System Prompt：补充“缺失信息是否阻塞下一步实现”的判断、最小可验证实现和用运行反馈继续调查的原则，并把行为任务的运行验证从少见例外改为可行时的常规步骤。Max tokens、stream、压缩、Runtime 策略和其他请求配置保持不变；同一 case 只运行一个 trial。

### Result / Evidence

新增的 System Prompt 确定性测试与 Agent Note 相关测试共 4 项通过，`git diff --check` 通过。Harbor 单次 trial 为 1 trial、0 exception、reward `0.0`。Agent 用了 37 轮和 55 次工具调用，累计 Provider prompt `997825`、completion `176112`、reasoning `169721` tokens；本地 prompt 估算 `980510`。未触发压缩，`write_file` / `edit_file` 均为 0，所有工具调用都用于检查，没有 workspace mutation，也没有创建目标实现文件或运行首次实现。第 37 轮以 `finish_reason=length` 达到 32768 completion/reasoning tokens，Runtime 以 `PROVIDER_OUTPUT_LIMIT` 结束。Harbor verifier 3 项均失败：未生成 `/tmp/frame.bmp`，首项等待 30 秒超时，其余两项因文件缺失失败。Trace 位于 `benchmark/harbor/jobs/main-implementation-strategy-mips-20260929/make-mips-interpreter__uKey5ci/agent/mini-claude/traces/task_13ff2b2c.json`。

与同一配置下的前次 32768 baseline（32 轮、50 次工具调用、706051 prompt、83530 completion、78245 reasoning tokens）相比，本次多了 5 次模型轮次、5 次工具调用，累计 prompt 增加 291774、completion 增加 92582、reasoning 增加 91476 tokens；两次均没有 mutation，且都因输出上限失败。新实验中前 36 轮约消耗 136953 reasoning tokens，仍全在检查阶段。

### Decision / Limitation

这一次试验没有观察到提示词促成更早的 Analysis → Action 转换；反而在达到输出上限前继续探索了更多轮次。因此，该 Prompt-level 改动在本次样本中不足以解决过度探索假设。单次运行不能判定总体效果或提示词因果效应；但它足以说明此次任务没有出现预期的更早实现信号。本次实验代码与结果作为实验记录提交；仅运行 1 次 Harbor trial，没有追加第二次运行。

## 2026-09-30：将结构化近期文件记忆接入 main 并复跑

Commit: `44ba9a9`
Commit Description: `feat(context): 默认开启结构化近期文件记忆`

### Description

前一轮 Prompt-only Harbor 运行与预期的项目配置不一致：`main` 没有结构化近期文件记忆，而 `feat/structured-context-memory` 已有实现。该分支的功能默认关闭，且包含其他历史变更，因此本次只移入记忆核心和 Agent 接线，不合并整条分支；保留 `main` 已有的 Agent Note、压缩提醒、流式请求与其他 Runtime 行为。记忆限制为最近 8 个文件路径、最多 16 条范围观察和 1600 字符渲染；成功的 `read_file` 写入简短观察，文件被修改时旧观察失效。每轮动态上下文在检查新鲜度后临时注入，不进入持久消息历史。`FeaturesConfig.memory` 与仓库默认 YAML 均设为开启。

### Result / Evidence

结构化记忆、默认配置、Agent Note 和 System Prompt 相关确定性测试共 21 项通过，`git diff --check` 通过。第一次 Harbor 启动在模型启动前因容器 pip 依赖解析 (`ResolutionImpossible`) 失败，未产生 Agent 请求；随后唯一一次有效 trial 成功运行，Harbor 0 exception、reward `0.0`。该运行共 34 轮、50 次工具调用，Provider prompt `940572`、completion `175034`、reasoning `163360` tokens，本地 prompt 估算 `925823`；压缩 0 次。9 次 `read_file` 均成功；运行时配置来自本仓库默认 YAML，memory 已开启。当前 Trace 不序列化临时动态上下文正文，因此无法从 Trace 单独逐轮核验注入文本。

首次成功 workspace mutation 在第 23 轮，为辅助脚本 `/app/analyze.py`；第 25、27 轮又写入 `analyze2.py`、`analyze3.py`。一次第 21 轮写 `/tmp/analyze.py` 的尝试被 workspace 权限拒绝。三个辅助脚本后来都被运行，但没有创建目标 `vm.js`，也没有运行 `node vm.js`。最后一轮 `finish_reason=length`，completion/reasoning 都达到 32768，Runtime 以 `PROVIDER_OUTPUT_LIMIT` 结束；Verifier 3 项均因没有 `/tmp/frame.bmp` 失败。Trace 位于 `benchmark/harbor/jobs/main-context-memory-on-mips-20260930b/make-mips-interpreter__SK7VwAN/agent/mini-claude/traces/task_1889d301.json`。

与最近一次 memory-off、同样包含 Prompt 策略改动的单次结果相比，本次少 3 轮、少 5 次工具调用，prompt 少 57253、completion 少 1078、reasoning 少 6361 tokens；本次首次 mutation 是分析脚本，不是目标实现。该差异只来自两个单次样本，不能据此断言近期文件记忆导致了 Token 或行为改善。Prompt-level 探索转实现问题仍未解决。

### Decision / Limitation

将该功能在 main 默认启用，保留 `features.memory: false` 作为可关闭选项。实现只从显式文件工具记录路径和有限文本观察；不会从 `bash` 命令推断最近文件，也不会自动注入整份文件内容。下一步如需评估记忆本身的因果效果，需要匹配的 memory-off/on 对照；本轮只运行一次，没有追加 trial。

## 2026-10-01：抽离并收敛静态 System Prompt

Commit: `389f5e4`
Commit Description: `refactor: 抽离静态 System Prompt 构造并精简规则`

### Description

此前 System Prompt 的内容由 `MiniClaudeAgent._load_system_prompt()` 拼接，平台指导另由 `_get_platform_prompt()` 提供。Identity、Preflight 环境、Shell 使用建议、Planning、Implementation、Verification 和 Skills 混在同一段长文本里；平台提示与 Tool description / `CommandPolicy` 也有重复。`PreflightResult.to_context()` 还混入了回答语言指令。

本阶段新增纯函数模块 `src/core/prompt_builder.py`，以 Agent 身份、workspace、当前启用功能、`PreflightResult`、平台和技能索引为输入，构造 `<identity>`、`<environment>`、`<execution_policy>`、可选 `<skills>` 四个稳定区块。Agent 只保留公开的 `refresh_system_prompt()` 生命周期方法；CLI 的 `/features` 调用该方法。回答语言策略移入 identity，Preflight 的启动事实由 Builder 按字段读取。Windows 规则缩短为 shell 类型、易碎 inline quoting、平台适配工具偏好和 Runtime Policy 拒绝反馈；行为、安全细节继续由工具描述或 Runtime 负责。

动态 hot context 和 `_build_request_messages()` 没有改动，当前轮状态仍只临时追加到最后一条 user message，不写入持久对话历史。

### Result / Evidence

在同一组代表性输入（默认配置、当前工作区、Windows、ONLINE 启动快照、Python 3.14.3 和一个技能索引）下，用现有 `Compressor.estimate_tokens_for_text()` 估算：旧 Prompt 5,788 字符 / 1,231 tokens，新 Prompt 2,174 字符 / 438 tokens，分别减少约 62.4% / 64.4%。这是静态 Prompt 的近似值，不包含 tools schema 或消息上下文。

Prompt、CLI、Agent Note、结构化记忆和 Context Reliability 确定性测试合计 `38 passed, 2 deselected`；两个被排除项需要 pytest `tmp_path`，当前 Windows 临时目录权限拒绝访问。Runtime Context 中不依赖临时目录的 EnvironmentBlocker 和 Windows shell wrapper 用例 `2 passed`。`git diff --check` 通过；没有运行真实 Provider 或 Benchmark。

### Decision / Limitation

本次保留了条件化的实现/验证原则，删除历史重复章节和固定验证命令示例；没有引入新的“立即写代码”压力。Prompt 的措辞、平台建议和规则密度发生变化，预期模型行为仍应由后续匹配的真实任务或 Benchmark 验证；本阶段没有付费试跑，因此不对行为改善作结论。
