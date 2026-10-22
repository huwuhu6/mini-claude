## 2026-10-13：功能已提交，待文档提交与本地快进整合

功能提交 40638955aca983bd840313c626d94616333f5e72 已在 codex/fix-provider-config-discovery 创建，提交说明为「feat: 完善本地会话恢复、授权交互与终端展示」。累计 40 个文件（包括此前 12 个未跟踪文件）已进入提交；功能提交后工作区干净。Evolution 中本次累计阶段的 PENDING 已回填为此真实 SHA，待单独提交文档。本地 main 仍为 5f41f1d09806ed8a4c387bd80b47a07adaa45609，尚未整合或推送。下文保留的是各阶段当时的现场记录。

## 2026-10-13：磁盘故障后的文档恢复与完整回归（未提交）

用户确认持续输出的日志积累约 90 GB，已释放 D 盘空间。本次检查 D 盘约有 99.5 GB 可用；从完整原始操作记录恢复 cli_command_boundary.md，补齐恢复副本末段的截断内容并保留后续补丁。文档不再缺失，没有修改生产代码、运行数据库或用户项目日志。

冻结源码后使用默认 Python 3.14.3 重跑完整 tests/unit：785 passed、12 skipped、0 failed（396.80 秒），禁用 pytest 缓存写入。上次六个失败标记未复现，因当时没有可靠失败报告，不将它们全部归因于满盘。git diff --check 通过；未调用付费模型，未提交、合并或推送。当前仍在 codex/fix-provider-config-discovery；HEAD / main 为 5f41f1d09806ed8a4c387bd80b47a07adaa45609。后续先审查累计修改和未跟踪文件，再按授权提交与安全整合；本次没有新增日志轮转功能。

## 2026-10-12：取消会话的只读历史与 UNKNOWN 提示（未提交）

终结 Run 恢复曾提前返回取消文本，导致消息数显示 0，隐藏原快照。现在含 UNKNOWN 的终结 Run 可加载经过校验的消息与 Note 作为参考；不恢复执行状态、cwd、Todo 或旧权限，不重放工具，原数据库事实不变。状态栏和 /status 明确只读保护，/approval 展示脱敏原操作供人工核验。非终结崩溃 UNKNOWN 仍阻断。历史非法参数 JSON 保留原文，查询不会再次解析失败。

只读校验用户原数据库确认快照 149 条消息，未修改数据库或执行命令。当时 D 盘已无剩余空间，pytest 缓存写入失败，不能将该轮完整单测称为全绿。关闭缓存后的关键 Runtime / CLI 回归 241 passed；完整单测出现 6 个失败标记且缓存写入失败，未取得可靠最终报告。实际 CLI Python 3.12 的 7 项本次新回归全部通过。未提交、合并或推送。

## 2026-10-12：Rich 动态状态与柔和终端排版（未提交）

用户要求仅重构 TerminalUI：此次未修改 mini_claude_agent.py，保留原有未提交 Agent 改动。requirements.txt 补齐 rich>=13.7.0；常规输出统一 Console，状态统一 console.status，运行期间显示带命令摘要的单行动态状态，结束后仅一行结果。计划文字 muted，成功绿色仅用于图标，失败使用柔和红色；UNKNOWN / CANCELLED / 未执行仍分别展示。PromptSession 的按键、输入和审批协议不变，进入输入或审批前停止 Rich 刷新。旧 entrypoint 的 _paint 调用保留纯文本兼容适配，不再生成 ANSI。

本阶段按最新需求替代此前的多行持久完整命令展示；长命令在运行态按终端宽度省略，完整审批参数仍在详情中。当前 Agent 的普通 tool_result 事件没有错误原文，UI 只显示事件实际携带的 error/message/reason，不猜测、不新增 Runtime 查询。已覆盖状态生命周期、宽度与脱敏二次扩展、输入边界、无颜色状态条与未知结果。CLI / 审批 / 资源授权 / Markdown / 输入集成组合 123 passed；随后补齐计划文字的宽度修复，最终展示专项在 Python 3.14 / 实际 CLI Python 3.12 均为 16 passed。完整单测未重跑，git diff --check 无空白错误。

## 2026-10-12：模型回答 Markdown 展示（未提交历史）

同日追加 Shell 执行前可观测性修复：用户遇到 Bash 久未返回时只看到“执行中 · bash”，原命令摘要要结束才展示且只有前 120 字符。现在既有 tool_execution_started 事件携带原命令与 RuntimeContext.cwd，UI 在 handler 前持久打印完整脱敏命令、目录及 Ctrl+C 提示，长命令换行，授权未通过则不产生执行记录。仅改展示事件及 UI，不改 Shell、审批消费或执行状态协议。两项新增回归先复现失败后通过；实际 CLI Python 3.12 的这两项与既有审批边界测试 3 passed。并未核实用户运行中那条命令或停止其 Redis / Java 子进程。源码兼容校验仍保留，旧未完成 Checkpoint 可能因 Agent 源码变更而拒绝恢复，不能通过重试绕过。

本次最终 Python 3.14 定向回归（资源授权、Markdown、审批菜单、CLI 和工具批次消息闭环）170 passed。完整单测未重跑，git diff --check 无空白错误。

在下述未提交体验改动之上，仅替换 TerminalUI.print_answer 的简单正则排版为 Rich Markdown，并声明 rich>=13.7.0。交互终端渲染标题、列表、引用、表格及代码高亮；NO_COLOR 保留排版但禁用 ANSI，重定向保留经显示脱敏的 Markdown 源文。控制输出、审批卡片、消息与 Checkpoint 均不改变。

本次展示 / 资源授权 / 审批菜单 / CLI 测试 109 passed；流式 Provider 与 CLI 输入集成 19 passed。新增 Markdown 展示测试 5 项。未重跑完整单测；下方 760 passed、12 skipped 是上一轮结果，不代表此次重跑。离线预览成功导出，真实 Windows 终端视觉验收及 Linux 实测仍待完成。

当前默认启用的是 OpenAI-compatible 流式传输，DeepseekProvider._collect_stream 收齐响应才交给 Agent，尚无逐 Token 终端展示。没有为了 Markdown 修改 Provider、执行循环或恢复协议。长期取舍见 cli_command_boundary.md 的 2026-10-12 记录。

用户实测发现 CLI 的启动器绑定 Python 3.12.1，而测试默认 Python 3.14.3；前者缺 rich，导致已完成工具后的回答展示抛出 ModuleNotFoundError。新增依赖导入失败时普通文本退化，回归先失败后通过；当前 CLI 解释器已安装 rich。重新安装项目时运行中的 exe 被 Windows 锁定，按原 RECORD 哈希精确恢复可编辑安装 .pth 与元数据，未终止进程或覆盖启动器。最终实际 mini-claude --help 成功，Python 3.12 展示 6 passed；Python 3.14 展示 / 菜单 / CLI 73 passed。完整单测未重跑。后续需要刷新安装元数据时先退出 CLI，再使用对应解释器重新安装项目。

## 2026-10-11 当前现场：文件授权范围与终端信息层次（未提交）

当前分支 `codex/fix-provider-config-discovery`；HEAD / 本地 main 仍是 `5f41f1d09806ed8a4c387bd80b47a07adaa45609`。在本 Worktree 继续已授权的未提交体验方案，完整保留此前改动；不提交、整合或推送，不改其他 Worktree 或评测 oracle。

- 输入状态栏显示工作区、Conversation 短 ID、审批开关及状态；`/status` 显示完整会话与 Run 身份、实际 Checkpoint、Recorder JSONL 和 Trace 路径，`--details` 再显示 Recorder / Trace ID。恢复历史回显最近用户和助手上下文，选中的完成会话不会借用旧内存 Run 身份。
- 内置 edit_file / write_file 共享规范文件目标授权，内容变化不再反复审批。本会话规则随 Conversation 保存，重启继续保留，新对话不继承；工作区规则跨对话有效。Shell 仍匹配完整参数、cwd 与平台，不放宽为前缀。敏感 / .git / 越界文件及自定义文件 handler 不开放文件范围规则，硬拒绝优先。
- 新规则使用原 SQLite v4 表的版本化 JSON，旧精确规则不扩大。一次性审批原 Call ID / 完整参数及消费事务不变；规则不是执行事实。共享编辑预校验进入源码兼容摘要，编辑读写保留原 CRLF。
- 审批只有一个可擦除的选择器，文件预览按完整文件生成全局 diff，与执行共用匹配函数；初始 Enter 保持暂停。已复现并修复 Backspace 重置到允许项的问题。长预览可滚动，快捷键说明完整换行，数字退化入口可确认规则。文件菜单不显示 Shell 提示。
- 每轮任务、临时模型/执行进度、工具结果、控制消息和最终回答分层；提议工具在审批前不显示为正在执行。`/status` / `/permissions` 只查询，不重新弹审批。`/help` 列常用入口，脚本审批、兼容与实验入口留在 `/help advanced`；授权列表显示目标与范围，不倾倒原操作 JSON。
- 最终冻结完整单测 760 passed、12 skipped、0 failed（381.42 秒）；菜单 / 资源授权组合 59 passed，包含两个 Backspace 和两个错误参数类型回归，以及三个真实循环 UI 分类用例。CLI 输入 / LoopController integration 37 passed。Windows Python 3.14.3，无真实模型调用。Linux/POSIX 与人工 Windows 终端视觉体验尚未实测。
- 原来的四项失败根因记录保留在下方历史和 Evolution；本轮未修改 oracle。源码/整体工作区摘要仍严格校验，Shell 没有沙箱；旧未完成 Run 因源码变化可能不能恢复，不能借资源规则复活失效审批。

本轮决策与详细证据见 `docs/evolution/cli_command_boundary.md` 和 `docs/evolution/tool_registry.md` 同日期的新增记录；Commit 为 PENDING，等待用户后续提交后回填。

## 2026-10-11 当前现场：日常会话与审批体验整理（未提交）

分支 `codex/fix-provider-config-discovery`，HEAD / 本地 main 基线 `5f41f1d09806ed8a4c387bd80b47a07adaa45609`；保留前轮配置、会话和审批菜单的未提交修改，在当前 Worktree 继续用户已批准的体验方案。未提交、合并或推送，未修改其他 Worktree 或评测 oracle。

- CLI 默认审批 / SQLite，`mini-claude` 当前目录新会话，`--continue` 最近活动会话，`--resume` 可搜索的 ↑↓/Enter/Esc 选择器。Conversation 包含多个 Run，只维护 head 索引，消息仍以 head Run Snapshot 为准；旧无会话身份的记录继续 /runs 查看。会话独占与 head 核对阻挡跨进程旧上下文覆盖。
- 真实终端审批展示片段 diff / 完整命令。允许一次、进程精确规则、工作区精确规则、拒绝并结束、替代意见后重新规划；规则额外确认且可撤销，不是 Shell 前缀放行。Runtime 消费一次性批准的原事务不变，用户意见同事务持久化。
- 默认策略核实内置 Todo / Note 与少量字面量 Shell 查询后允许；写入、测试、构建、git 和未知风险仍 ASK，硬拒绝不能绕过。不支持持久恢复的后台/SubAgent 在 schema 与 dispatch 都阻挡。UNKNOWN / INVALIDATED 可记录人工处理原因并归档，保留执行事实、阻断旧操作；不核销 UNKNOWN 或自动重试。
- SQLite v1–v3 自动事务升级为 v4。数据目录改为 LOCALAPPDATA / XDG 用户目录，旧 D 盘数据库需 `--checkpoint-db` 显式定位，不自动移动。配置可用用户文件或 MINI_CLAUDE_CONFIG，不再仅依赖当前工作目录；缺失所选环境变量报错，不切换 Provider。
- 最终冻结完整 tests/unit：723 passed、12 skipped、0 failed；CLI 输入 / LoopController integration 37 passed。前两次完整无失败复跑为 719 / 720 passed，最终包含末次归档目标与参数类型修复。历史四项根因与旧失败记录保留：补齐 Provider fixture 配置、同步 read_file 分页描述，不删断言；Node/Java oracle 原字节与 HEAD 一致，.gitattributes 保证未来 LF checkout。不调用付费模型。
- 添加 Windows/Linux、Python 3.10/3.14 CI；本地实际是 Windows Python 3.14.3。该解释器缺少 prompt-toolkit，已安装项目声明依赖补齐键盘实测。Linux/POSIX 本轮尚未实测，不能把 CI 配置存在当成通过。
- 源码指纹与整体 Workspace 摘要严格恢复门槛仍保留；操作级前置条件、沙箱、UNKNOWN 补偿和更宽 Shell 查询白名单留作独立任务。本轮没有继续增加通用状态机。

长期设计、证据与取舍以 `docs/evolution/cli_command_boundary.md`、`docs/evolution/tool_registry.md` 本日期的新增阶段记录为准，两处 Commit 标记 PENDING 等用户后续提交后回填。

## 2026-10-11 配置加载修复（未提交）

此前真实 TTY 新增单次审批卡片；本轮已经扩展为上面的规则和反馈语义。ID 命令保留用于诊断、管道和跨进程定位，原 SQLite 审批消费协议不变。

此前已修复目标项目启动时丢失配置、静默切到 DeepSeek 官方的问题；本轮增加用户配置优先级和所选 Provider 环境变量校验。原回归保留，不修改密钥，不发起真实模型请求。

## 2026-10-11 当前现场：第七阶段 C 本地 HITL CLI

- 基线 main 为 `f0de996d71e28922edffd29471216eb7aa30844e`，已包含 7B 功能 `f069085` 和文档 `f0de996`。独立分支 `codex/hitl-cli-interaction`；Worktree `D:\02_study\code\AgentProject\mini-claude-worktrees\hitl-cli-interaction`。功能提交为 `342a5232611a1db268f2351ef5f4d48dc1ee6676`；用户已授权仅 fast-forward 整合至本地 main，不推送、不删除 Worktree。
- 首次 --hitl --init-checkpoints；重启 --hitl 要求已有数据库，--checkpoint-db 明确指定原库，--resume <Run ID> 为启动恢复。所有决定 / 执行都走原 Runtime API；新增只读查询，未改 SQLite Schema 或原子审批消费协议。
- /runs、/approval <Run ID>、/approve 或 /reject <Run ID> <Approval ID>、/resume <Run ID>。决定与执行分开，普通输入、压缩和清空不能覆盖等待 Run。UNKNOWN 无重试入口，INVALIDATED 无重新授权提示。固定拒绝理由沿用 7B；不支持自由文本理由。
- 展示及诊断脱敏不改变授权绑定；审批详情不写命令历史 / 结果日志，必要持久化执行事实仍含原参数。敏感字符串不能保证全部识别，可信本地库不提供认证或加密。
- Windows Python 3.14.3 最终完整单测 646 passed、12 skipped、4 failed；新增 CLI 45 项通过，包含三个真实独立进程 CLI 工作流；LoopController / 输入 integration 37 passed。main 同四项复核 2 failed、2 passed，原 oracle 哈希不变；UTF-8 覆盖造成的旧 GBK 子进程测试问题和带引号命令名日志缺陷均有复现 / 验证记录。Linux / POSIX 和人工 TTY 尚未验证。实现取舍、验证范围和剩余限制见 docs/evolution/cli_command_boundary.md 第七阶段 C。

## 2026-10-11 当前现场：第七阶段 B 持久化 HITL Runtime

- 开发基线为 `899fa3c247e0d021af2ee752b5b4a3dbc3be5f85`，已包含第 7A 功能 `2518787` 和文档 `899fa3c`。独立分支 `codex/hitl-runtime-integration`，Worktree `D:\02_study\code\AgentProject\mini-claude-worktrees\hitl-runtime-integration`；功能提交为 `f069085c9f072f8fd97511e05eb6b41f33e6aa29`；用户已授权仅以 fast-forward 整合到本地 main，不推送、不删除 Worktree，不进入 7C。
- 主 Agent 显式 `enable_checkpoints()` 后可 `enable_hitl()`，或构造时同时启用二者。默认关闭；不提供 CLI 审批交互，不进入 7C。
- Runtime API：`get_pending_approval(run_id)`、`decide_approval(run_id, approval_id, approved=..., actor=...)`、`resume_checkpoint(run_id)`。等待期间新输入及普通压缩被阻止；新实例恢复 PENDING 也安装 WAITING 生命周期。
- ASK 在同一事务创建审批并保存 WAITING_APPROVAL，当前调用保持 PREPARED，不生成结果 / 不推进游标 / 不计失败，立即释放锁。批准后沿原循环原子消费并标 RUNNING，拒绝则关闭当前及剩余调用后重新规划。
- SQLite v3 事务扩展 runs 的状态 / 边界 CHECK，保留旧 calls 与 approvals；HITL 绑定投影 v3 只能通过 SQLite 恢复。普通 JSON v1 暂停入口继续支持，但不接受审批等待；旧源码指纹兼容门槛不放宽。
- 核心实现与限制见 docs/evolution/tool_registry.md 第七阶段 B；已回填真实功能提交 SHA。提交前定向回归 333 passed；完整单测的既有验收结果及四项历史失败、平台限制以该记录为准。

## 2026-10-11 当前现场：第七阶段 A 权限与审批契约

- 本阶段开发基线为 `c97f917a8ac591c1bc76c322824f422842e46107`，第六阶段功能提交 `d892ed3` 已整合。第七阶段 A 功能提交为 `2518787fe6b4531ab8055774e3ae65373186700b`；分支 `codex/hitl-permission-contract`，Worktree `D:\02_study\code\AgentProject\mini-claude-worktrees\hitl-permission-contract`。本轮仅采用 fast-forward 整合到本地 main；不推送、不删除 Worktree、不进入 7B。
- 新增 PermissionDecision / ToolOperation 与 DefaultPermissionPolicy。已有硬拒绝优先，只核实过的内置只读 handler ALLOW；写入、Shell、网络 / 状态变更与未知风险 ASK。本阶段未调用新策略改变正常 Agent 流程。
- 新增 ApprovalStore / ApprovalRecord，复用 Checkpoint 数据库及 Run 锁。SQLite v1→v2 事务迁移新增 approvals，保持 runs / calls 不变；原 Snapshot 源码兼容约束仍然有效。
- 创建 / 用户决定只保留审批事实，调用仍 PREPARED。消费 API 在同一事务写 CONSUMED + RUNNING，不执行 handler；重复消费拒绝，遗留 RUNNING 仍 UNKNOWN。失效批准不复活，重规划为新调用。
- Windows Python 3.14.3：新增权限 / 审批 66 项全部通过；定向集 87 passed、1 deselected；完整 tests/unit 543 passed、12 skipped、4 failed；LoopController integration 35 passed。main 同四项复核 2 failed、2 passed，未出现新增回归。完整单测原有 Provider fixture / schema 文本断言及 Worktree CRLF oracle 两项问题的归因保留，禁止修改 oracle 或断言掩盖失败。
- 精确字段、合法转换、验证证据及接入边界以 docs/evolution/tool_registry.md 第七阶段 A 记录为准，已回填真实功能提交 SHA。下一阶段才协调 ASK 暂停、用户交互、当前上下文重新核验与恢复；权限记录不承担用户认证、沙箱或副作用补偿。

## 2026-10-11 当前现场：第六阶段 SQLite Checkpoint

- 第六阶段整合基线是已包含前五阶段的本地 main `9f6a313cfe75e3c1d9a07db109cdf4d83d50a383`；第五阶段实现为 `f6ff2571fa2a8f0d826a7a83a1a8ea6b21bfd8a1`。
- 当前分支 `codex/sqlite-checkpoint-recovery`，Worktree `D:\02_study\code\AgentProject\mini-claude-worktrees\sqlite-checkpoint-recovery`；功能提交为 `d892ed3d27c3269bc6843d959634a0e4b11c80d2`，提交说明为 `feat: 引入 SQLite Checkpoint 与保守故障恢复`。用户已授权仅以 fast-forward 整合至本地 main，不推送、不删除 Worktree、不进入 HITL。
- 先完成独立 Store / 故障协议并测试，再接入既有 RunState / Snapshot / dispatch 边界。SQLite schema v1 只有 runs / calls 两表，WAL + FULL，版本 CAS；结果、messages 与游标同一事务保存。执行前可靠提交 RUNNING，遗留 RUNNING 保守归 UNKNOWN，不自动重放。
- 默认内存 / JSON v1 路径保留；checkpoint_enabled=True 或 enable_checkpoints() 显式启用。resume_checkpoint(run_id) 使用内核文件锁持锁读取、核验和继续。持久化投影是绑定数据库的 JSON v2，不允许通过普通 Snapshot 入口绕过锁。后台任务 / 子 Agent 在该模式拒绝启动。
- Store 提交异常在新连接核验 last_operation；无论已提交、未提交还是无法判断都停止当前实例。恢复必须新建实例；Run 被另一实例推进后旧内存 CAS 拒绝继续。工具原调用、结果前缀、当前目录、工作区摘要、实现 / 配置和当前能力按既有验证器检查，不继承旧授权 / 完整环境。
- Windows Python 3.14.3 最终完整 tests/unit：477 passed、12 skipped、4 failed；新增 Checkpoint 58 项全部通过。Snapshot / RunState / 消息闭环与 Checkpoint 的非进程故障定向集 164 passed、10 deselected；LoopController integration 35 passed。独立进程测试使用临时数据库、Mock Provider 与可观察副作用，包含 PREPARED、RUNNING、结果事务前后、终结后退出、受控暂停和活 handler 并发排斥。无付费模型或 Terminal-Bench。
- 完整单测四项已知失败仍保留：Provider fixture / read_file schema 文本断言在未修改 main 同样失败；Node / Java oracle 的本 Worktree CRLF 哈希与 HEAD 的 LF 不同，在 main 对应测试通过。测试修改的是临时副本；原 oracle 未修改，未删除断言。Linux / POSIX 实际验证尚未执行。
- 实际契约、证据与保守恢复限制以 docs/evolution/tool_registry.md 为准，Evolution 已回填真实功能 SHA，由独立文档提交保存。仍没有 HITL 审批、自动 UNKNOWN 解除、数据库迁移、跨机器锁或外部副作用事务保证。

## 2026-10-01 当前现场

- System Prompt 第一阶段重构已进入 main：新增纯函数 `src/core/prompt_builder.py`，Agent 只通过 `refresh_system_prompt()` 刷新；CLI 改用公开方法。Prompt 收敛为 identity、environment、execution policy、skills 区块，回答语言策略已从 Preflight context 移到 identity；动态 hot context 和请求消息构造未变。代表性输入下旧/新 Prompt 为 5,788/2,174 字符，估算 1,231/438 tokens。详见 `docs/evolution/context_management.md`。
- CLI 命令边界重构已提交并推送：Console parser 与 Agent-bound 命令位于 `src/cli/`，REPL 入口负责 slash command dispatch，`MiniClaudeAgent.chat()` 不解析命令；移除了 Agent 底部重复的旧 main/demo 入口。同步修复 `/clear`、malformed command、`/tasks` 非法状态，以及 `/features` 状态切换后的 skills discovery / system prompt 更新。详见 `docs/evolution/cli_command_boundary.md`。

- Tool 层共享 schema 已收敛到 `src/core/tools/definitions.py`；SubAgent 使用独立 `ToolRegistry`，EXPLORE 只读，GENERAL/PLAN/REVIEW 保持四个基础工具且没有 `task`。TodoWrite 第一阶段已恢复到 MainAgent registry：只作为每次 `run()` 范围内的轻量进度状态，动态状态临时注入，不再每三轮重复催更。稳定 System Prompt 已加入复杂多步骤任务的 TodoWrite 使用策略；定向 Prompt、Todo runtime 与 Registry 测试共 32 项通过。之后对同一个 Terminal-Bench case 的唯一 trial 达到 Harbor 1800 秒上限，32 次请求开始、49 次工具调用，仍为 0 TodoWrite、0 workspace mutation，reward 0.0。该样本没有显示 Prompt 策略促使模型调用 TodoWrite；Token usage 未从超时 Trace 中取得。详见 `docs/evolution/tool_registry.md`。

- 已将上下文与工具输出核心修复移入 `main`：完整请求预算估算、显式 1M/250K/500K 配置、Provider usage 与缓存命中 Trace、压缩事务和有界工具输出。
- Agent Note 继续每轮临时注入；自动压缩先提醒再执行。整合时修复了提醒状态与本轮动态上下文不同步的问题。
- Provider 输出达到上限时，Runtime 记录 `finish_reason` 和实际 usage，以 `PROVIDER_OUTPUT_LIMIT` 结束，不发布截断文本或执行截断工具调用。
- Harbor 适配器和 headless 入口已移入 `main`，可直接从当前 checkout 运行 Terminal-Bench 单任务。默认 Provider 请求超时为 60 秒，OpenAI-compatible 客户端允许一次重试并正确分类超时。
- OpenAI-compatible 主 Provider 现默认使用流式请求；响应仍在 Provider 内完整组装后再交给 Agent，Trace 记录每轮是否启用流式传输。此功能不是 UI 逐 Token 展示。`llm.stream: false` 可切回非流式。
- 2026-09-29 的 `make-mips-interpreter` 单次试跑使用整合前的 20 秒配置，第 10 次请求超时，Harbor reward 0.0；前 9 次未见输出上限截断或压缩。原始结果在 `sandbox/terminal_bench_runs/main-context-mips-20260929-1/`。新超时配置尚未进行付费 Provider 复跑。
- `configs/default.yaml` 的 `llm.max_tokens` 已从 8000 调到 32768。同一 `make-mips-interpreter` case 单次重跑 reward 0：32 轮、50 次工具调用，末轮 `finish_reason=length` 且 completion/reasoning 均为 32768；全程 706051 prompt、83530 completion tokens，无压缩和文件写入。Verifier 3 项均失败，因 30 秒内未生成 `/tmp/frame.bmp`。Job 为 `benchmark/harbor/jobs/main-max-tokens-32768-mips-20260929/`。单次结果只说明增加上限未解决该任务，不代表成功率结论。压缩后尚无实际窗口余量硬性检查。
- 随后只修改了 `src/agent/mini_claude_agent.py` 的 System Prompt，加入通用的“缺失信息是否阻塞下一步实现”判断，并调整行为任务的 Runtime Verification 表述；其他 Runtime、模型和压缩配置未动。Prompt 策略测试及 Agent Note 相关测试 4 项通过，`git diff --check` 通过。同一 case 只复跑 1 次：37 轮、55 次工具调用、997825 prompt、176112 completion、169721 reasoning tokens，无压缩、无 mutation、无 `write_file` / `edit_file`，末轮 `finish_reason=length`，Runtime `PROVIDER_OUTPUT_LIMIT`，Harbor 0 exception / reward 0.0。前 36 轮约 136953 reasoning tokens 均在检查；没有实现或运行反馈。Verifier 3 项失败，均因 `/tmp/frame.bmp` 未生成。说明本次单次样本没有显示 Prompt-level guidance 改善 Analysis → Action 转换；不要据此推断总体成功率。Trace 在 `benchmark/harbor/jobs/main-implementation-strategy-mips-20260929/make-mips-interpreter__uKey5ci/agent/mini-claude/traces/task_13ff2b2c.json`，演进记录见 `docs/evolution/context_management.md`。
- 应用 `feat/structured-context-memory` 上的实现后，当前 main 默认开启结构化近期文件记忆：只合并记忆模块，不合并分支上其他改动；Agent Note、压缩提醒和流式请求保留。memory、Prompt 与 Agent Note 相关确定性测试 21 项通过。第一次 Harbor 启动在依赖安装时失败，Agent 未启动；之后仅有 1 个有效 trial。结果：34 轮、50 次工具调用、940572 prompt、175034 completion、163360 reasoning tokens，压缩 0，末轮输出上限，Harbor 0 exception / reward 0。9 次 read_file 成功；第 23/25/27 轮写入并运行了三个分析脚本，未生成目标 `vm.js`，也未运行 `node vm.js`。Verifier 3 项因 `/tmp/frame.bmp` 缺失失败。相较 memory-off Prompt-only 的单次样本少 3 轮和 5 次工具调用，但未完成目标，不能把差异归因于记忆。Trace 在 `benchmark/harbor/jobs/main-context-memory-on-mips-20260930b/make-mips-interpreter__SK7VwAN/agent/mini-claude/traces/task_1889d301.json`。详情见 `docs/evolution/context_management.md`。
- 长期背景与证据见 `docs/evolution/context_management.md`。
- Provider 传输演进见 `docs/evolution/provider_transport.md`；当前流式请求尚未在 main 上做真实 Provider Benchmark。
- 循环治理已降低归一化意图导致的误拦截；不同 grep/sed 范围可以继续执行，只有状态振荡在给过重规划机会后仍会硬停止。治理 Trace 区分 observation 变化、实际 workspace/verification 进展，并记录工具耗时。
- Bash 工具结果现在记录可见性元数据；阈值为 200 行/4000 字符。Task Trace 记录请求配置，`--debug flow` 概括请求轮次、工具结果和输出截断比例，不展开输出内容。
- 本轮相关循环治理、flow、工具可见性、Trace 与流式 Provider 测试合计 101 passed，8 个依赖 pytest 临时目录的用例未运行；完整 `test_runtime_context` 集成测试受 Windows pytest 临时目录 ACL 阻挡，不能声称全套通过。无付费 Provider 运行。
- Tool Observation Reliability 已提交为 `8c7af3e`：POSIX 单行 `;` 链仅在保守语法门槛下采集分段退出码；Runtime 用结构化 segment code 向模型展示部分失败。`search_code` 的路径权限、确定性顺序、exact-cap 判断、整块输出和跳过/截断可见性已加强；MainAgent 文件观察 handler 保留 ToolResult。Windows 定向回归 117 passed、10 skipped、7 deselected，另直接验证 Windows CMD 状态掩盖；Docker Python 3.12.15 Linux 回归 160 passed、1 skipped、1 deselected，POSIX shell 与 symlink 场景均通过。一个既有 Provider parse-error 测试 fixture 缺当前配置字段，单独 deselect；Windows Context audit 仍受 pytest Temp ACL 阻挡。详见 `docs/evolution/tool_registry.md`。
- Evolution 记录已分别补到 `tool_deduplication.md`、`context_management.md` 和 `session_trace_evolution.md`；对应实现 Commit 尚待用户创建，标记为 PENDING。
