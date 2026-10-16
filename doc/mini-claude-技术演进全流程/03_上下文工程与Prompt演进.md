# 上下文工程、日志外置与 Prompt 演进

> 版本 `5f41f1d09806ed8a4c387bd80b47a07adaa45609`。对照源码：`src/core/compression.py`、`src/core/context_memory.py`、`src/core/agent_note.py`、`src/core/prompt_builder.py`、`src/core/tools/base_tools.py` 与 `src/agent/mini_claude_agent.py`。历史证据：`docs/evolution/context_management.md`、`docs/evolution/tool_deduplication.md`、`eval_reports/log_to_file_refactor_v3/compare.md`、`eval_reports/log_to_file_refactor/task014_compare.md`。

## 1. 为什么“减少 Token”不是这个问题的充分描述

Coding Agent 执行较长的任务时，需要不断阅读源文件、工具日志、单测报错和自身先前判断。每次模型请求通常需要带上相关历史，所以一个冗长 Shell 输出不只增加本轮内容，还会在后续轮次重复计入上下文。直接把全部历史放回模型会不断涨成本和延迟；直接截断则可能恰好删除定位故障最有价值的中间行，导致模型再次探索或根据不完整结果胡乱判断。因此目标是维持三种信息的可用性：**当前有效的任务要求、足够解释历史行动的结构化事实、能按需恢复的原始诊断材料**。若只考察 Token 而不检查任务完成/错误率、重复探索、外部资源可访问性，成本下降可能是以信息丢失换来的。

上下文工程在仓库中实际分成三个改造维度：工具端减少不必要的“直接注入量”；消息端执行微压缩、摘要与消息配对清理；请求前正确估算完整请求，包括稳定 System Prompt 与工具 Schema。它们分别拥有不同的证据和失败方式，不能把 `task_014` 的 68.8% 直接归因于摘要算法。

## 2. 最早的消息裁剪：增加行号未必提高效率

历史 `docs/evolution/context_management.md` 记录过 `read_file` 输出携带行号的尝试。行号有助定位后续 `edit_file`，但会让每条返回都附额外文本；在读取次数多的任务中，这些字节会被再次累计发送给模型。此阶段后来移除了默认行号。工程意义在于：上下文可读性和调用成本并非单调关系；看似有助于编辑的元数据，可能在大规模重复读取中造成净成本。

2026-05-29 `4d8cc169` 引入了按阈值执行的微压缩和 Stub 替换，试图只对已经较低价值的旧工具输出留下短占位符，而不是把全部 Tool Result 永久留在请求。若后续模型需要回到原始内容，就要求宿主有合理的日志或文件位置可用。该阶段仍有重要正确性风险：压缩边界若切断 Assistant 的 Tool Call 与对应 Tool Result，可能制造 Provider 协议不合法的消息；如果错误地把执行失败的 Tool Result 当作成功并 Stub 化，又会掩盖阻断事实。

## 3. 长日志外置为什么比单纯截断更安全

`task_013_large_log_debug` 的测试输出可达到约 147,000 字符。旧 `ShellSession` 一度在约 50,000 字符处硬截断，而模型无法看到被删去的内容，也没有稳定路径再次取回。演进记录显示，模型部分时候自己会把 Shell 输出重定向到普通文件再检索；但这个行为不稳定、会消耗额外轮次，并非 Harness 提供的可靠语义。2026-08-17 `d360134` 一线把大输出先保存为日志，再将退出码、原始行数/字数、头尾预览和相对日志路径返回给模型。后来为了避免中等源码窗口被过早文件化，输出阈值再调整到默认约 200 行或 4,000 字符，同时 Trace 记录完整输出与可见预览的长度和截断状态。

用一条**机制示例，非本次新运行日志**解释：`bash(pytest)` 产生 2,251 行，唯一诊断在 1,100 行。如果只截断为最前与最后几十行，模型根本看不到该行；工具外置将完整内容保存为日志文件并给出路径。模型可先用 `search_code` 查找 `AssertionError`、`timeout` 等关键词，再 `read_file(start_line=...)` 查看附近窗口，定位根因。不是说“文件保存了，所以模型一定会检索”：模型仍可能在正文预览不足时不读取保存日志、自己重定向输出，或者重复检查目标源码。

### 3.1 第一次漂亮数字，为什么后来降级

旧 `task_013` 5×5 对比报告 `eval_reports/log_to_file_refactor/compare.md` 曾展示平均总 Token 143,457 → 47,960，即下降 66.6%，成功率均为 5/5。然而对 Fixture 审核发现原基线含可指向正确答案的注释，部分旧运行出现极端长上下文；这样的对照不适合直接得出一般收益。重新清理任务、固定任务/配置/Verifier hash 后，`eval_reports/log_to_file_refactor_v3/compare.md` 记录了更可靠的同条件五次对照：

| `task_013` v3 | Baseline | 文件化版本 | 解释 |
|---|---:|---:|---|
| 验证通过 | 5/5 | 5/5 | 不表示通用成功率 100% |
| 平均总 Token | 54,693 | 39,147 | 降低 28.4% |
| 峰值单轮 Token（平均） | 7,810.6 | 5,504.2 | 降低 29.5% |
| 平均模型轮次 | 7.4 | 8.2 | 反而增加 10.8% |
| 平均耗时 | 10.842s | 11.872s | 反而增加约 9.5% |

对照 manifest 中的环境是 Windows 11 / Python 3.12.1，两个 Agent Commit 分别为 `c656000f` 和 `f0b29086`，任务集 hash 均以 `d428474a` 开头。两个版本任务成功都维持 5/5，才有资格进一步讨论成本；不能隐藏轮次与耗时恶化。并且该重测里模型仍未形成充分可靠的“读取日志中间部分”的证据，不能据此宣称自主日志检索已经稳定。

### 3.2 针对中间日志的独立任务

为避免成功完全依赖 Head/Tail，又设计 `task_014_large_log_middle_debug`，唯一有效诊断在输出第 1,100 行。`eval_reports/log_to_file_refactor/task014_compare.md` 记录 Baseline `5111599f` 与 Refactor `2dd60c22`，固定任务哈希以 `f440451f` 开头、环境 Windows 11 / Python3.12.1，5 次对 5 次：均为 5/5 成功，平均 Token 253,998 → 79,288（下降 68.8%），平均峰值 24,295 → 7,805（下降 67.9%），平均轮次 14.8 → 13.8，平均耗时 22.588s → 21.590s。该次历史报告还观察到了对保存日志的 `search_code`/`read_file` 检索及第 1,100 行附近的信息，验证了特定任务条件下的回查路径。

这两个实验**任务不同，不能合并成一个整体“Token 下降约 67%”**。尤其 66.6% 是存在答案泄露/极端运行影响的早期 `task_013` 数据；68.8% 是不同的中间日志任务；28.4% 才是 `task_013` 清理后更可信的对照。所验证的是特定日志输出处理，不是整个 Agent 的所有任务、所有模型或全生命周期上下文管理的普遍收益。

## 4. 压缩算法本身经历了正确性修复

9 月的 `402f474`、`08fd7cf`、`a543b62` 记录了三个核心正确性问题。第一，估算 Token 如果遗漏持久化 Tool Call 参数，本地看似没有超过阈值，真实请求却可能超额；第二，微压缩如果只根据工具名称猜测操作成功，可能把重要失败输出当作可替换垃圾；第三，全量摘要在切分 middle 和 tail 时不能切断 Tool Call/Result 配对。于是引入消息清洗 `Compressor._clean_tool_chains()`、OpenAI 兼容消息配对修复、对已完成低价值输出的保守替换，并为读取工具补字符/字节/行数硬上限。

另一个真实错误是“没有可压缩 middle 还创建 Summary/Transcript”，会凭空改变上下文；修复后无 middle 时保持 no-op。摘要 Provider 失败时不应先删除原始消息再抛异常；更新要以压缩结果成功和校验为前提，避免半完成状态。Transcript 跨进程缓存、retention 索引同样需要区分“逻辑删除”和实际文件系统是否已删除：外部 I/O 拒绝不能被内存索引装作成功。

当前 `src/core/compression.py` 的职责不是简单一个 `truncate()`：包含 `estimate_prompt_tokens()`、`should_microcompact()`、`microcompact()`、`compress()`、`sanitize_openai_messages()`、`_clean_tool_chains()`、`_llm_summarize()`、Transcript 加载与回收。涉及函数具体返回值的细节以 SHA 固定的实现为准。压缩机制会牵涉模型摘要，摘要可能误删关键条件；当前没有完成对任意跨模型摘要可靠性的通用证明。

## 5. 为什么后来要计算完整请求预算

历史系统曾使用与实际模型窗口不匹配的隐式 70K/100K 压缩阈值，导致号称 1M 的 Context Window 很早就压缩；同时仅估算 durable messages，遗漏 System Prompt、工具 Schema 和动态 Hot Context。`1986265` / `c682808` 改成显式配置：`context_window_tokens=1_000_000`、微压缩阈值 `250_000`、全量压缩阈值 `500_000`。这些数值是面向当前模型的工程基线，而非从 A/B 搜索得到的“最优参数”。

当前模型请求前 `_llm_tool_cycle_impl()` 先从消息历史构造实际发送消息，融合临时工作笔记、近期结构化文件观察、执行预算信息，再估算消息＋稳定 System Prompt＋工具定义；压缩后重新计算。这解决了“算的不是要发的东西”的问题，但源码及历史报告明确说明本地 `tiktoken` estimated tokens 不等同于 DeepSeek Provider 专有 tokenizer；真正请求的 Token 用量应读 Provider `usage.prompt_tokens`。如果编码初始化失败，估算器还有粗略回退，不能把估计值包装成真实账单精确值。

此外，当前仍未建立压缩后的严格 Context Window 余量硬性 invariant，Summary 输入超长等情况需要 fail closed；阈值与模型可能发生变化，静态预算策略仍应谨慎使用。

## 6. Agent Note 和结构化近期文件记忆分别解决什么

`Agent Note` 面向跨压缩保留的用户任务约束、已确认结论和关键证据。`06b28ead` 引入自动压缩前提醒：在下一次压缩前给模型一次机会，通过 `update_agent_note` 保存需要继续使用的信息。笔记存于运行数据区，随后每轮作为临时 Hot Context 注入；它不能取代源代码与 Verifier。**笔记是模型编辑的信息**，应以经过实际验证的事实为主，不能把它当作绝对可信的结构化事实源。

`StructuredContextMemory`（`src/core/context_memory.py`）则针对近期源码观察：存储近期文件引用、观察摘要和可检查的新鲜度；渲染时只占有限字符，失效时应更新或丢弃。两者方向不同：Note 保存任务级语义约束，Structured Memory 帮助减少重读近期文件。但它们都可能过期、增长、遗漏关键条件，因此需要动态刷新和有限窗口，不能宣称拥有完整长期记忆。

2026-09-30 的 `44ba9a9` 把结构化文件记忆接入默认主线。它的一次有效 Harbor Trial（`make-mips-interpreter`）为 34 轮、50 次工具调用、940,572 prompt、175,034 completion tokens、reward 0。虽然相较相近单次 memory-off 结果少了三轮和五次工具调用，但目标文件 `vm.js` 没有完成，且执行中写入的是辅助分析脚本。**单次随机模型试跑、任务未成功，不允许证明这项记忆改善了 Coding Agent 能力**。

## 7. System Prompt 从大字符串改成可维护的稳定前缀

`389f5e4` 将静态 System Prompt 组装抽到 `src/core/prompt_builder.py`，区分身份、环境、执行原则、技能目录等稳定信息；动态任务状态不应每轮改写持久历史。`docs/evolution/context_management.md` 中在一组代表性输入下的静态 Prompt 统计为旧 5,788 字符 / estimated 1,231 tokens，新 2,174 字符 / estimated 438 tokens，即静态部分估算降低约 64.4%。**这不是完整 Agent 请求 Token 下降 64.4%**，因为工具、历史、动态上下文仍占用大量输入。这次重构的根因之一还是旧 Prompt 中重复或失效规则使维护困难，而不是纯粹为了指标。

此前对 Harbor 同一 `make-mips-interpreter` Task 的单次实验尤其值得保留失败：扩大 `max_tokens` 从 8000 到 32768，执行约 32 轮、50 次工具调用，仍 `PROVIDER_OUTPUT_LIMIT`、reward 0；随后提示词强调“缺失信息是否真正阻断实现”，模型 37 轮、55 次工具调用，依旧几乎只有探索、不产出目标实现，reward 仍 0。看似更明确的提示并没有在这一个样本中解决从 Exploration 到 Implementation 的行为转换问题；无法证明一般性 Prompt 无效，但必须反对“改了 Prompt 就提升了任务成功率”的叙述。

## 8. 当前行为边界

当前实现将工具输出、消息摘要、Note、Structured Memory 和 System Prompt 分层维护，是为避免整个项目把所有东西都视为“聊天历史字符串”。但没有证据证明任意复杂代码任务都能在压缩后恢复所有关键依赖；也没有证据表明每次外置日志模型都会正确主动检索。输出文件化阈值、微压缩窗口和摘要保留策略还有成本、信息缺失与误引用取舍。最值得传承的方法是：**先把模型实际收到的信息量与原始执行事实分离，再逐层减少冗余、修复消息一致性，最后才在固定任务/固定环境中比较模型真实成功率和 Token**。

相关测试：`test_context_foundation.py`、`test_context_reliability.py`、`test_context_audit_foundation.py`、`test_structured_context_memory_runtime.py`、`test_system_prompt_strategy.py`、`test_tool_output_visibility.py`。历史记录存在明确的通过/跳过数字，但本次没有重新运行；本篇实验表是既有报告的复核，而非本次新评测。
