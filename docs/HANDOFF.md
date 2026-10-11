## 2026-10-11 当前现场：第七阶段 B 持久化 HITL Runtime

- 开发基线 / 本地 main 为 `899fa3c247e0d021af2ee752b5b4a3dbc3be5f85`，已包含第 7A 功能 `2518787` 和文档 `899fa3c`。独立分支 `codex/hitl-runtime-integration`，Worktree `D:\02_study\code\AgentProject\mini-claude-worktrees\hitl-runtime-integration`；本阶段尚未提交、合并或推送。
- 主 Agent 显式 `enable_checkpoints()` 后可 `enable_hitl()`，或构造时同时启用二者。默认关闭；不提供 CLI 审批交互，不进入 7C。
- Runtime API：`get_pending_approval(run_id)`、`decide_approval(run_id, approval_id, approved=..., actor=...)`、`resume_checkpoint(run_id)`。等待期间新输入及普通压缩被阻止；新实例恢复 PENDING 也安装 WAITING 生命周期。
- ASK 在同一事务创建审批并保存 WAITING_APPROVAL，当前调用保持 PREPARED，不生成结果 / 不推进游标 / 不计失败，立即释放锁。批准后沿原循环原子消费并标 RUNNING，拒绝则关闭当前及剩余调用后重新规划。
- SQLite v3 事务扩展 runs 的状态 / 边界 CHECK，保留旧 calls 与 approvals；HITL 绑定投影 v3 只能通过 SQLite 恢复。普通 JSON v1 暂停入口继续支持，但不接受审批等待；旧源码指纹兼容门槛不放宽。
- 核心实现与限制见 docs/evolution/tool_registry.md 第七阶段 B；功能提交 SHA 仍 PENDING。最终测试结果以该记录为准，保留四项历史失败及平台限制。

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
