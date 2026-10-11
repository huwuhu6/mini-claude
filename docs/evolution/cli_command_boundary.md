# CLI Command 边界演进

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
