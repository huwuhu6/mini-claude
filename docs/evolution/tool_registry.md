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
