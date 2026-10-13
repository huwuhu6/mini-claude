## 2026-10-01：统一 MainAgent 与 SubAgent 的共享工具定义

Commit: `330e11d`
Commit Description: `refactor(tools): 统一主代理与子代理工具定义`

### Description

第一阶段让 MainAgent 用 `ToolRegistry` 将 schema 与 handler 绑定在一起，但共享基础工具的 schema 仍只写在 MainAgent；SubAgent 又单独维护了一份 schema 和 handler 字典。两份定义已经发生实际漂移：SubAgent 的 `read_file` schema 使用无效的 `limit`，handler 却传 `start_line` / `end_line`；SubAgent 的 `edit_file` schema 使用 `old_text` / `new_text`，handler 却传 `edits`。

本阶段新增轻量的共享 ToolSpec factory，集中定义 `bash`、`read_file`、`write_file`、`edit_file` 的 schema。MainAgent 和每个 SubAgent 实例仍各自创建 Registry，并自行选择工具、绑定 handler。MainAgent 的 15 个工具名称与顺序保持不变；只有四个基础工具共享定义。SubAgent 的 EXPLORE 仍只暴露 `read_file`；GENERAL、PLAN、REVIEW 继续暴露相同的四个基础工具，且所有 SubAgent 都没有 `task`。

TodoWrite 的 handler、TodoManager 以及 RuntimePolicy / Compression 对历史 TodoWrite 消息的兼容逻辑仍保留。当前 LLM registry 没有注册 TodoWrite（旧 schema 是注释代码），README 和演进文档中也没有明确要求将其启用，因此本阶段保持 dormant 状态，并删除 System Prompt 中让模型调用未暴露工具的说明。

### Result / Evidence

新增回归测试检查 MainAgent 工具名及 Provider 格式、四个共享 schema 的相等性、各 SubAgent 类型的工具集合、无 `task`、Registry 实例隔离、read/edit 实际 dispatch 参数、未知工具兼容行为、FeatureManager 过滤以及 System Prompt 不再提及 TodoWrite。

工具注册与提示词单测 16 项通过；SubAgent 集成测试 1 项通过；LoopController / TodoWrite 兼容测试 35 项通过。尝试运行完整 `test_all_modules.py` 时，若干与本次无关的用例因 Windows 临时目录 ACL 或外部 `mini-claude-project-data` 路径权限错误失败；针对 SubAgent 的集成用例独立通过。未运行真实 Provider。

### Decision / Limitation

共享层只包含当前确实由 MainAgent 与 SubAgent 共用的四个工具，不引入 BaseAgent、profile、能力框架或新的 Runtime 抽象。MainAgent 的工具顺序与 feature filtering 留在原有职责中。TodoWrite 仍未向模型暴露；若将来要启用，应单独确认产品意图并补齐对外契约测试。

## 2026-10-01：恢复主 Agent 的轻量 TodoWrite

Commit: `eb1b021`
Commit Description: `feat: 恢复主 Agent 轻量 TodoWrite`

### Description

TodoWrite 早期曾向模型开放。2026-05-30 的 `c8db40a` 将 schema 注释掉：当时的试跑观察到 Token 与轮数下降，并推测这是省去了维护 Todo 的成本，但仍看到模型编写验证脚本。后续 ToolRegistry 重构保留了 `TodoManager`、handler、压缩历史兼容和 RuntimePolicy 的意图归一化逻辑，却没有明确的产品要求接回工具，因此将它留在 dormant 状态。

本阶段重新启用它作为主 Agent 的轻量 planning state，不接入冻结的 tasks/team 多 Agent 基础设施。工具 schema 进入 MainAgent 的 `ToolRegistry`，由现有 handler 更新 `TodoManager`；FeatureManager 没有 Todo 专属开关或映射。每次 `MiniClaudeAgent.run()` 开始时清空上一次 run 的 Todo，确保动态状态不会跨任务残留。

现有 Manager 校验最多 20 项、合法状态和最多一个 `in_progress`。移除了“连续三轮未更新就 nag”的计数与重复提醒，避免模型为了消除提醒而频繁重写 Todo。打开的 Todo 仍通过每次请求构造的 transient hot context 提供；tool result 继续使用固定短文本，hot context 不写回 `self.messages`。

### Result / Evidence

Todo runtime、ToolRegistry、冻结多 Agent 合约、Agent Note、结构化记忆和 LoopController 测试共 `73 passed`；System Prompt 策略测试另有 `9 passed`。其中检查了模型可见 schema、真实 Registry handler 更新、状态约束、run 生命周期、动态上下文及消息历史不被 hot context 改写。Headless 测试因 Windows pytest 临时目录 ACL 在 fixture 初始化阶段失败，未进入断言；没有运行真实 Provider 或 Terminal-Bench。

### Decision / Limitation

Todo 现在可表达模型自己的高层任务进度，并可作为后续 execution-progress governance 的观测输入；本阶段没有据此添加强制阶段控制、MUTATION_STARVATION 或 Anti-Loop 规则。此前关闭 TodoWrite 的单次观察说明维护 Todo 可能增加成本，因此真实任务中它是否能促进探索转实现，仍需用同一 Terminal-Bench case 的 Trace 做单次观察或后续 A/B 评测后判断。Todo 生命周期目前以每次公开 `run()` 为界。

## 2026-10-02：在稳定 Prompt 中说明 TodoWrite 的适用场景

Commit: `d0bc3e1`
Commit Description: `fix: 明确复杂任务的 TodoWrite 使用策略`

### Description

TodoWrite 恢复后，首个 `make-mips-interpreter` trial 里模型进行了 36 轮请求和 53 次工具调用（41 次 `bash`、12 次 `read_file`），但没有调用 TodoWrite，也没有修改文件；任务在 Harbor 1800 秒上限处超时。工具已经在 MainAgent 的模型可见列表中，生命周期和 hot context wiring 也正常，因此这次现象指向工具选择策略缺少引导，而非 TodoWrite 注册失败。

本阶段只在稳定 execution policy 中增加一条适用规则：复杂多步骤 Coding Task 在大量探索或实现前建立小型高层计划；一次只标记一个进行中目标，只在目标变化或完成时更新；简单单步任务跳过 TodoWrite。没有改变 schema、RuntimePolicy、FeatureManager 或执行治理。

### Result / Evidence

Prompt 策略、Todo runtime 和 ToolRegistry 定向测试共 `32 passed`；`git diff --check` 通过。单测确认 Prompt 包含复杂/简单任务的差异化指导，TodoWrite 仍存在于 MainAgent 模型可见工具中，现有实现没有每三轮 nag。

按授权对同一个 `terminal-bench/make-mips-interpreter` 运行 1 trial，job 为 `benchmark/harbor/jobs/todo-write-prompt-policy-20261002/`。Agent 执行达到 Harbor 的 1800 秒时限，Harbor 记录 `AgentTimeoutError`、reward `0.0`；Verifier 的 3 项检查均因未生成 `/tmp/frame.bmp` 失败。Trace 有 32 次模型请求开始事件、49 次工具调用（45 次 `bash`、3 次 `read_file`、1 次 `list_files`），没有 `TodoWrite`、`write_file` 或 `edit_file`，也没有目标实现文件或首次程序运行。模型在多轮中说明调查方向并继续检查 ELF、stdlib 和指令集；最后一个模型请求未能在 Agent 时限内完成。

Harbor 结果中的输入/输出 Token usage 为 `null`；超时也导致 Agent 最终 usage 结果文件未生成，因此本 trial 没有可核实的累计 prompt、completion 或 reasoning Token 数。此前未加 Todo 使用策略的单次 trial 为 36 轮请求、53 次工具调用（41 次 `bash`、12 次 `read_file`），同样没有 TodoWrite、文件修改并以 1800 秒 timeout 结束。两次单样本都未观察到 TodoWrite 或实现动作；不能据此归因于 Prompt，也不能推断总体成功率。

### Decision / Limitation

本次 Prompt contract 与工具可见性均通过确定性测试，但唯一一次后续 trial 没有出现 TodoWrite，亦未发生 workspace mutation 或实现运行；尚未观察到复杂任务使用 TodoWrite 建立高层规划的证据。不要仅凭这个 case 的单次失败再叠加 Prompt 规则。若继续研究，应先查看完整模型输出与中断请求的证据是否可获得，再决定是否需要另一个具备不同行为指标的评测任务；任何新策略仍应独立验证，不能将 Todo 维护直接等同于执行进展。

## 2026-10-02：修复工具观察结果的可靠性

Commit: `8c7af3e`
Commit Description: `fix: 提高工具结果观察的可靠性`

### Description

ShellSession 原先为复合命令构造了包含状态标记的命令文本，但 POSIX 分支仍执行未经修改的原命令。因此 Runtime 无法获知被后续成功命令掩盖的早期失败。本次只对可安全识别的单行 POSIX `;` 命令链插入状态标记；遇到多行、heredoc、注释、状态变量、命令替换、控制结构等无法可靠解析的语法时，保留原命令不变。BaseTools 根据结构化 `segment_exit_codes` 给模型补充简短的部分失败标记；普通 stderr 警告仍不作为失败证据。

搜索工具此前在路径展开中绕过了统一 WorkspaceAuthority 检查，目录遍历和 glob 结果也不稳定；命中数恰好达到上限时还会误报截断，最终输出边界可能切开上下文块并丢掉警告。本次改为在直接路径、glob 锚点和每个结果上复用 `safe_path`，按固定顺序遍历，跳过 broad search 中的 `.agent` Runtime 数据但允许显式搜索；限制后的结果以完整匹配行或完整上下文块输出，并在文本和 `ToolResult.output_visibility` 中记录搜索范围、截断原因、遗漏匹配下限及跳过文件。`search_code` 明确按行匹配正则；`count_occurrences` 明确返回正则出现次数。

MainAgent 的 `read_file`、`search_code`、`count_occurrences` 和 `list_files` handler 此前把 BaseTools 的 `ToolResult` 降级成字符串。本次保留结果对象，使执行事实及可见性元数据能通过现有 Agent Loop、Trace 和 AttemptHistory 边界。

### Result / Evidence

Windows Python `3.14.3` 上的非临时目录定向回归为 `117 passed, 10 skipped, 7 deselected`；被跳过的是 POSIX-only / symlink 权限场景，被排除项依赖被 ACL 限制的 pytest 临时目录，Windows CMD 状态掩盖用例另以本地 workspace 直接调用验证通过。Context audit 的完整 Linux 运行覆盖了这些临时目录用例。

Linux 验证使用 Docker Python `3.12.15`、当前工作区 bind mount 和项目 editable install。工具观察、ToolRegistry、ObservationNormalizer、System Prompt、LoopController、Runtime Context、完整 Context audit 与 search_code 集成测试合计 `160 passed, 1 skipped, 1 deselected`。唯一 skip 是 Windows CMD 专属测试；唯一 deselected 是一个既有 Provider parse-error 测试，它的 Agent fixture 缺少当前 `_llm_tool_cycle()` 要求的 `config.llm.provider/model/stream`，与本次改动无关。POSIX 普通命令成功/失败、`false ; echo ok` 分段失败、多个 segment、引号内分号、成功/失败 heredoc、多行 heredoc 后续命令以及完整 ShellSession → BaseTools → ObservationNormalizer / LLM-visible marker 均通过。Linux symlink 可用，直接路径与 glob 路径的越界链接均被拒绝；`count_occurrences` 权限校验、额外授权根、稳定排序、`.agent` 隐式排除/显式可读、exact max 与 max+1、整上下文块 budget 和 metadata 也通过。

Windows 的完整 Context audit 测试仍受系统 pytest 临时目录 ACL 阻挡，不能把这些 setup errors 算作代码通过。没有运行真实 Provider 或 Terminal-Bench。

### Decision / Limitation

保持 WorkspaceAuthority、ToolResult、ObservationNormalizer 与既有 Trace 数据流为唯一事实来源，没有加入新治理策略。POSIX 状态采集采用保守语法门槛，无法识别的 Shell 构造可能不提供 segment 级状态，但执行命令本身不变。输出行预览仍受单行 200 字符限制，并在可见文本与 metadata 中标记；完整内容应通过缩小范围或其他读取方式获取。下一次 Terminal-Bench 运行应观察 bash 部分失败标记、search 输出可见性 metadata，以及是否还有截断后被误认为完整结果的 Trace。

## 2026-10-10：主 Agent dispatch 前复查 Feature 能力

Commit: `3f61cee`
Commit Description: `fix: 在主 Agent 工具 dispatch 前复查 Feature 能力`

### Description

FeatureManager 原先只在构造发给模型的工具列表时过滤工具。ToolRegistry 仍保留 handler，而 Main Agent 的 `_execute_tool()` 只检查 handler 是否注册，所以模型生成工具调用后若 Feature 被关闭，过期调用仍可能 dispatch。

本阶段把工具所属 Feature 的查询收敛到 `FeatureManager.is_tool_enabled()`，模型可见性过滤与 Main Agent 的执行前校验复用同一映射。主执行入口返回明确的 dispatch 状态；功能关闭和工具未注册会成为未执行的阻断结果，仍为 Tool Call 写入对应 Tool Result。控制类 `RuntimeEscalationException` 从普通工具异常捕获中透传到现有 Agent Loop 控制处理器。

### Result / Evidence

Windows Python `3.14.3` 上，工具批次闭环、ToolRegistry、工具观察、Todo、Runtime Resolution、LoopController、CLI、System Prompt 和结构化记忆定向回归合计 `136 passed, 10 skipped`。10 项跳过均为当前 Windows 环境不支持 POSIX shell 或符号链接的用例。新增测试覆盖模型返回调用后关闭 Feature、禁用期间不调用 handler、重新启用后恢复、dispatch 状态区分、Tool Result 闭环，以及 Main Agent 与 Registry/SubAgent 独立入口边界。没有调用真实 Provider。

### Decision / Limitation

本阶段只保护 Main Agent 的 `_execute_tool()` 调度路径，没有把 FeatureManager 塞入 ToolRegistry，也没有改造 SubAgent。直接调用 `ToolRegistry.execute()` 和 SubAgent 自己的 Registry 不经过主 Agent 校验，仍是独立入口。CommandPolicy 继续只做命令规则检查，WorkspaceAuthority 继续约束 BaseTools 的文件路径；二者都不是通用 HITL 或 Shell 沙箱。

后续 HITL 可在同一 dispatch 边界返回显式 pending/control 状态，由 `_llm_tool_cycle()` 决定暂停，并保留原 Tool Call 为待处理状态；必须避免把 pending 转成终结 Tool Result。本阶段没有实现该状态，也没有持久化或 Resume。

## 2026-10-10：显式维护主 Agent Run 与工具批次进度

Commit: `a3ef6b3`
Commit Description: `refactor: 显式维护主 Agent Run 与进程内工具批次继续执行`

### Description

主 Agent 在 `_llm_tool_cycle()` 中用局部变量维护模型轮次、无工具重试、当前 Tool Call、批次去重集合、已写回的调用 ID 和延迟的 REPLAN 消息。局部变量随调用栈结束而消失；若运行时需要在工具之间安全暂停，直接再次进入原循环会重置 Runtime Policy 和 Trace，并重新请求模型，不能可靠地从已有响应继续。

本阶段新增进程内 `RunState` 和 `ToolBatchState`。Run 保存稳定的 Run ID、模型轮次预算、require-tool-call 与重试状态，并关联其 Trace task ID；当前批次保存原始有序 Tool Calls、原 Assistant 消息位置、执行游标、结果、重复签名及延迟重规划消息。公开 `run()` 仍表示新任务；内部 `_continue_run()` 只接受已暂停 Run，不追加用户消息、不重置 Todo / Runtime Policy / Trace，也不重新请求 LLM 来生成待处理调用。恢复批次时跳过压缩和工具链清理，按原顺序从游标继续，并在每次 dispatch 时沿用当前 Feature 能力校验。

暂停信号只在调用结果写入后生效。暂停调用本身不被伪造成失败或 NOT_EXECUTED；已完成结果保留，尚未开始的原始调用仍挂在批次上。若最后一个调用刚好完成，Run 仍会停在下一次模型请求之前，继续时才进入新模型轮次。若 handler 已返回明确结果但消息 append 失败，终止闭环会补写该已知结果而不重跑工具；只有 handler 已进入执行阶段却未返回结果时才记为 UNKNOWN。运行中禁止开始新 Run；有活动 Run 时手动压缩和清空历史都会被拒绝，自动压缩在有待处理调用时不运行。HARD_STOP、取消和异常仍终止当前 Run；闭环会将已有和终止反馈写入批次结果，并将不可继续批次的游标收敛到末尾，避免终止状态暴露成可恢复的 pending 调用。

本状态仅属于一个 Agent 实例的内存，不是跨进程 Checkpoint。RunState 的 Run ID 与 TraceManager 独立分配的 Trace task ID 分开保存，并通过 Session Recorder 事件关联；Session Recorder 另有自身 Session ID，只记录生命周期。Todo、消息、Runtime Policy 历史、压缩对象、ShellSession 和后台对象仍由现有模块持有，没有尝试序列化整个 Agent。

### Result / Evidence

Windows Python `3.14.3` 上，RunState 与工具批次定向测试 `28 passed`；扩展的 Runtime、CLI、ToolRegistry、Headless、Provider 输出限制及 Runtime Context 回归为 `121 passed`。测试覆盖三调用批次在 A 后暂停并从 B/C 继续、没有为恢复批次额外请求模型、A 不重放、保持 Todo / 执行预算 / AttemptHistory / Trace、压缩和清空保护、重复签名、运行时重新检查 Feature、最后一个调用后的暂停、独立 Run 的调用身份隔离、Tool Result 写入失败后保留 handler 已返回的真实结果，以及批次闭环和终止状态。未调用真实 Provider。

最终完整 `tests/unit` 为 `310 passed, 12 skipped, 4 failed`。其中 Provider parse-error fixture 和 read_file schema 文本断言在未修改的 main 上也失败；两个 Reference Variant 测试在本 Worktree 受 CRLF 影响，main 上通过。Reference Variant helper 只在系统临时目录复制 fixture 并修改待测实现文件，任务目录中的 oracle 哈希在测试前后保持不变。

### Decision / Limitation

保留单个轻量 RunState 和当前 ToolBatchState，不抽象成工作流框架，也不落数据库。暂停只能由进程内控制信号在工具调用完成边界产生；没有实现审批等待、运行中断恢复、跨进程恢复、崩溃后副作用判定或持久化。本轮仍有控制流和工具后处理局部变量，但它们只服务于当前同步调用；终止异常不会被伪装成可继续状态。未来 SQLite Checkpoint 仍需定义原子保存边界和 UNKNOWN 恢复策略，不能直接将当前 Python 对象视为持久化格式。


## 2026-10-10：限定 ShellSession 持久 cwd 的确认边界

Commit: `123586a`
Commit Description: `fix: 修正 ShellSession cwd 与环境状态语义`

### Description

ShellSession 此前会在启动子进程前扫描复合命令里的 `cd`，并立即修改逻辑 cwd。若 `cd` 位于失败命令之后、条件分支未运行，或执行随后超时、取消，内存状态仍可能保留并未发生的目录变化。ShellSession 也复制了完整进程环境到 `self.env`，但启动 subprocess 时没有使用该副本；真正的子进程继承调用时 Agent 进程的环境。

现在只有严格识别出的独立字面 `cd` 会在确认目标存在且为目录后更新逻辑 cwd。复合命令不再由 Python 侧预扫描 cwd，而是按既有分段观测规则交给实际平台 Shell 执行；子 Shell 的目录不会持久到后续工具调用；需要跨调用保留目录时，应先单独调用 `cd`。传入 `cwd_override` 时，即使命令只有 `cd` 也交给子 Shell 执行，且不会改变会话 cwd。ShellSession 不再持有环境副本，也不承诺跨调用环境变量修改；subprocess 继续继承每次调用时 Agent 进程的环境。

同时显式保留初始化时的 `workspace_root`，使 `reset()` 能真正恢复到配置根目录，而不是把当前 cwd 当作原始根目录。

### Result / Evidence

Windows Python `3.14.3` 上，新增 ShellSession 状态测试及 RuntimeContext、工具观察、分段退出码、Tool Call 闭环和执行能力校验相关回归合计 `113 passed, 12 skipped`。Windows CMD 实测覆盖 `&`、`&&`、`||`、纯 `cd /d`，以及跨调用 cwd、timeout、cancel、`cwd_override` 和子进程环境继承；单独 `&` 的分段退出码回归通过。10 项 POSIX Shell 用例和 2 项 symlink 权限用例跳过。当前机器没有 POSIX Shell；`wsl --list --quiet` 因 `E_ACCESSDENIED` 无法列举发行版，因此未声称 POSIX Shell 实测通过。POSIX 复合命令 instrumentation 的纯函数测试通过。没有调用真实 Provider。

### Decision / Limitation

本阶段为避免猜测而收紧兼容行为：`cd src && command` 仍会在同一次子 Shell 中正常执行，但不会把 `src` 写入跨调用逻辑 cwd；跨调用目录导航使用单独的纯 `cd`。ShellSession 的 cwd 仍不是 Shell 沙箱；它本身不授予 WorkspaceAuthority 权限。未来恢复必须依据当前 Runtime 的授权重新验证目录，不保存或恢复旧权限、进程环境和命令历史。 Windows CMD 的标记扫描现将 && 保持为双字符运算符，同时继续对独立 & 插入分段标记。


## 2026-10-10：第四阶段审查修复六项执行边界问题

Commit: `a3ef6b3`
Commit Description: `refactor: 显式维护主 Agent Run 与进程内工具批次继续执行`

### Description

审查发现，最后一个工具调用完成后暂停时，批次已经没有待执行调用，但延后的重规划提示还没有进入消息历史；继续入口会直接请求模型，丢失提示。因此将“调用执行完成”与“批次收尾完成”分开记录，继续时先完成收尾再请求模型。收尾只消费已经实际追加的提示，写入前后中断都不会重复反馈。

初始化原本位于循环异常处理之外，失败或中断可能遗留 RUNNING。现在新任务初始化失败后进入 FAILED，KeyboardInterrupt 等中断进入 CANCELLED；原异常继续向调用方传播。继续入口在 dispatch 前初始化失败时恢复 PAUSED，保留原调用和预算，不自动重试。保护只覆盖初始化，不将执行异常恢复成可继续状态。

重复调用的异常闭合曾引用上一调用的结果文本，现在每次调用清空结果临时值，跳过反馈和异常补齐共用当前调用的文本。Registry 在 handler 调用前绑定可检查的 Python 签名，用专门的绑定错误区别尚未进入 handler 与 handler 内部 TypeError；后者仍为执行失败，前者的最终结果为 INVALID_ARGUMENTS。无法检查签名的外部 callable 不根据 TypeError 推测参数错误。

Windows Bash 的 start 路由曾绕过 background Feature。后台队列和主 Agent 公共后台 API 现在复用当前能力映射进行启动前检查，保留正常返回形式；原始 BackgroundProcessor 仍是独立底层入口，没有获得 Main Agent 的策略所有权。POSIX cd 快捷路径对未引用 # 保守退回真实 Shell，不推断子 Shell 的持久 cwd；引用或转义的 # 和普通路径仍可按独立字面 cd 处理。

### Result / Evidence

先在未修复的第四阶段实现上复现六类问题，再修改生产代码。Windows Python 3.14.3 新增 55 个参数化回归用例，验证最后调用后暂停与重规划、收尾写入前后中断、初始化各入口、消息写入前后错误与取消、重复调用结果关联、参数绑定与 handler 内部错误、后台启动能力及 POSIX 注释/引用。扩展定向回归为 206 passed、12 skipped，包含 LoopController integration；最终完整 tests/unit 为 365 passed、12 skipped、4 failed。没有真实模型调用。

四项失败逐一复核：Provider parse-error 测试的配置 fixture 缺少 provider 等字段，main 上也在初始化失败；read_file schema 文本断言在 main 上同样失败，真实 schema 仍有 start_line/end_line 参数和范围说明，未修改生产接口或断言。两个 Reference Variant 在 main 上通过，本 Worktree 的 oracle 为 CRLF，而提交文件及预期哈希对应 LF；仅归一化换行后内容一致。测试只复制 fixture 到系统临时目录并修改待测实现，测试前后 oracle 字节哈希保持不变，没有修改参考答案。

4 项真实 Shell 对照在隔离目录使用 Windows Git Bash 通过，覆盖注释、未引用 #、引用 # 与普通路径；这不代表 Linux 全套验证已完成。其余平台相关跳过仍需 Linux/CI 环境补齐。

### Decision / Limitation

保留原 RunState、ToolBatchState 和同进程继续执行设计，只增加批次收尾标记及必要的小型辅助入口，没有持久化、审批或新的状态机。测试覆盖明确的同步故障注入边界，不证明任意字节码位置的异步中断、内存耗尽、持续日志/消息写入故障或跨进程恢复具备事务一致性；这些不能直接从当前测试通过推导出来。完整单测仍非全绿，四项既有/检出环境问题保持可见，未扩大到 Evaluation 或 schema 清理。


### 提交后的独立最终审查（2026-10-10）

第四阶段已在功能分支提交为 `a3ef6b3`，此前未跟踪的 RunState 和状态测试文件均在该提交中作为新增文件入库。对实际提交补丁的空白检查通过。

此次重新检查批次收尾标记的提交顺序、初始化中断治理及 Tool Result / Call ID 关联，没有再次扩大到全仓扫描。收尾标记只在待写反馈耗尽后设置，最后调用后的继续入口先完成收尾再请求模型；初始化中断原样传播，新 Run 终结，尚未 dispatch 的继续入口保留暂停状态；每次调用的结果临时值独立，异常闭合保留已写结果而不重放工具。

对已提交代码重新运行定向回归为 163 passed、2 skipped。另运行 7 项不写源码的独立内存故障注入：Tool Result 已写入后的游标推进前/后普通异常及 KeyboardInterrupt、下一模型请求前 finalized 与 REPLAN 写回检查，以及新 Run/继续入口的 SystemExit 原对象传播，均通过。未发现此次限定范围内的阻塞项；这不代表任意异步中断或持续写入故障具有事务保证。

决定冻结第四阶段功能实现，暂缓合并 main，不推送。此前完整单测的四项基线/检出环境失败继续保留，跨平台验证可以后续独立处理，不混入 RunState 功能提交。Checkpoint 持久化和 HITL 审批留待后续获准的阶段。


## 2026-10-10：暂停 Run 的 JSON 快照与新实例继续执行

Commit: `f6ff2571fa2a8f0d826a7a83a1a8ea6b21bfd8a1`
Commit Description: `feat: 增加版本化 Run 快照与跨实例恢复`

### Description

第四阶段能在工具调用之间暂停，但继续执行仍依赖同一 Agent 内的消息、Todo、Note、治理历史和 Trace。本阶段在已经整合四阶段的 main `a3cbd53` 上建立显式 JSON 数据契约；没有增加另一套运行状态模型，也没有数据库或自动崩溃恢复。

入口为 `export_run_snapshot()`、`restore_run_snapshot(snapshot)` 和已有 `_continue_run()`。导出只接受 PAUSED Run：当前批次的下一调用尚未 dispatch，或全部调用完成、下一模型请求尚未开始。恢复只安装暂停状态，不执行工具或请求模型；随后由调用方显式继续。仍然禁止在未完成 Run 上开始新任务或覆盖状态。

Schema 版本为 1，顶层字段如下：

| 字段 | 内容与所有权 |
| --- | --- |
| schema_version | 格式版本；当前无自动迁移 |
| workspace | 规范绝对 root、cwd，以及 WorkspaceStateGuard 可观察范围的内容摘要 |
| compatibility | 当前工具 schema / handler 源码标识、相关 Runtime 源码摘要、平台、Python 主次版本、非凭据 LLM 配置、压缩阈值与治理窗口 |
| run | 现有 RunState；包含原始 ToolBatchState、模型预算、重试、原调用 ID / 参数、游标、结果、去重集合、延后的 REPLAN 与 finalized |
| messages | Message 原字段；消息结果和批次结果必须一致 |
| history | AttemptHistory 的有限窗口、所有事件字段和下一个序号，保持原精度 |
| todo / agent_note | 有序 Todo 条目及有效 Note 文本；不保存旧 Note 文件路径 |
| memory | rebuild 策略；不传递 Recent Files、观察或指纹缓存 |
| context | 原任务文本、压缩提醒标志、最后 Assistant Note |
| diagnostics | 原 Session / Trace ID 和累计用量；不传递活 Trace、客户端或锁 |

恢复先复制并验证全部 JSON 数据，再准备新的 Trace 片段和 Note 文件，最后建立 Agent 状态。校验失败不会覆盖接收实例的消息、Todo、Note、治理历史或 Trace；准备阶段 IO 失败和中断也保留原状态。恢复的 history 被绑定到当前唯一 RuntimePolicy，LoopController、observer 和 completion gate 继续使用这同一个 policy，不重置旧历史。

原始 Assistant 与批次必须逐项匹配；结果必须恰好对应游标前缀，内容与消息一致，结果连续且按原调用顺序。禁止缺结果、重复 / 孤立结果、错误 ID、额外模型批次以及 UNKNOWN 等终止闭合状态伪装成可恢复暂停。恢复不猜测结果、不调用清理器修补数据。最后调用后暂停仍先消费延后的 REPLAN，再请求下一轮模型；pending 批次沿用第四阶段的压缩和清理隔离。

初版检查发现，仅匹配 workspace 路径无法发现导出后用户修改文件，旧的待执行覆盖写入仍可能覆盖新内容。最终复用 WorkspaceStateGuard 的现有文件观察生成摘要：可观察内容变化时拒绝导入。导入后、首次继续前再校验该摘要；一次性前置条件保存在 RunState 的 `workspace_checkpoint_digest` 中，初始化失败不会消费它，首个调用完成才清除。已执行工具自身的写入不会与旧摘要反复比较；再次暂停并导出时生成新的摘要。这不是文件系统锁或并发事务。

cwd 在恢复及每次继续前按当前 WorkspaceAuthority 校验其授权和目录存在性。旧 additional_roots 不被继承；当前实例明确授权的根仍可使用。Feature 在实际 dispatch 时重新判断；前台 Bash 保留 BaseTools 的 CommandPolicy，后台路由保留 Agent 的独立 CommandPolicy。快照不传递旧授权、完整环境、Shell 进程、command_history 或后台任务表。有后台任务表的实例不支持本契约的导出 / 导入；没有实现子 Agent 执行栈恢复。

新 Trace 是新的执行片段，request_config 关联同一 run_id 和原 trace_task_id；有 pending 调用时建立原模型轮次的部分 turn，记录 B/C，不增加模型请求或预算。SessionRecorder 使用新 Session ID，通过 run_snapshot_restored 关联原 Session / Trace。Todo 和 Note 内容保留；文件观察缓存清空，所以后续模型少了 Recent Files / 观察摘要，需要重新读取，历史 Tool Result 仍保留为历史消息。Compressor 沿用新实例，压缩提醒保留；旧归档文件、长输出文件和缓存不被搬运。

### Result / Evidence

新增测试使用真实 MiniClaudeAgent 构造、独立 Runtime 组件、Mock Provider / handler 和临时工作区。测试先确认旧实现没有快照入口，再覆盖 JSON round-trip、A 后暂停的新实例 B/C 执行、结果顺序和唯一性、模型请求次数、Todo / Note / 预算 / 重试 / 有界治理历史、准备失败、当前能力 / 命令 / 路径拒绝、文件内容与 cwd 变化、取消 UNKNOWN、批次收尾、重复导入 / 继续，以及合法写入后的再次跨实例恢复。

Windows Python 3.14.3 上，快照专项为 `54 passed`；扩展定向回归（含 LoopController integration）为 `260 passed, 12 skipped`；最终完整 `tests/unit` 为 `419 passed, 12 skipped, 4 failed`。54 项新增测试全部通过，12 项跳过为既有平台相关用例，未运行真实 Provider 或 Terminal-Bench。

未修改 main 上的同四项复核为 `2 failed, 2 passed`：Provider parse-error fixture 缺少 provider 等初始化字段，read_file description 不含断言所需原文，二者在 main 同样失败，未将其简单认定为失效断言。Node / Java Reference Variant 在 main 通过，新 Worktree 的 oracle 分别含 7 / 13 个 CRLF，归一化 LF 后与 HEAD 字节完全一致；测试只在 TemporaryDirectory 的副本修改实现。测试前后原 oracle 哈希不变，未修改 oracle、harness 或有效断言。

### Decision / Limitation

只支持受控暂停数据的移交，调用方必须安排一个有效继续者：导出不会撤销旧实例，同一快照复制给多个全新实例仍可能重复副作用。实例内用已见 Run ID 拒绝重复安装或回退；完成 / 终止 Run 的继续入口仍不会重跑调用。这不是全局去重、租约或任意历史回滚。

源码和配置匹配采用保守拒绝策略，包括注释 / 格式等源码变化；不可检查源码的 handler 不能导出快照。指纹检查覆盖当前列明的模块和 handler，不证明动态 monkeypatch、闭包配置、插件依赖或所有外部工具版本相同。Workspace 摘要仅覆盖现有 guard 可观察的主根文件；忽略目录、不可读文件、额外根、远程服务和 Shell 外部资源不因此被锁定或证明不变。首次继续检查与实际文件操作之间仍有外部修改竞态。Shell cwd 也不是 Shell 沙箱。

SQLite 阶段可以复用版本契约、验证器和安全边界；仍需原子保存 / 事务、唯一继续者、工具特定前置条件、外部产物归属、审批状态和副作用 UNKNOWN 的人工处理协议。本阶段未处理进程在 handler 内退出、工具成功但结果未保存、任意字节码位置中断或数据库加载。快照内容包含任务消息 / Note / 结果，未做加密或真实性签名；格式验证不证明其中历史事实真实。


## 2026-10-11：SQLite Checkpoint 与保守故障恢复

Commit: `d892ed3d27c3269bc6843d959634a0e4b11c80d2`
Commit Description: `feat: 引入 SQLite Checkpoint 与保守故障恢复`

### Description

第五阶段的 JSON 快照能把安全暂停状态交给新实例，但它无法确认退出前工具有没有执行，也没有阻止多个实例继续同一副作用。本阶段先单独建立存储层与故障协议，通过测试后再在既有 Agent Loop 插入提交边界；没有替换 RunState、Provider、工具注册、循环治理或压缩算法。

Snapshot 仍是经过验证的逻辑状态；Checkpoint 是这个状态的可靠数据库提交；Execution Journal 是每个原始调用是否进入 handler、结果是否保存的事实。数据库 schema v1 只有 runs / calls 两张表。runs 保留 Run ID、版本号、状态、当前投影、工具日志摘要和最终响应；calls 按模型轮次与调用位置保留 Provider ID、原调用 JSON、执行阶段和结果。SQLite 使用 WAL、synchronous=FULL、参数化 SQL、逐操作关闭连接；没有 pickle、Provider API Key、完整环境或活运行对象。

提交边界为 tools、before_model 和 terminal。完整模型批次先作为 PREPARED 与一致投影提交；真正 handler 前提交 RUNNING；handler 返回后，将真实结果、消息、游标及必要状态同一事务保存，然后才允许下一项。去重、无效参数或执行能力拒绝保存为 NOT_EXECUTED，并保留原具体 outcome。确定的 handler 失败是 COMPLETED 中的失败事实。批次收尾另行保存 finalized 和延后 REPLAN，恢复最后一个调用后的暂停时仍先收尾再请求模型。模型请求前保存闭合消息的边界，允许在尚无工具批次时恢复。未经提交的模型响应不是权威事实，退出后可能重新请求模型，不能保证复现那次响应。

工具日志和投影分开不是放宽快照规则：安全可继续投影仍只有已完成结果前缀及未执行后缀，RUNNING 仅在日志中。新增显式终结验证模式保存 HARD_STOP、取消及异常闭合的原结果；不把 UNKNOWN 伪装为可继续暂停。普通独立 JSON 仍用 v1；持久化投影使用 v2 并绑定规范数据库路径及 Run ID，只能由持有 Run 锁、核对当前数据库投影的恢复入口加载。此前普通 JSON 导入可以绕过 Store 的独占，这个反例已通过回归测试封闭。

同一数据库、同一 Run 使用永久文件上的内核锁（Windows 字节锁 / POSIX flock）和数据库版本 CAS。锁覆盖完整执行 / 恢复，暂停返回后释放；另一实例已经推进后，旧内存版本不能继续。锁无超时租约，进程退出由内核释放，不删除锁文件。父进程死亡与外部子进程停止是两回事；恢复不凭锁释放就认为未保存的 Shell 副作用可以重试。

Checkpoint 写入失败立即隔离当前实例，禁止继续工具、普通错误闭合清理和新任务。COMMIT 异常通过新连接核对本事务操作标识，区分已提交、未提交与无法核验；三种结果都不在故障实例中继续。新实例持锁读取最新版本，遗留 RUNNING 改为 UNKNOWN 并拒绝自动执行；PREPARED 可以在当前环境 / 授权 / 能力重新校验后继续；已经提交的结果不会再次 dispatch。结果副作用已发生但尚未保存时，SQLite 无法与文件 / Shell / 外部服务统一事务，只能要求人工核验，不承诺通用 exactly-once。

### Result / Evidence

实现顺序是独立 Store / 故障测试先行，再接入 Runtime。回归实际暴露并修复了：初始化身份提交失败后内存残留 RUNNING、初始化中断未写终结状态、WAL 下分开读取 Run / Journal 的版本错配、历史日志损坏未被检测，以及持久化投影绕过锁走普通 JSON 入口。Store 加载现在使用同一读事务，日志整体和投影都有一致版本的校验摘要；摘要用于发现损坏，不证明数据来源真实性。

Windows Python 3.14.3 最终完整 tests/unit 为 477 passed、12 skipped、4 failed；其中新增 Store 22 项与 Runtime 36 项全部通过。非进程故障定向集（含既有 Snapshot / RunState / 消息闭环）164 passed、10 deselected；LoopController integration 35 passed。12 项跳过均为既有平台相关测试；未进行真实 Provider 或 Terminal-Bench。新增测试包含独立真实 Python 进程的退出 / 重启、真实文件副作用计数和活 handler 竞争；不以同一进程的两个对象代替进程恢复。故障位置包括模型请求前、响应未提交、PREPARED 后、RUNNING 后 handler 前、结果事务提交前 / 后、终结事务后；另有提交成功后抛 IO 异常、核验连接不可用、数据损坏、源码 / Workspace 改变、能力变化及重复恢复测试。Trace 和 SessionRecorder 建立新的诊断片段并关联原 Run，不作为恢复权威。

原有四项完整单测失败继续记录：未修改 main 的同四项复核为 2 failed / 2 passed；Provider fixture 与 read_file 描述字串断言同样失败，不修改真实接口断言。Node / Java Reference Variant 的 Worktree oracle 分别含 7 / 13 个 CRLF，归一化 LF 后匹配 HEAD，main 对应测试通过。测试只在临时目录修改实现，原 oracle 的测试前后哈希保持不变，未修改评测数据或 Harness。

### Decision / Limitation

采用显式 opt-in：默认 CLI / Mock / Benchmark 路径不打开数据库。该模式的主 Agent 前台工具受 journal 保护；后台任务和子 Agent 明确拒绝，不宣称能恢复它们的执行栈。直接使用 BaseTools、Registry 或手动外部执行不在此契约内；独立 v1 JSON 快照仍由调用方保证唯一继续者，不能把复制数据库或手动改写归属当作合法恢复。

每次真正 dispatch 至少增加 RUNNING 和结果两次事务，另有批次准备、收尾、模型前与 Run 终结提交。投影、工具日志摘要、源码 / 工作区摘要有随历史增长的成本；没有运行性能 Benchmark，不量化吞吐影响。未启用模式只增加轻量空 Store 判断，没有数据库 IO。

恢复沿用现有源码 / 配置兼容检查、WorkspaceStateGuard 摘要、cwd 存在及当前路径授权检查，实际 dispatch 重查当前 Feature / CommandPolicy。它不扩大旧权限，也不是文件系统锁或 Shell 沙箱。工作区摘要观察范围、外部根 / 资源、动态依赖与执行前的外部修改竞态仍有第五阶段的限制。运行数据可能包含任务文本 / Note / 输出；未实现加密、真实性签名、数据库迁移或自动清理。

POSIX flock 和真实 Linux shell 尚待 Linux 环境验证，Windows 核心故障协议已用真实子进程测试。未来 HITL 可以复用 PREPARED 安全边界、Run 锁、版本 CAS 与投影原子提交，但审批身份 / 授权有效期 / ASK 状态尚未设计。UNKNOWN 不提供自动清除、补偿或重试接口；本阶段也不支持任意历史回滚、分布式执行或跨机器共享数据库。


## 2026-10-11：第七阶段 A：权限决策与持久化审批契约

Commit: `2518787fe6b4531ab8055774e3ae65373186700b`
Commit Description: `feat: 建立 HITL 权限决策与持久化审批契约`

### Description

第六阶段确认了工具是否已经开始执行，却没有表达“此操作是否需要人的决定”。本阶段只增加数据契约和独立 Store API，没有让 Agent Loop 等待审批、调用新策略或执行获批工具，也没有 CLI approve/reject。

PermissionDecision 表达 ALLOW / DENY / ASK，并包含完整操作绑定、策略版本、规则 ID 与可展示原因。ALLOW 不是执行成功，DENY 不是 handler 失败，ASK 不是 UNKNOWN 或 NOT_EXECUTED；这些对象不会写 Tool Result 或进入 AttemptHistory。未知风险保守采用 ASK。当前 Feature、注册状态、参数绑定、路径授权、CommandPolicy 硬拒绝优先，审批不能扩充这些能力。Windows Bash start 的后台能力也先检查。

默认策略只自动允许核实过的内置 handler 身份：BaseTools 的 read_file、list_files、search_code、count_occurrences，以及主 Agent 对应包装和 get_background_status。同名工具被替换为自定义 handler 后不因此 ALLOW。搜索通配符降为 ASK，真正执行时仍须按 BaseTools 原规则逐项校验目标。write_file/edit_file、Shell、后台启动、网络 health_check、停止进程、Todo、Agent Note、skills 和未知风险的已注册工具采用 ASK：其中 Todo/Note 修改 Runtime 状态，health_check 有外部请求，不能凭工具名当成纯查询。日志格式化可能写诊断文件，本契约的只读指向工作区 / 外部业务操作，不承诺进程完全没有诊断 IO。

ToolOperation 使用规范 JSON 保存原始 Provider 调用，完整参数可独立取得规范摘要；批次身份由 run_id / turn 派生，内部调用身份由 run_id / turn / ordinal / Provider ID 派生。绑定还包括规范 workspace / 有效 cwd、Workspace 摘要、工具 schema / 实现指纹、有效能力 / 当前授权根 / CommandPolicy 摘要、Runtime 兼容摘要和策略版本。MainAgent 策略工厂每次评估重新观察当前 Workspace / Runtime 摘要，通用独立策略的调用方负责提供新鲜观察；不持久化旧授权根作为新的授权，也不复制环境或凭据。

SQLite user_version 从 1 升级为 2，新增 approvals 表；runs / calls 的字段和既有记录不改写。初始化持 BEGIN IMMEDIATE，版本检查、建表和版本更新同一事务；失败回滚，没有不受控的 executescript 隐式提交。升级后的旧记录可以按原 Journal 协议加载与继续；Schema 升级不解除既有 Snapshot 的源码 / 配置兼容门槛，旧程序生成的 Run 如果源码摘要不兼容仍会被 Agent 拒绝恢复，不能伪造历史摘要来放行。

审批表保存 approval_id、run_id / turn / ordinal、完整权限决策 JSON、绑定摘要、状态、创建 / 到期时间、用户决定 / 决策者 / 决定时间、消费时间与失效原因。外键直接关联 calls 的主键，唯一约束使一个具体调用只有一个审批记录；Provider ID、工具、参数和上下文保存在决策 JSON 中并逐项核对。审批不是第二套执行日志，创建或批准都保持 calls 为 PREPARED。

合法转换为 PENDING→APPROVED / REJECTED / INVALIDATED，APPROVED→CONSUMED / INVALIDATED；REJECTED / INVALIDATED / CONSUMED 不重新激活。相同请求返回原记录，同一用户的同一决定保留原时间，冲突用户或相反决定拒绝；重复操作不推进 Run 版本，真实审批变更推进版本以隔离旧内存继续者。过期与上下文改变时提交 INVALIDATED 后再向调用方报错；失效记录要求重新规划为新调用，本阶段不循环复用审批 ID。

ApprovalStore 使用既有 Checkpoint Store 的连接、Run 内核锁、事务、版本与提交异常核验，不引入另一个数据库服务。consume_and_mark_running() 是事务契约测试入口：持锁检查当前策略、绑定、未消费批准、当前 PREPARED 位置与 Run 版本，同一事务写 CONSUMED 和 RUNNING；不调用 handler、推进游标或伪造结果。存在审批记录的调用不能通过普通 mark_running() 绕过消费。消费事务后退出仍按第六阶段规则恢复为 UNKNOWN，不拿原批准重试。

### Result / Evidence

Windows Python 3.14.3 上新增权限 25 项、审批 41 项，合计 66 项全部通过。权限 / 审批 / Store 定向集为 87 passed、1 deselected；最终完整 tests/unit 为 543 passed、12 skipped、4 failed（包含 Checkpoint 的独立进程测试与 Snapshot 回归）；LoopController integration 35 passed。测试覆盖三类决策与真实 handler 分类、硬拒绝不可伪装为 ASK、JSON / SQLite round-trip、重复请求 / 决定、冲突决定、错 Run / 调用 / 参数 / Workspace、过期 / 失效 / 消费、双 Store 竞争、请求 / 决策 / 消费事务失败及提交后异常、v1 迁移保持旧投影和 Journal，以及迁移回滚。审批测试不执行 handler；既有 Checkpoint 真实子进程回归另行验证执行协议未破坏。

本轮在未修改 main `c97f917` 上复核同四项，得到 2 failed / 2 passed。Provider fixture 缺 provider 初始化字段、read_file 描述原文字串断言仍在 main 失败；Node / Java Reference Variant 在 main 通过，在本 Worktree 因 CRLF oracle 哈希差异失败。原 oracle 的 SHA256 与第六阶段记录一致，未修改评测数据或有效断言。12 项跳过沿用平台限制；没有付费 Provider 或 Terminal-Bench，Linux 实测仍未进行。

### Decision / Limitation

7B 的准确接入点在 _execute_tool() 已确认注册 / 当前 Feature 后、_checkpoint_dispatch() 提交 RUNNING 和 Registry.execute() 调用 handler 前。ASK 的持久化等待必须保持当前调用 PREPARED，不生成终结 Tool Result、不消耗批次游标、不计失败；需要由下一阶段增加明确暂停 / 审批恢复协调。获批继续时要重新绑定当前上下文并原子消费，不能先标 RUNNING 再请求批准。

目前全部 Agent、SubAgent、公开 run_background/run_subagent、直接 Registry / BaseTools 入口仍未使用新权限策略；第六阶段持久化模式原有后台 / SubAgent 拒绝继续有效。没有通知、用户身份认证、审批 UI、自动 Resume、RBAC、Shell AST / 沙箱或 UNKNOWN 补偿。actor 是调用方提供的审计身份，不是认证机制；数据库按受信任本地存储处理，JSON 验证 / 摘要不是真实性签名。Policy 检查也不是文件系统事务，外部修改竞态及 Workspace 摘要观察范围沿用既有限制。审批参数可能包含敏感内容，Store 不把它们额外写入诊断日志。


## 2026-10-11：第七阶段 B：主 Agent 的持久化审批等待与原批次继续

Commit: `f069085c9f072f8fd97511e05eb6b41f33e6aa29`
Commit Description: `feat: 接入持久化 HITL 暂停与审批后工具继续`

### Description

7A 能保存用户决定，但 Agent 尚未使用它；如果在普通异常路径中模拟 ASK，会把未执行调用写成 UNKNOWN 或错误结果。本阶段在主 Agent 的既有 dispatch 前增加显式控制返回，并复用原工具批次循环，没有另建执行器。HITL 默认关闭，启用前必须有 SQLite Checkpoint；注册 / 参数 / Feature / 路径授权 / CommandPolicy 等硬性规则不会因审批而放宽。

ALLOW 先按原协议提交 RUNNING，再调用 handler；DENY 写原 ID 的未执行结果，跳过实际工具失败分析和治理观察；ASK 返回 WAITING_APPROVAL，不写 Tool Result、不推进游标、不记录失败。等待 Run 保存 approval_id，批次仍以原有 PAUSED 表达当前位置，原参数和 Provider ID 保留。去重集合只代表已处理前缀，遇到 ASK 时移除尚未处理的当前签名，避免获批继续时被错误去重。

ApprovalStore.request_wait() 持既有 Run 锁，将创建审批与保存等待投影放在同一 BEGIN IMMEDIATE / COMMIT 中。创建和保存复用既有校验及事务实现，使用内部连接传递避免嵌套提交；提交后才发布内存等待状态。等人期间不持锁、不阻塞线程。请求、消费或结果提交失败均停止推进，提交后异常仍按原 Store 的独立连接核验规则处理，不能把持久化失败转换为工具反馈。

现有 SQLite runs 表的 CHECK 限制不允许新状态和边界，因此将数据库升级为 v3。迁移在事务中重建父表的 CHECK，显式按列复制 Run 原值，calls / approvals 不改写；临时关闭该迁移连接的外键检查，提交前执行 foreign_key_check，失败整体回滚。v1 顺序增加审批表再升级，v2 直接扩展等待状态。初始化验证版本和字段，不强行接收未来版本。数据库迁移不改写快照源码指纹，不保证旧实现生成的 Run 能在新源码下执行。

HITL 快照使用绑定数据库的 v3 投影，等待 Run 只能在 approval 边界校验；旧 v1/v2 缺少 approval_id 时解释为无审批。普通 JSON 导入不能接受 WAITING，绑定投影必须匹配数据库已提交内容并持执行锁。Store 在同一个 WAL 读快照中读取 Run、calls、审批，核对原调用、当前位置、Workspace 和 Runtime 绑定，以及等待 / 消费与 PREPARED / RUNNING / UNKNOWN 的对应关系。

Runtime 查询 API 只展示等待记录；决定 API 先校验 Run 与审批 ID，再持锁调用原 Store 决定接口，不执行 handler。恢复 PENDING 返回等待信息，新实例同时建立 WAITING 生命周期，防止后续普通输入覆盖它。APPROVED 恢复重新校验 Snapshot 与当前执行上下文，在既有 _checkpoint_dispatch() 调用 consume_and_mark_running()，同一事务 CONSUMED + RUNNING；之后不再次调用普通 mark_running()。原 Handler 及结果 / messages / 游标提交沿原循环继续。

REJECTED 不运行当前调用和剩余依赖调用；为它们按原 ID 写连续 NOT_EXECUTED 说明，用已知阻断结果保存闭合批次，再在 Tool Results 后写重规划反馈。用户拒绝不进入真实执行失败统计。关闭投影提交后即使退出，下一实例也能完成剩余收尾，不会重新执行工具。每次恢复只继续原批次，下一次模型请求发生在完整闭合之后。

审批消费后、handler 前退出和副作用发生后结果未提交，都按第六阶段恢复为 UNKNOWN；批准不能重放 UNKNOWN。结果已经提交的工具不重复执行。审批等待返回后的诊断错误 / 中断保留等待事实并传播信号，真实策略异常不伪装成参数错误或 UNKNOWN。HITL 模式的参数必须先成为有限 JSON 对象，避免非对象进入治理观察器或 NaN 污染投影；默认关闭的原模式保持行为。

### Result / Evidence

Windows Python 3.14.3 最终冻结代码的完整 tests/unit 为 601 passed、12 skipped、4 failed；新增 HITL 58 项全部通过，其中 52 项非进程测试、6 项真正独立 Python 进程测试。LoopController integration 35 passed。确定性覆盖实际 ALLOW / DENY / ASK、前缀保存、连续审批、批准 / 拒绝、去重、参数形态、Todo / Note / cwd / 预算、手动 / 自动压缩保护、审批身份损坏、上下文失效、请求 / 消费 / 结果事务提交前后故障、迁移及回滚、正常关闭模式回归，以及真实进程的等待 / 决策 / 原批次继续、活 handler 竞争与死亡。没有付费 Provider、危险 Shell 或 Terminal-Bench；12 项跳过沿用平台限制，Linux 实测尚未进行。

首轮完整测试期间源码仍在修改，有一项真实进程恢复被源码指纹检查拒绝，该次结果不作为最终验收；冻结生产代码和测试后重新完整运行，最终只剩四项历史失败。审批后的 cwd / 参数 / Feature 等校验，以及等待返回后诊断错误和中断的传播均有回归；测试通过不表示可以自动恢复 UNKNOWN 副作用。

提交前再次运行 HITL Runtime、审批、权限、Checkpoint Store / Runtime、Snapshot、RunState、工具消息闭环与 LoopController integration 定向集：333 passed。显式设置 PYTHONPATH=src；首次未设置时仅出现模块导入收集错误，不作为代码回归。完整单测未在本次整合操作重跑，上述 601 passed、12 skipped、4 failed 为此前冻结代码的验收记录。

本轮未修改 main `899fa3c` 上同四项历史失败复核为 2 failed、2 passed：Provider fixture 缺 provider 字段、read_file 描述原文字串断言仍失败；Node / Java Reference Variant 在 main 通过，本 Worktree 保留 CRLF oracle 字节哈希差异。原 oracle SHA256 与 7A 一致，不修改 oracle 或删除有效断言。

### Decision / Limitation

仅主 Agent 的持久化批次调度受此 opt-in HITL 保护，SubAgent、直接 Registry / BaseTools 和公共后台 API 不接入；原持久化模式对后台和 SubAgent 的限制仍有效。actor 是调用方审计身份，不是认证；本地 SQLite 视为受信任数据，未增加签名、加密、TTL 默认策略或自动清理。工作区摘要和执行前外部修改的竞态沿用已有边界，不承诺 Shell 沙箱或文件系统事务。

审批恢复不兼容或策略 / Feature / 授权变化时 fail-closed；过期或上下文变化的旧批准失效，不自动换发新批准。INVALIDATED 暂无用户取消 / 重规划 API；UNKNOWN 没有补偿或重试接口。本阶段不提供最终 CLI 操作。7C 只需展示 Run / Approval 的绑定及原因、调用查询和决定接口、再显式恢复；不允许 CLI 直接操作 SQLite 状态或 dispatch handler。

## 2026-10-11：标准确认策略、独立授权规则和安全归档

Commit: `PENDING`
Commit Description: `feat: 整理会话恢复、审批交互与可撤销授权规则`

### Description

默认开启 HITL 后，TodoWrite 和工作笔记也逐项 ASK，Shell 查询与构建没有区分；持久化模式还会把实际不支持恢复的后台/SubAgent 工具提供给模型。现在在既有权限策略中核实内置 handler 身份：计划与笔记只修改 Runtime 内部状态，可以自动允许；只读文件查询保持 ALLOW，常见凭据文件单次 ASK；仅少量不带展开或复合语法的 Shell 内置查询允许，测试、构建和未知风险保持 ASK。持久化模式的能力约束同时用于 schema 过滤和实际执行阻断，不改 FeatureManager 或 CommandPolicy 的职责。

策略版本改为 main-standard-v2。旧 main-conservative-v1 记录允许读取用于检查和归档，当前绑定始终生成新版本，evaluate 不会将旧绑定解释为可执行批准。默认策略变化同时受 Runtime 源码指纹约束，没有降低原恢复兼容性检查。

可撤销规则是明确用户意图，不是第二份执行事实：进程规则在内存，工作区规则在原 SQLite 新表；指纹包含工具、完整参数、cwd、工作区、工具实现与有效策略，不绑定新 Run/Call ID 或工作区内容摘要。后续同一操作可以不再弹 ASK，但每次重新生成当前绑定并先过硬拒绝，原批准仍按原协议消费一次。规则的原操作与指纹一致性重新校验，损坏或数据库读取失败不会允许 dispatch。任何依赖当前文件内容的工具前置条件仍由 handler/恢复校验负责，本次不承诺工作区级规则具有文件系统隔离能力。

SQLite v4 在同一升级事务增加 conversations、conversation_runs、run_archives、permission_grants、approval_feedback，保留 runs/calls/approvals 原记录。创建下一 Run 持 Conversation 与 Run 两个内核锁并核对 head；正常等待、完成和异常返回释放锁。拒绝意见与用户决定同事务写入，stop 与 replan 分别控制本轮终结或工具结果闭合后的模型请求。归档保留执行事实，只阻断原操作、解除当前会话选择，UNKNOWN 永远不会因归档变成成功或未执行。

### Result / Evidence

最终验收结果见 CLI 演进主线本日期的记录。迁移测试模拟真实 v1/v2/v3 旧结构，比较升级前后 Run 与 journal；会话与规则测试检查跨实例、旧 head 冲突、执行次数、原 Call ID 唯一性、规则精确参数匹配、撤销、禁用 Feature、旧策略和归档。原审批原子消费及崩溃恢复测试继续保留，不修改 oracle。

### Decision / Limitation

授权规则可跨新 Run 使用，授权范围明确；这并不保证工具幂等或 exactly-once，执行日志继续负责原调用的一次 dispatch 和未知状态。归档是人工处理记录，不是 UNKNOWN 的补偿/核销。源码整体摘要、严格工作区摘要暂不放宽；审批过期、上下文失效不能通过规则恢复原批准。更多 Shell 查询应在可验证隔离条件下独立扩展，不能单靠字符串黑名单扩大 ALLOW。


## 2026-10-11：将可复用授权与一次性操作绑定分开

Commit: `PENDING`
Commit Description: `feat: 按文件与对话保存授权并整理终端交互`

### Description

前一阶段保存完整参数，使 edit_file 的下一次局部编辑无法复用授权；将这种规则叫做“会话允许”会误导用户。现在内置 edit_file / write_file 的可复用规则绑定同一个规范文件路径，不绑定替换文本；保留当前实现、有效 Feature、策略版本与主工作区绑定。路径复用统一使用实际文件工具的 Workspace PathResolver，不能随 Shell cwd 改变文件解释。当前 ToolOperation / ApprovalRecord 仍精确绑定原 Call ID、原参数和执行上下文。

Conversation 范围在原 permission_grants 表的版本化 JSON 中保存，匹配前从原 Run 查询 Conversation 身份，另一个对话不能借用；Workspace 范围没有 Conversation 限定。两种范围都重新检查规范路径与当前两个文件 handler 的实现契约，未知替换 handler、敏感文件、.git 或越界目标不提供文件范围授权。Shell 与其他操作保持精确参数、cwd 与平台绑定，不维护第二套命令解析器。旧 v4 全操作规则不改变语义，损坏数据或数据库读取错误 fail-closed。

为了让审批 diff 与实际执行一致，把 edit_file 原有的全量预校验和逆序 offset 应用抽成纯函数，保留原有 2000 字符限制、唯一性及 approx_line_start 窗口规则。执行继续原子替换文件，预览只读取目标文件。共享函数同时进入 Runtime 源码兼容摘要，改变辅助实现不能留下可继续使用的旧授权。文件读写显式禁用通用换行转换，修复原来声明保留 CRLF、实际读取却先转换为 LF 的差异。

### Result / Evidence

测试与交互结果以 CLI 演进主线本日期的“文件授权范围与终端信息层次”记录为准。回归不仅检查提示文字，还验证真实临时文件内容、Call ID 唯一性、calls Journal 完成前缀、模型请求次数、同一 Conversation 重启、新 Conversation 隔离、当前 Feature 与替换 handler。原 SQLite 锁、审批消费、异常与 UNKNOWN 协议不变。

### Decision / Limitation

文件规则授权的是对选定文件的后续编辑/覆盖，不承诺代码内容安全，也不授予 Shell 或新路径授权。规则与执行日志不是同一个事实来源；旧批准不能因有资源规则而复活。整体源码和 Workspace 摘要仍是恢复门槛。更宽 Shell 查询、命令前缀、操作级前置条件和沙箱应作为独立任务；本轮没有扩大默认 Shell ALLOW。
