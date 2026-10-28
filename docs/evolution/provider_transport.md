# Provider 传输可靠性

输出截断恢复的完整问题、修复和测评过程统一见 [模型输出截断恢复](output_limit_recovery.md)。下文保留当时各阶段的技术记录。

## 2026-10-28：按输出截断恢复能力验收，独立于编程任务得分

Commit: `2124f03`
Commit Description: `fix: 实现模型输出截断的有界恢复与任务内降档`

### Description

用户明确本阶段的“有效”是：可信输出截断发生后，在有合法调整和恢复预算时应继续当前运行，避免直接错误结束；不要求这套机制促使模型开始编写程序，也不把 Terminal-Bench 得分混作恢复机制的验收条件。持续截断且调整不可行、上下文不足或恢复预算耗尽时，保留有界停止。

新增受控 SDK 对照：使用真实 OpenAI SDK、DeepseekProvider 及完整 Agent / 工具循环，本地 MockTransport 按请求参数返回响应。固定 high 返回 length，low 返回六个完整工具轮次后正常结束；不根据测试模式改变这个服务约定。三种模式只改变是否启用恢复和是否保留降档，均使用相同 8000 输出上限和 Run 最多四次恢复。流式与非流式各执行一遍，截断工具包含不完整参数与缺失 ID，用于确认它们始终不能执行。

### Result / Evidence

| 设置 | Provider 调用数 | 额外恢复数 | 完整工具轮次 | 结束方式 |
|---|---|---|---|---|
| 关闭恢复 | 1 | 0 | 0 | 首次 length 后停止 |
| 仅本轮降档 | 9 | 4 | 4 | 每轮重回 high，最终按恢复额度停止 |
| 同一 Run 保留降档 | 8 | 1 | 6 | 后续沿用 low，受控流程正常结束 |

流式和非流式的结果一致。这证明：当前机制在给定服务约定下能消除“每轮重置制造重复截断”的行为，也能在首次截断后获得完整响应，而不是只改变最终文本。额度耗尽后的停止仍是按规则发生，不被归为“错误结束”。模拟服务的 usage 只用于核对记录和计数，不代表真实模型成本降低。

另有真实 SDK 的无强度控制模型测试：8000 截断后按额度提高至 16000，获得完整工具调用，下一正常轮仍从 8000 开始；不发送虚构的思考参数。精确预算边界测试中，估算输入 100、输出预留 8000，额外预算 8099 拒绝重试、8100 允许一次，已用次数和预留量均与 Session 事件一致。既有恢复专项还覆盖连续截断、无 usage、上下文不足、重启不补回额度、审批及不重放工具。

历史真实 Provider 的第一个首题 Trace 也保留第 37、40、42 轮 high/8192 length → low/8192 tool_calls，随后执行有效工具；这是已有基础恢复的真实证据，不是当前全部新增代码又完成一次真实试跑。跨轮保留版本的下一次完整真实请求验证仍须使用新记录，不能用历史 Trace 冒充新结果。

本阶段还补齐超时前请求记录并进行真实子进程强杀验证，详细实现及边界维护于 session_trace_evolution.md 的同日期阶段。Windows Python 3.12.1 最终完整单测 951 项通过、13 项跳过、无失败，包含 10 项恢复效果与 13 项请求记录专项；CLI 输入和 LoopController 集成另有 37 项通过，Harbor 离线适配器 4 项单独通过。跳过原因和旧测试替身修正过程见上述记录主线。结构化对照、原始结果入口及源码指纹保存在 `sandbox/output-limit-tests/recovery-proof-20261028/`，由 collect_recovery_proof.py 从通过的 XML 与历史 Trace 提取，未把模拟结果当成新真实 trial。

### Decision / Limitation

保留现有降档、输出长度、上下文和预算边界，不增加额度、不增加自动升档、不修改工具执行或推动实现的规则。受控对照证明机制路径，历史真实 Trace 提供有限真实证据；不据此宣称所有模型或任意任务都能恢复，也不承诺完全消除 PROVIDER_OUTPUT_LIMIT。

本阶段不新增付费 Trial。后续真实验证应核对每次已完成请求的参数、结束原因和已报告用量，明确未完成请求；任务正确性仍单独由 verifier 评分。

## 2026-10-28：任务内保留降档的首题实测与超时记录缺口

Commit: `2124f03`
Commit Description: `fix: 实现模型输出截断的有界恢复与任务内降档`

### Description

实现任务内保留强度后，用户再次授权运行此前同一道 Terminal-Bench 首题。固定数据集与任务引用，实际 task checksum 与上次一致；沿用同题已通过的 Oracle，仅运行一次真实 Agent，1 task / 1 attempt / 0 Harbor retry。输出 8192、恢复上限 32768、额外预留总额 262144 保持，开启任务内记忆。宿主 Windows / Python 3.12.1，实际 Agent 容器 Python 3.13.7。安装的主循环文件及配置匹配运行前指纹，生产源码与配置在试跑中未变化。

### Result / Evidence

Harbor reward 为 0.0，三项 verifier 检查均失败。Agent 执行满 1800 秒，Harbor 记录一个 AgentTimeoutError；总耗时约 32 分 25 秒。本次停止原因是时间上限，不是已确认的恢复次数或 Token 预算耗尽。

Session 记录进入第 39 轮，63 次工具调用和对应结果，其中一个工具结果失败；工具为 Bash 42、read_file 18、list_files 1、search_code 1、TodoWrite 1。第 27 轮启动 low/8192 恢复后进入第 28 轮；第 38 轮启动 low/16384 恢复后进入第 39 轮；第 39 轮再次启动 low/16384 恢复，之后没有完成记录。后两轮直接增加长度的参数路径，支持重试前已处于 low、任务内记忆没有立即重置；不是完整的逐请求参数验证。

最后观察时 `/app/vm.js` 仍不存在，verifier 独立确认 `/tmp/frame.bmp` 缺失。评分的三项失败分别为等待图像生成超时、图像不存在、图像对比缺少输入文件。Session 最后的 Bash 调用还在检索源码与反汇编，未交付目标。

本次没有最终 Trace、headless 结果文件或 Session final 事件。Harbor Token 字段也为空，因此本地证据无法完整恢复所有请求的参数、用量、工作区变化及最终 Runtime 状态，不能填成零。当前 TraceManager 在 end_task 写出完整 Trace，逐请求记录留在内存；外部超时暴露了正常收尾之外的记录缺口。两次实验的轮次、恢复数来源不同，不能直接算作成本降幅。

证据：`sandbox/terminal_bench_runs/output-limit-sticky-first-20261028/summary.md`、`summary.json`、`comparison.json`、`manifest.json`、`setup-verification.json`、`live-observation.json`；原始 job 为 `benchmark/harbor/jobs/output-limit-sticky-first-20261028/`，trial `make-mips-interpreter__kBDXyjY`。没有修改题目、verifier 或 Runtime，也没有做第二次付费试跑。

### Decision / Limitation

保留有界恢复和任务内降档，不据本次失败加入任务专属逻辑。证据支持部分行为路径按设计运行，但任务结果仍为失败，也没有足够用量记录证明节省。

下一步先让已完成请求的参数和 usage 在外部超时后仍可读取，优先复用现有 Session JSONL，不先新增存储框架；同时调查现有进展判断为何允许持续检查而未实现目标。本阶段只记录问题，不中途改代码、不扩大额度或时间限制，不增加自动升档。只有一个随机样本，不能据此推断整体成功率或费用变化。

## 2026-10-28：按部署声明降档，并在同一任务保留已采用的强度

Commit: `2124f03`
Commit Description: `fix: 实现模型输出截断的有界恢复与任务内降档`

### Description

此前真实首题出现多次 high 截断、临时 low 后继续，但下一正常轮次又读取配置中的 high。已有恢复字段保存的是本轮已经预留的请求，成功后会清空；它们不能承担后续轮次的强度记忆。下面的早期实现记录保留当时的“先跳 low”方案，本阶段用部署明确声明的顺序替代该规则。

本次沿用现有恢复循环，只在 RunState 增加一个可选的已采用强度。正常完成或工具调用响应返回后才记住候选；再次截断、传输失败、预算不足及上下文不足均不提交。跨轮保留开关在配置类型中默认关闭，仓库 YAML 显式开启；新用户任务重新从初始配置开始，扩大过的输出上限不跨轮继承。原来的次数和 Token 预留计数不重置。

Provider 根据配置中从低到高排列的有效选择降低一项，不再使用全局五名称白名单，也不把所有兼容接口视为同一模型。无强度控制时仍只调整输出长度。仅 DeepSeek 官方 endpoint 使用其已公开的别名映射，合并 medium/xhigh 与 high 等有效相同的选择；其他部署必须声明自己的规范值，不按模型名称猜测。初始强度是上限，恢复待处理的候选也不能高于已经记住的强度。

模型适配代码负责合法参数，Agent 负责请求、提交状态和检查点，原预算模块只管次数及用量。不新增自动升档、冷却、稳定轮数或工具评分，不为强度控制改造 Registry。无效工具参数仍由现有逻辑拒绝；不把“工具没执行”误解成强度参数没被采用，也不等待审批结果才提交选择。

新快照为独立 v10、Checkpoint v11、HITL v12。旧格式读取时只补空记忆，既有预算保持；新格式删除记忆字段仍拒绝。实际 Provider 的规范选择和脱离凭证的 endpoint 摘要纳入原兼容信息；数据库只验证结构和预算不回退。源码兼容门槛继续生效，不能通过新字段迁移承诺旧审批跨升级执行。

### Result / Evidence

Windows Python 3.12.1 的完整 `tests/unit` 回归为 928 项通过、13 项跳过、无失败，耗时约 445 秒。其中输出恢复专项 64 项，覆盖跨轮保留及关闭开关、新任务重置、任意字符串档位、逐项降档、扩大长度不继承、无控制能力、无效工具 JSON、候选失败、合成 length、任务中断与新实例恢复、审批、非法快照和部署身份变化。

13 项跳过包括 10 项 POSIX Shell 语义、2 项 Windows 符号链接权限限制，以及缺少全局 Harbor 依赖而整组未收集的适配器模块。复用原有临时 Harbor 依赖单独补跑该模块，4 项离线测试通过；没有安装全局依赖。CLI 输入与 LoopController 两组集成另有 37 项通过。不能把这些分组结果称为再次完整运行包含 Harbor 的全部单测。

最初沙箱内的完整回归遇到本机 HTTP 模拟服务访问被拒绝（WinError 10013），该轮已停止；上述完整结果来自允许回环网络的环境。原始 XML 保存在 `sandbox/output-limit-tests/sticky-full-local.xml`、`sticky-harbor.xml`、`sticky-integration.xml`。所有 Provider 和外部安装行为均为假的对象或本地模拟，没有启动真实模型、Docker Benchmark 或新的付费试跑。

### Decision / Limitation

保留单向降档。更多档位时，现有每轮两次额度可能不足以到达最低；不为遍历所有档位增加预算。配置中的有序声明属于部署契约，旧版本只检查成员关系，升级时需人工核对原列表顺序。未知服务的别名、是否允许任务中切换，以及思考模式与强度的关系不能从名称猜出。

数字预算暂不增加公共区间或插值框架；真实接入对应 Provider 时再由适配层转换离散选择。不改思考模式开关，不切换模型。已有首题证据只支持修复下一轮重置，不证明降档能提高任务成功率或减少总费用；真实效果仍须后续批准对照实验。

## 2026-10-28：小输出额度下的首题真实验证

Commit: `2124f03`
Commit Description: `fix: 实现模型输出截断的有界恢复与任务内降档`

### Description

用户在离线验证完成后明确要求运行 Terminal-Bench 第一题，并先不要使用很大的输出额度。本次仅降低仓库 YAML：正常请求 8192、恢复上限 32768、额外请求预留总额 262144；不改变恢复机制，也不根据题目加入特殊行为。配置类型的缺省值保持原实现设置，当前仓库默认配置使用上述较小额度。

Harbor package registry 元数据确认数据集的第一题就是 `terminal-bench/make-mips-interpreter`，版本与此前缓存一致。先运行同题 Oracle，reward 1.0、无运行异常，确认 Docker 和评分链路能工作；随后运行真实 DashScope / deepseek-v4-flash-0731 一次，Headless、温度 0.7、流式请求、1 task / 1 attempt / 0 Harbor retry。没有改任务、verifier 或成功标准，也没有做第二次付费试跑。

### Result / Evidence

真实 Agent 得分 0.0，Harbor 0 exception。总耗时约 31 分 5 秒，其中 Agent 执行约 29 分 24 秒；结束原因是恢复次数用完，不是 Agent 的 30 分钟超时。

44 轮共发出 48 次模型请求，其中 5 次 high 响应以 length 结束，均为思考用满 8192。第 36、37、40、42 轮通过 low / 8192 获得 tool_calls 响应并继续；第 44 轮第五次截断时，Trace 记录“输出恢复次数已用完”，没有发出第五次额外请求。实际所有请求的 max_tokens 都是 8192，没有用到提高输出上限。每轮最多两次与 Run 最多四次的设置没有被混成同一个计数。

第一次 low 响应仍用满 8192，其中 8094 是思考，生成的 Bash 参数 JSON 不完整；既有校验将其拒绝，没有执行，之后继续重新生成。因此这里的“恢复”只表示拿到非 length 响应并继续循环，不保证模型工具参数正确。后三次 low 响应的输出分别为 1784、1986、4844 tokens，对应工具调用被执行。

Trace 记录输入 1327658 tokens，其中 1189888 命中缓存；输出 109682，其中思考 101504。总计 1437340，已核对等于全部 48 个响应 usage 之和，包含失败的 high 请求。四次额外恢复请求合计 197761 tokens；这些数字是报告用量，不是费用金额。最终请求配置与运行前 manifest 一致，源码指纹也没有变化。

70 次工具调用为 Bash 65、list_files 1、read_file 4，没有 write_file/edit_file。69 次工具带有工作区前置观测，记录到的变化事件为 0；没有压缩。只读检查和最终评分确认目标 vm.js/frame.bmp 未产生，评分三项均失败，其中包含等待目标运行/产物的超时与 frame.bmp 缺失。不能把“没有 write_file”单独当作无写入证据，本次同时核对了工作区变化和目标产物。

证据：`sandbox/terminal_bench_runs/output-limit-8192-first-20261028/summary.md`、`summary.json`、`manifest.json`；原始 job 为 `benchmark/harbor/jobs/output-limit-8192-first-20261028/`，trial `make-mips-interpreter__n3K7RpP`，原生 Trace `agent/mini-claude/traces/task_725c548c.json`。Oracle 结果保存在独立 job `output-limit-oracle-first-20261028`。完整 Harbor 0.23.0 CLI 依赖仅安装到仓库忽略的临时目录，未修改全局 Python。

### Decision / Limitation

保留较小的仓库默认额度。此样本表明不增加单次输出上限也能恢复部分截断，并且累计用量与次数上限真实生效；没有解决模型持续检查而不实现目标程序的问题。未因单题失败加入运行阶段强制切换或其他任务专属规则。

这不是与旧参数一致的 A/B，也只有一个有效 trial，不能推断整体成功率或声称已经改善任务完成率。Headless 的进程退出码仍为 0，原生 Trace 明确 FAILED，Harbor reward 为 0；结果解释仍须以 Trace 和 verifier 为准。

## 2026-10-28：输出截断后安全重新生成

Commit: `2124f03`
Commit Description: `fix: 实现模型输出截断的有界恢复与任务内降档`

### Description

此前 Harbor 的长任务曾在模型思考耗尽单次输出额度后结束。Runtime 已经拒绝把截断正文当作完整答案，也不执行截断响应中的工具，但只能记录 `PROVIDER_OUTPUT_LIMIT` 后失败。单纯把输出上限从 8,000 提高到 32,768 没有解决已有单次样本，相关实验仍以 `context_management.md` 的历史记录为准。

本次为当前 OpenAI-compatible 主链路增加有限次数的重新生成。恢复只覆盖当前模型请求，输入是截断前可信的消息；不把截断思考塞回模型，不重复已完成的工具。Provider 根据显式配置提供可用参数，Runtime 决定是否还有恢复额度。百炼的 `deepseek-v4-flash-0731` 支持 `reasoning_effort=low/high/max`，默认 high，依据为[百炼官方 API 文档](https://help.aliyun.com/zh/model-studio/deepseek-api)。默认部署先尝试 low，再提高输出上限；其他模型没有声明推理强度时不发送该控制参数。Anthropic 本次不接入。

恢复默认每轮最多额外两次，整个 Run 最多四次。除了次数限制，还按每次估算输入和完整输出额度预留 Token 预算；报告用量超出预留时补记差额，不退还少用或无法确认的部分。预留不是账单预测，而是避免缺少 usage 或频繁重启导致无限重试。预算和当前尝试写入 RunState，并在额外请求前保存到原有 `before_model` Checkpoint。新进程保守跳过已经预留的尝试，可能少获得一次有效恢复机会，这是为避免重复收费而保留的取舍。完整响应仍走原工具、权限和人工审批路径。

同时修复两处识别边界：流式响应已正常以 length 结束时，缺失工具 ID 不再遮住停止原因，整个工具批次仍被丢弃；压缩摘要自身截断时拒绝替换原历史，但保留该请求的 usage。只有完整请求的估算表明输出空间不足才尝试现有压缩，重新计算剩余空间并保留安全余量，不根据 output=0 或缺少 usage 猜测上下文溢出。

Trace 为同一模型轮次增加 `provider_attempts`，分别保存各次请求参数、停止原因、usage 和传输异常类型，任务总量累计所有已报告用量。未发出的请求另标 `not_requested`，不冒充模型调用。Trace 的请求配置同时保存恢复设置，便于之后对照。新快照版本为独立 v7、Checkpoint v8、HITL v9，旧 v1–v6 保留读取；新格式不能通过删除恢复字段重置预算，Store 也拒绝总恢复次数或预留预算回退。

### Result / Evidence

Windows Python 3.12.1 的恢复专项 32 项通过，包含真实 OpenAI SDK 经本地 HTTP Mock 的流式和非流式恢复、缺失工具 ID、连续截断、无 usage、预算上限、跨实例预算、模型请求前退出、审批后继续、拒绝审批后的再次恢复与 Store 预算防回退。Harbor 0.23.0 四项离线适配器测试通过；依赖仅装在仓库忽略的临时目录。CI 的 CLI 输入与 LoopController 两组集成共 37 项通过；最终 wheel 已离线构建并确认包含恢复模块与 headless 入口。

完整单测执行结果为 895 passed、2 failed、12 skipped。两个失败指出未声明能力时不应给 Provider 初始化附加 None/空列表；现仅在显式声明且使用兼容 Provider 时转发，Anthropic 原参数形状保留。随后 Provider Bootstrap、恢复、流式和截断组合 72 项全部通过，其中 Bootstrap 14 项含 3 项新增能力转发用例。按测试身份取最新结果为 900 项通过、12 项跳过，不能把它表述为修复后又完整重跑一次全绿；原始完整 XML、Bootstrap 复测 XML 和去重 JSON 均保存在 `sandbox/output-limit-tests/`。跳过项为 10 项 POSIX 和 2 项 Windows 符号链接权限限制。

完整检查还发现两处旧测试手写配置没有新增字段，现改用真实配置类型，原业务断言保持；相关 156 项复测通过。没有删除工具配对、权限或恢复断言来迁就实现。

先前沙箱内测试遇到临时目录文件替换拒绝与异步事件循环停顿；改用仓库临时目录，并在沙箱外验证 Harbor 后通过。这些属于运行环境限制，不作为 Runtime 失败证据。没有执行付费模型请求或新的 Terminal-Bench trial。

### Decision / Limitation

保留重新生成，不承诺恢复不可见思考的断点。参数声明针对具体部署，更换模型需复核；预算估算和安全余量也不能代替服务端 Token 校验。没有增加针对任务 ID、固定错误或 verifier 的规则，没有修改 Harbor 的启动和 reward 判断。

源码和配置兼容校验继续生效，旧版本未完成 Checkpoint 可能拒绝跨代码恢复，不能通过修改快照版本或关闭校验强行继续。Headless 仍默认不启用人工审批，已有 Trace 即退出码 0 的行为本次保留，应以 final_status 和 verifier 结果判断任务是否完成。真实模型的恢复效果、费用和成功率仍需后续获准后测量。

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
