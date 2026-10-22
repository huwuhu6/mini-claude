# CLI Command 边界演进

## 2026-10-12：取消会话仍可检查已保存历史

Commit: `40638955aca983bd840313c626d94616333f5e72`
Commit Description: `feat: 完善本地会话恢复、授权交互与终端展示`

### Description

取消 Bash 后重新进入会话，界面显示 CANCELLED、消息数 0，却不能发送新任务。数据库仍有完整消息与 UNKNOWN 记录；恢复入口只返回取消文本，造成历史似乎丢失、安全阻断又无法解释。

现在终结 Run 可以加载经过校验的消息与 Agent Note 作为只读参考，不安装执行状态、旧权限、cwd、Todo 或预算。含 UNKNOWN 时明确提示保护，/status 给出调用身份，/approval 展示脱敏原操作。重复打开不改变 Checkpoint、Journal 或 revision，不请求模型或重放工具。历史参数本来可能就是非法 JSON，查询保留原文，不伪造可执行参数。

### Result / Evidence

回归覆盖两种恢复入口、重复打开、历史与数据库不变、新输入及清理保护、TTY/非 TTY 控制提示、原操作查询、窄屏状态栏和非法参数。只读校验实际用户快照确认 149 条消息。关闭缓存写入后，关键 Runtime / CLI 回归 241 passed（362.80 秒）；实际 CLI Python 3.12 的 7 项新回归全部通过。完整单测出现 6 个失败标记且因 D 盘无剩余空间写缓存失败，未取得可靠最终报告，不能记为全绿或将全部失败归因于磁盘。

2026-10-13 用户确认持续输出的日志积累约 90 GB，并已释放空间。满盘写入曾使本文档缺失，恢复副本末段还包含聊天截断标记；本次以完整原始补丁恢复并核对后续阶段记录。冻结源码后用默认 Python 3.14.3 重跑完整 tests/unit，结果为 785 passed、12 skipped、0 failed（396.80 秒）。六个失败标记没有复现，但旧报告不完整，不能给每项失败追认原因。未修改生产代码、用户日志或执行事实，没有新增日志管理能力。

### Decision / Limitation

仅加载终结 Run 的参考历史，非终结崩溃 UNKNOWN 仍禁止恢复执行。人工核验后由用户显式归档再开新会话，不自动核销 UNKNOWN。本次没有确定 Bash 卡住的原因，也不保证取消父进程会停止外部子进程。

## 2026-10-12：收拢运行态与统一 Rich 渲染

Commit: `40638955aca983bd840313c626d94616333f5e72`
Commit Description: `feat: 完善本地会话恢复、授权交互与终端展示`

### Description

上一版为避免 Bash 长时间运行时看不到命令，在 handler 前持久打印命令、cwd 和取消提示；用户进一步要求不要把工具过程铺成多行日志。此次明确选择动态单行状态：使用已有执行事件，在开始时显示工具及命令摘要，console.status 原地更新，结束后清除，只留下简明结果。长摘要按显示单元省略，完整操作仍从审批详情查看；不再承诺运行中持久显示完整命令。非交互管道只保留结果。

TerminalUI 的手写 ANSI 和原生 print 由 Console 接管，统一柔和的命名主题。计划信息 dim，成功只强调小图标，失败在事件确实带有原因时附带关键原因；未知、取消与未执行不标记成功。进入输入、审批、回答或控制信息前停止刷新，PromptSession 的输入和按键协议保持原样。保留旧 entrypoint 的纯文本 _paint 兼容适配，但它不再产生终端控制码。requirements.txt 与 pyproject.toml 都声明 Rich，缺依赖的旧安装仍有普通文本退化。

### Result / Evidence

新增确定性测试验证一个状态对象原地更新、start/stop、结果一行、输入和输出边界停止刷新、NO_COLOR 下状态生命周期、CJK 窄终端、模型 markup 原样展示，以及 UNKNOWN / CANCELLED 不呈现成功。测试发现脱敏结果截断后再次脱敏会重新扩展字段长度，改为在裁剪后的 Rich Text 上直接渲染。原完整命令持久日志测试按新契约改为检查 handler 进入前动态状态已存在，执行次数和审批边界继续验证。Windows 的测试捕获环境 TERM=dumb；ANSI 颜色测试明确模拟现代终端，保留生产环境 Rich 自动探测，不强制向真实 dumb 终端发控制码。

CLI、审批、资源授权、Markdown 与输入集成组合 123 passed（122.02 秒）；随后补齐计划文字同类宽度修复，最终展示专项在 Python 3.14 与实际 CLI Python 3.12 均为 16 passed。完整单测未重跑，git diff --check 无空白错误。

### Decision / Limitation

本次不改 Agent、工具或审批协议。普通真实 tool_result 尚不携带错误原文，因此 UI 只能展示事件提供的 error/message/reason，不能声称已展示所有真实失败的具体原因。宽度按渲染时读取终端尺寸，活动状态不是完整审计日志；模型状态在 API 返回后由调用者展示答案时清除。没有进行真实模型运行、所有终端视觉验收或 Linux 实测。

## 2026-10-12：在 Shell handler 前保留实际命令

Commit: `40638955aca983bd840313c626d94616333f5e72`
Commit Description: `feat: 完善本地会话恢复、授权交互与终端展示`

### Description

用户运行服务启动和验证任务时，Bash 长时间未返回，屏幕仅显示“执行中 · bash”。原展示缓存是最多 120 字符的摘要，结果返回前不可见，因此既无法确认正在做什么，也难以判断是否应该中断。此次沿用 dispatch 后、handler 前的 tool_execution_started 事件，增加完整命令与 RuntimeContext 的当前目录，终端在调用开始前持久打印脱敏内容，长行按宽度换行而不是截断。结果行继续保持紧凑摘要，执行中的进度更新不能擦掉上面的命令。

### Result / Evidence

新增真实 Agent / Mock handler 测试：未审批时没有执行记录；批准后 handler 进入时已经能读取完整多行长命令和 cwd，结果尚未输出；执行次数为一次、Provider 请求数与原调用闭环不变。第二项测试验证脱敏只影响显示，不改变命令参数。两项在旧实现失败后通过；Python 3.12 实际 CLI 环境连同既有审批边界测试 3 passed。没有执行截图中的命令、查询真实服务或使用付费 Provider。

最终默认 Python 3.14 的资源授权、Markdown、审批菜单、CLI 和工具批次闭环定向回归 170 passed（119.00 秒）；完整单测未重跑。git diff --check 无空白错误。

### Decision / Limitation

保持审批、能力校验、Journal 消费和 Shell 超时 / 取消语义。本次解决可见性，不声称修复所有长时间运行问题。普通 Bash 的现有 subprocess 默认超时为 120 秒；子进程及继承管道等行为仍需针对具体命令调查，不能保证 Ctrl+C 清除所有外部副作用。源码严格校验仍保留，此 Agent 源码改动可能让旧未完成 Run 恢复受阻，不应伪造旧批准或自动重试副作用操作。

## 2026-10-12：模型回答的 Markdown 渲染

Commit: `40638955aca983bd840313c626d94616333f5e72`
Commit Description: `feat: 完善本地会话恢复、授权交互与终端展示`

### Description

用户希望回答中的表格、列表和代码有清晰排版。此前展示只用正则去掉标题和粗体标记，代码围栏变为文字提示，无法处理完整 Markdown。本次采用 Rich 的 Markdown 渲染，只替换最终回答展示；审批 diff、控制命令和工具进度保持专用样式。渲染前仍调用显示脱敏和控制字符清理，数据库中的原文不因排版发生变化。交互终端支持无颜色排版；管道保留源文以便复制代码和后续处理。参考官方文档：https://rich.readthedocs.io/en/stable/markdown.html 。

### Result / Evidence

新增五项确定性测试检查标题、列表、引用、表格、代码、颜色开关、重定向源文、控制信息独立和显示脱敏。展示、资源授权、审批菜单及 CLI 专项合计 109 passed；流式 Provider 和 CLI 输入集成 19 passed。没有调用模型，未重新运行完整单测，上一阶段的完整结果保持独立记录。已从真实 TerminalUI 输出生成离线 HTML 预览；第一次导出试图经 PowerShell 的 GBK 捕获管道再次打印 Unicode 列表符号而失败，改为内存输出导出成功，此预览不代表所有 Windows 控制台均已验证。

### Decision / Limitation

当前流式请求仅发生在 API 传输层：Provider 收齐文本和 Tool Call 参数才返回给 Agent。真正逐 Token 展示需要单独接通分片事件，并处理失败残片与工具调用闭环，本次不混入这一改造。Markdown 排版不提供网页布局，窄表格和代码自动换行仍受终端尺寸影响；旧编码控制台与真实交互视觉效果需用户实测。恢复历史的有限摘要仍按原样展示，没有把审批、日志或历史摘要统一解释为 Markdown。

随后真实 CLI 复现缺少 rich 的展示错误：默认测试解释器 3.14 已有依赖，但 PATH 启动器实际绑定 3.12。最小修复是导入依赖失败时保留完整脱敏回答并退化为普通文本，不把展示依赖缺失传播成运行错误；未捕获 KeyboardInterrupt 或普通工具异常。新增用例在修复前明确失败，修复后连同原展示用例 6 项在实际 3.12 环境通过，默认 3.14 展示 / 菜单 / CLI 73 项通过。为真实 CLI 补齐依赖时，项目重装因运行中的 exe 文件锁中断；依据原 RECORD 哈希恢复旧可编辑安装的路径文件和元数据后，实际启动器 --help 成功。以后升级应先退出 CLI 再刷新项目安装，不能仅依据另一解释器的测试通过判断用户环境依赖齐全。

## 2026-10-01

Commit: `677c925`
Commit Description: `refactor: 将 CLI 命令职责从 Agent Runtime 剥离`

### Description

此前 `MiniClaudeAgent` 在初始化时创建 Console 并注册 `/status`、`/features` 等命令，`chat()` 又根据输入是否以 `/` 开头决定执行本地命令还是运行模型。Agent Runtime 因而依赖 CLI 语法。Agent 文件底部还保留另一套交互入口和演示入口，与正式的 `cli.entrypoint` 重复。

本阶段将通用解析、注册、帮助、退出、历史和补全放到 `src/cli/console.py`，将依赖 Agent 状态的命令放到 `src/cli/commands.py`，由 `src/cli/entrypoint.py` 路由 slash command。普通输入继续交给 `MiniClaudeAgent.chat()`，该 API 不再解析 CLI 命令。CLI 通过命令执行 helper 保留 `user_input` 和 `command_result` 会话事件。

同时修复了三项命令行为：`/clear` 实际清空对话历史及依赖该历史的临时标记；未闭合引号等解析错误返回可读信息；`/tasks <status>` 对无效状态列出可用值。`/features` 成功切换后重新构建 system prompt；启用 skills 时先执行发现，使提示中的功能和技能描述与运行状态同步。

### Result / Evidence

新增独立 CLI 命令单元测试，覆盖命令解析与路由、Agent 命令注册、会话事件、`/clear`、无效任务状态以及 skills 启用/禁用后的 prompt 更新。CLI 命令与 system prompt 单测 `10 passed`；Agent Note 单测 `3 passed`；排除依赖系统临时目录的 Agent 用例后，Provider streaming 测试 `16 passed, 1 deselected`；旧集成脚本的通用 Console 用例 `1 passed`。

Agent 集成用例在创建 task 时因 Windows ACL 拒绝写入仓库外既有 runtime-data 目录而失败。带 `tmp_path` 的 Provider/Agent 用例也受 pytest 临时目录 ACL 阻挡，未作为本次回归失败计入。没有运行真实 Provider。

### Decision / Limitation

命令 handler 仍直接读取 Agent 已有的管理器、消息和压缩器对象，以保留现有命令行为并避免为此次边界调整新增服务框架。它们只由 CLI 创建和调用；Agent Runtime 不持有 Console 或命令 handler。


## 2026-10-11：第七阶段 C，本地 HITL 终端与跨进程操作

Commit: `342a5232611a1db268f2351ef5f4d48dc1ee6676`
Commit Description: `feat: 接入本地 HITL 审批 CLI 与端到端恢复验收`

### Description

7B 已将审批等待、用户决定和原调用继续保存在 SQLite，但真实终端只能调用普通 chat，用户无法可靠取得身份、决定或重启恢复。本阶段继续沿用 ConsoleCommandSystem：新增 /runs、/approval、/approve、/reject、/resume，仅在显式启用 HITL 时注册；模型输出只展示，不经过输入命令路由。必须同时给出 Run ID 和 Approval ID，避免多个 Run 或多个审批时猜测用户目标。CLI 不写 approvals / calls、不 dispatch handler、不新增执行状态机。

批准和拒绝只调用 decide_approval，显式恢复只调用 resume_checkpoint。等待时展示原参数、Provider ID、位置、Workspace / cwd、审批原因和当前 / 历史状态，返回普通 REPL 提示；没有在 Runtime 内嵌套等待输入，也不长期持锁。PENDING 不执行；APPROVED 仍重新检查执行上下文；REJECTED 沿 7B 关闭批次后重新规划；INVALIDATED 不再提示重新批准；CONSUMED 不能重复消费；UNKNOWN 明确阻断，不提供重试确认；终结 Run 显示保存结果。

首次创建数据库需要 --hitl --init-checkpoints；之后 --hitl 或 --resume 要求原有效数据库。默认沿用 RuntimeDataPaths 的稳定工作区目录，--checkpoint-db 可明确定位。Store 增加 require_existing，通过 SQLite URI mode=rw 阻止文件在检查后消失时重新创建，并拒绝恢复时初始化 version=0 空文件。--resume 即使同时指定 --init-checkpoints 也不能绕过缺失库检查。没有更改 SQLite Schema 或审批消费事务。

现有 API 无法查询全部 Run 或终结审批，因此增加小型只读 Runtime 查询 get_checkpoint_info / list_checkpoint_runs，底层查询复用 Store 连接和已有 load 校验；查询不拿执行锁、不改变调用状态。展示是时点视图，真正决定和恢复仍重新读权威数据库并持锁验证；不承诺 UI 查询是执行授权。

审批详情不写入 Console 历史或命令结果 Session 日志，只保留不含参数的 cli_control 命令类别事件。HITL CLI 为终端事件、Session Recorder 和日志增加展示脱敏，覆盖敏感字段、常见凭据赋值 / 命令参数、Bearer、URL 凭据及终端控制字符。原授权参数和执行事实不改变，普通模式不启用该行为；退出清理日志 Filter。脱敏不能识别任意文本秘密，可信 SQLite / Snapshot / 必要执行 Trace 仍保存原操作，未开发加密或认证。

拒绝原因沿用 7B 的固定“用户拒绝原操作，剩余依赖未执行，需要重新规划”说明。现有 ApprovalRecord 没有自由文本理由字段，本次没有将理由塞入 actor，也没有为交互修改审批数据库模型。

### Result / Evidence

Windows Python 3.14.3 最终冻结代码完整 tests/unit：646 passed、12 skipped、4 failed（328.38 秒）。新增 CLI 45 项全部通过，其中三项参数化工作流使用真实独立 Python 进程、实际 entrypoint.main、管道 stdin 和 Mock Provider：批准后退出 / 重启 / 启动 --resume，拒绝后原批次关闭，以及活 handler 竞争 / 进程死亡转 UNKNOWN。批准与拒绝流程均断言四个不同 PID，执行前缀 A 不重放，B / C 按原 ID 执行或明确不执行，重复决定和终结恢复不增加副作用。没有真实付费 Provider、危险 Shell 或 Terminal-Bench。

覆盖等待展示、未决定恢复、连续 ASK、多个等待 Run、明确身份 / 跨 Run 错配、Feature / Workspace / cwd / handler / 策略变化、过期 / 失效、决定事务提交前后失败、数据库损坏 / 丢失 / 空文件、查询无副作用、控制与模型文本分离、参数脱敏与授权原值、Todo / Note / 历史 / 预算及消息闭环。既有 CLI / 输入兼容定向集 14 passed；LoopController / 输入 integration 37 passed。末次隐私修复及既有 CLI 单测 13 passed，最终完整单测包含该修复。

首轮设置 PYTHONIOENCODING=utf-8，旧跨进程锁测试按 GBK 读取 stderr，出现 readerthread UnicodeDecodeError / stderr None，完整结果 644 passed、12 skipped、5 failed；同一环境在未修改 main 也复现。移除此环境覆盖后，该锁用例单独通过，默认编码的完整复跑为 645 passed、12 skipped、4 failed。随后审查发现带引号命令名可以被 Console parser 接受，但命令诊断使用空格拆分，导致审批详情被当成普通命令结果记录。新增回归先复现失败，再复用既有 parser 分类，保持实际 handler 只执行一次；最后重新冻结完整验证，得到上述 646 / 12 / 4。

未修改 main f0de996 上的四项失败复核为 2 failed、2 passed。Provider fixture 缺 llm.provider，read_file 描述缺期望 start_line/end_line 字串仍失败，不删断言或直接宣布测试过期；Node / Java Reference Variant 在 main 通过，本 Worktree 的两份 oracle 是 CRLF（7 / 13 行），main 是 LF。测试只复制并修改临时工作目录中的实现文件，原 oracle 测试前后 SHA256 分别保持 c7c7f26829d0be90577cbfb2bb876df913d3d9aee2f9d4d69ec63f8ca9238e8e 与 24c4fec6e5fba5de30d2bb8d001a56381a4503c021e90b8d19bf9b8c6e9cae6e；不把完整单测描述为全绿。

### Decision / Limitation

保留默认保守策略。实际 CLI 测试确认 Shell 查询 / pytest / 构建、TodoWrite、Agent Note 都会 ASK；同一批三个 ASK 分别审批，不自动累计授权。拒绝后模型再次提出同一操作会生成新轮次和新审批 ID，仍要求新决定；这种频繁中断与反复提案留给独立策略优化，不引入会话放行。

只保护主 Agent 的既有 SQLite 调度。直接 Registry / BaseTools、独立后台 API、SubAgent 不新增 HITL 保护，原持久化模式的禁止启动规则仍在。可信终端 actor 使用本地用户名做审计，不提供多用户认证；没有 UNKNOWN 补偿、INVALIDATED 核销 / 取消接口，也没有 Shell 沙箱。源码指纹仍 fail-closed，旧版本 Run 不自动跨本阶段源码修改继续。Windows 真实进程和管道终端已经执行；真实 Linux / POSIX 和人工 TTY 交互尚需后续验证，不把 Mock 通过称为全平台验收。


## 2026-10-11：交互终端单次审批菜单

Commit: `40638955aca983bd840313c626d94616333f5e72`
Commit Description: `feat: 完善本地会话恢复、授权交互与终端展示`

### Description

7C 的命令入口可靠，但日常使用需要复制 Run ID 和 Approval ID，再手工 resume，打断了编码流程。现在真实 TTY 在 Runtime 返回 WAITING_APPROVAL 之后展示操作卡片，默认展示实际参数、Workspace、cwd 和原因，诊断身份移入详情。选择允许一次或拒绝时，CLI 绑定刚展示的审批 ID，先记录决定再调用原 resume_checkpoint；遇到下一个 ASK 再展示卡片。数据库失败直接返回外层错误处理，不自动重试。UI 等待发生在 Runtime 返回、执行锁释放之后。

### Result / Evidence

菜单及既有 CLI / 输入定向集 23 passed，覆盖真实 Mock Runtime 的批准继续、拒绝闭环、连续三个 ASK、暂停不消费、查询详情、决定失败不执行、管道保留原命令流程，以及操作展示脱敏。没有真实付费模型调用。

### Decision / Limitation

空输入保持暂停，Ctrl+C / EOF 保持暂停并退出；管道不自动显示菜单，以保持明确命令协议。不新增 SQLite 状态机、不扩大授权、不提供虚假的始终允许按钮。自由文本替代方案和可撤销的持久授权需要后续独立 Runtime 契约。实际人工 TTY 视觉和键盘体验仍需用户确认，本版采用数字 / y / n 输入，不提供方向键选择。

## 2026-10-11：默认审批与日常会话入口

Commit: `40638955aca983bd840313c626d94616333f5e72`
Commit Description: `feat: 完善本地会话恢复、授权交互与终端展示`

### Description

原 CLI 要求首次初始化参数，恢复需要长 Run ID，完成任务还只能查看结果，无法重新打开后接着聊天。本次普通启动默认开启审批和 SQLite，会话首次创建自动初始化默认数据目录；--continue 选择当前工作区最近创建的 Run，--resume 无参数展示任务标题 / 状态的数字列表，有参数仍可精确定位。--no-hitl 为明确退出持久化和审批的高级入口，Python Agent API 仍保持原显式启用行为。

Runtime 增加 open_checkpoint_conversation：已完成 Run 在独占锁下校验存储记录与工作区归属，加载消息和笔记，但不导入旧 RunState、批准、执行游标、cwd 或治理预算。新输入创建新的 Run，历史消息只作为上下文；因此工作区变化不会阻止参考已完成对话，也不会赋予旧操作新的执行权。其他状态仍交给原 resume_checkpoint，UNKNOWN 不重试，审批仍重新校验。没有修改 SQLite Schema 或执行消费事务。列表按数据库插入顺序显示最近创建 Run，不用随机 UUID 充当时间。

### Result / Evidence

新增会话专项覆盖默认启用、创建默认库、恢复不创建空库、损坏库不降级、当前工作区筛选、列表选择 / 取消、完成上下文与新 Run 身份、旧工具不重放、待审批批次恢复，以及真实 REPL 的 --continue 加载。既有要求显式 --hitl 的参数断言改为验证 --no-hitl 与持久恢复参数互斥，保留其他有效断言。最终完整回归结果待本轮冻结验证后回填。

### Decision / Limitation

高级自定义缺失库仍要求 --init-checkpoints，避免误拼路径创建另一份空历史。默认 API 行为未扩大到 SubAgent 或后台入口；CLI 默认进入持久化模式后沿用其后台启动限制。源码变化仍可能使旧未完成 Run 不兼容，已完成历史仅作为参考加载，不继承旧授权。没有会话级放行、审批反馈编辑、历史删除或多进程同时对话编辑。首版按 Run 显示历史入口，同一对话中的不同任务可能各占一行；最近指最近创建任务，不是最近查看时间。

## 2026-10-11：从调试命令转为日常会话与审批体验

Commit: `40638955aca983bd840313c626d94616333f5e72`
Commit Description: `feat: 完善本地会话恢复、授权交互与终端展示`

### Description

用户实际使用发现，复制两段长 ID、批准后再手动恢复，虽然可审计，却不适合日常编码。前一版菜单改善了单次交互，但历史仍按 Run 展示，一次对话的每条输入都占一行；“拒绝”还会马上让模型再提方案。这次参考主流本地 Coding Agent 的会话选择与操作确认方式，保留已有可靠执行协议，调整产品入口。

新增 Conversation 身份，包含一组 Run，消息仍以当前 head Run 的快照为准，不建立第二套消息或执行日志。普通新输入创建 Run，但留在同一 Conversation；`--continue` 取最近活动，`--resume` 用可搜索的方向键选择器。打开历史只导入参考上下文，未完成 Run 仍走原恢复协议。创建新 Run 时持会话独占并核对 head，另一个进程已经推进会话则拒绝旧内存覆盖。

审批卡片展示文件替换片段 diff、完整写入或 Shell 命令。允许一次仍先记录审批，再调用 Runtime 恢复。“拒绝并结束”关闭未执行的批次尾部，不再请求模型；“换一种方式”保存用户意见，闭合工具消息后再给模型重规划。意见与拒绝决定同一事务持久化，重启不会丢失。输入只来自本地终端，模型文本不走命令分发。

加入两种明确授权范围：当前 CLI 进程、当前工作区。保存前第二次确认，只匹配完整参数、cwd、工作区和工具/策略版本；不采用 Shell 前缀或全工具放行。它们与一次性 Approval 分开，原批准仍只消费一次，数据库故障不会降级成允许。原规则可查看和撤销；更宽的会话策略、沙箱和凭据分类没有在本次实现。

UNKNOWN / 失效操作提供归档而不是重新批准。归档仅记录原因、隐藏会话入口并阻断原 Run 的执行与审批，保留 snapshot、journal、UNKNOWN 和批准消费事实。人工检查后可以提出新计划，但归档不证明副作用已经消除。回归发现，失败恢复曾提前发布已归档的会话身份，导致后续新任务仍被旧会话阻挡；现在先检查归档与 head，再发布可操作身份。选中 UNKNOWN 会话也在新输入初始化前阻挡覆盖，CLI 保持可查看和归档。

用户配置允许 `MINI_CLAUDE_CONFIG` 和 `~/.mini-claude/config.yaml`，优先于仓库默认配置；显式文件或所选 Provider 的环境变量缺失时报错，不换 Provider。运行数据不再绑定作者 D 盘路径，采用 Windows LOCALAPPDATA / POSIX XDG 用户数据目录；旧库需要明确指定，不自动迁移。

### Result / Evidence

Windows Python 3.14.3 最终冻结完整 tests/unit：723 passed、12 skipped、0 failed（351.77 秒）。前两次完整无失败复跑为 719 / 720 passed、12 skipped，随后追加归档目标与非字符串命令回归，最终结果包含全部新增测试。CLI 输入 / LoopController integration 37 passed；会话 / 菜单组合 53 passed；历史失败相关与配置专项 58 passed。没有真实付费模型调用或 Terminal-Bench。

确定性测试检查会话跨实例全历史、head 冲突、暂停锁释放、审批原 ID 与结果唯一性、反馈重启保存、明确范围授权及撤销、规则损坏 / 数据库失败、UNKNOWN / INVALIDATED 归档保留事实。使用真实 prompt-toolkit 输入管道验证 ↑↓、Enter、Esc 和搜索；既有独立 Python 进程 CLI 恢复测试继续验证 A 不重放、批准 / 拒绝和执行独占。最后复现 Windows 路由对 None / 数字 / 布尔 command 的正则 TypeError，增加类型检查后回到已有 arguments.invalid 拒绝，不调用 handler。

原四项失败没有删除有效断言：Provider fixture 补齐实际 Trace 初始化需要的配置；read_file 描述补齐接口已经支持的 start_line/end_line 分页契约。Node / Java Reference Variant 在当前主工作区本来通过，原 run_tests.js / run_tests.cmd 字节仍与 HEAD 一致；新增 .gitattributes 让以后 checkout 的文本保持 LF，不修改 oracle。初次完整复跑还有会话锁夹具遗漏释放、默认解释器缺 prompt-toolkit 的六项失败；适配手工安全边界夹具并补齐项目声明依赖后消除，不归咎于 Runtime 或删断言。

新增 GitHub Actions：Windows/Linux × Python 3.10/3.14，安装项目依赖、验证 Node/JDK 存在后运行离线单元及输入/循环集成测试，再检查已跟踪文件未被测试修改。YAML 与矩阵已静态检查；本轮未推送或运行远程 CI，12 项平台跳过及真实 Linux/POSIX 验证仍待 CI，不把配置存在说成跨平台通过。

### Decision / Limitation

SQLite v4 增加身份索引、归档、授权规则与拒绝意见，沿用原 Store 的事务、锁与原子消费；旧没有 Conversation 身份的 Run 保留 /runs 入口，不猜测历史分组。源码和整体工作区摘要的严格校验继续保留：本次优先改善明确的体验与错误，不同时放宽崩溃恢复安全协议。后续应独立设计工具前置条件和兼容版本，再考虑减少无关文件变化造成的失效。终端脱敏不能识别任意秘密，授权和执行日志仍含原操作，本地 SQLite 不提供认证、加密或 Shell 隔离。

当前允许的 Shell 查询故意很小：只允许字面量内置 cwd 查询和简单 echo；git、测试、构建及复合命令保持 ASK。TODO/Note 自动允许只适用于核实过的内置 handler，替换或未知 handler 仍 ASK。不支持恢复的后台 / SubAgent 能力在模型 schema 与 dispatch 都排除；独立 API 仍不是主 Agent 的 HITL 范围。

在首个完整 Snapshot 之前退出的初始化记录仍不能原样恢复；缺少可核验 Workspace 快照的记录也尚不提供归档。可以退出后不带 --continue 开始独立新会话，保留原记录，不通过改写数据库伪造执行事实。这类初始化记录的管理，以及规则的更细粒度过期/条件表达，留作独立任务。
