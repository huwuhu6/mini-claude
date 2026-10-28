# mini-claude

一个面向本地代码仓库的 coding agent runtime。项目从 `s_full.py` 的单文件原型演进而来，目前的主实现位于 `src/`，重点探索以下问题：

- Agent 如何绑定并约束自己的 workspace
- 如何安全地读写文件、执行 shell 命令并保持工作目录状态
- 如何处理工具调用失败、重复调用、上下文压缩和有限重试
- 如何记录结构化 trace，并用评测任务观察 Agent 的执行质量
- 如何在单机环境中组织任务、子 Agent、队友和后台任务

这是一个用于学习和架构实验的项目，不是生产级的 Claude Code 替代品。

## 当前入口

推荐使用模块化 CLI：

```text
mini-claude [path] [-y|--yes]
```

`path` 是 Agent 要操作的 workspace，省略时使用当前目录。启动时 CLI 会解析路径，并在需要时请求 workspace 确认。

`s_full.py` 是早期的单体参考实现，适合对照设计演进，不是当前推荐入口。

## 主要能力

### Runtime 与工具

- `WorkspaceAuthority` 和 `RuntimeContext`：集中管理 workspace、路径解析、shell 会话和命令策略
- 文件工具：读取、写入、编辑和搜索代码
- shell 工具：在受控 workspace 中执行命令，并维护当前工作目录
- 工具调用循环：支持多轮 LLM 响应、工具执行和结果回传

### Agent 可靠性

- `LoopController`、`LoopGuard`：对重复工具调用和异常循环进行限制
- `Failure Intelligence`：对失败进行分类，记录策略指纹，并决定是否升级或终止
- `Compressor`：对长对话进行上下文压缩
- Agent 工作笔记：自动压缩前提醒 Agent 保存关键约束和结论；笔记存于运行数据区，每轮临时注入当前会话
- `TraceManager`：把任务、轮次和工具调用写入运行数据目录的 `traces/`

### 扩展能力

- DeepSeek 和 Anthropic provider 抽象及 provider manager
- JSON 文件持久化的任务和队友状态
- 子 Agent、MessageBus、后台任务和 skills loader
- `eval_runner.py`：基于 `sandbox/tasks/` 的隔离评测运行器
- `compare_reports.py`：比较历史评测 trace 并生成报告

通过 Harbor 运行 Terminal-Bench 2.0 的安装与单任务试跑流程见 [Harbor 集成说明](docs/evaluation/harbor.md)。

这些模块的成熟度不完全相同。CLI、runtime context、工具循环、trace 和评测代码属于当前主线；队友、后台任务、skills 和部分历史兼容逻辑更适合视为实验性或扩展模块。

## 快速开始

### 环境

- Python 3.10+
- 一个可用的百炼 DashScope、DeepSeek 或 Anthropic API key
- Windows、macOS、Linux 均可运行；shell 命令策略会随平台产生差异

### 安装

```bash
python -m venv .venv

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

# macOS / Linux
# source .venv/bin/activate

python -m pip install -r requirements-dev.txt
python -m pip install -e .
```

如果当前终端不在项目根目录，需要先进入项目目录，或者使用依赖文件的绝对路径：

```powershell
Set-Location D:\02_study\code\AgentProject\mini-claude
py -m pip install -r requirements.txt
```

复制环境变量模板并填写 API key：

```bash
# Windows PowerShell
Copy-Item .env.example .env

# macOS / Linux
# cp .env.example .env
```

至少配置以下变量之一：

```dotenv
DEEPSEEK_API_KEY=your_key
ANTHROPIC_API_KEY=your_key
DASHSCOPE_API_KEY=your_key
```

默认 provider、模型、功能开关和运行时目录可在 `configs/default.yaml` 中查看。部分配置也会从环境变量读取。

### 启动 CLI

```bash
# 在当前目录启动，交互式确认 workspace
mini-claude

# 显式指定当前 workspace，并跳过确认
mini-claude . --yes
mini-claude path/to/project -y
```

`mini-claude` 默认把当前目录作为 workspace，并会请求确认；因此日常交互使用直接输入 `mini-claude` 即可。`.` 和 `--yes` 不是必须的：`.` 只是显式表示当前目录，`--yes` 用于跳过确认，适合自动化或已经确认路径安全的场景。

也可以在仓库根目录直接运行模块入口：

```bash
python -m cli.entrypoint . --yes
```

配置优先级：`--config <文件>` → `MINI_CLAUDE_CONFIG` → 用户目录 `~/.mini-claude/config.yaml` → 启动目录 `configs/default.yaml` → Runtime 源码目录的同名配置。建议复制配置到用户目录，或用环境变量明确选择自己的 Provider 配置；显式路径不存在或当前配置的环境变量未设置时报错，不会切换 Provider。只有使用源码安装且没有用户配置时才依赖仓库默认配置，独立安装应准备用户配置。

百炼上的 DeepSeek 应配置 `llm.provider: dashscope` 并使用 `DASHSCOPE_API_KEY`，模型名称与百炼控制台一致。`deepseek` Provider 默认连接 DeepSeek 官方服务，与百炼账户余额不互通。启动会显示实际 Provider / 模型，不显示密钥。

### 模型输出超限后的恢复

OpenAI-compatible 主链路遇到 `finish_reason=length`（或 `max_tokens`、`limit`）时，会丢弃该响应的正文和全部工具调用，使用截断前的消息重新请求当前这一轮。已经完成的工具不会重放，恢复成功后的工具仍经过原来的权限检查和人工审批。恢复耗尽后以 `PROVIDER_OUTPUT_LIMIT` 失败结束。

仓库默认配置的正常输出上限为 8,192 tokens，`llm.output_limit_recovery` 已启用：每轮最多额外请求 2 次，整个 Run 最多 4 次，恢复请求的输出上限为 32,768 tokens。额外请求按“估算输入 + 完整输出额度”预留预算，整个 Run 最多预留 262,144 tokens；实际报告超过预留时补记差额，不因少用、缺少 usage 或重启退还额度。该预算用于限制重试，不是精确费用预测。设 `enabled: false` 可恢复为截断后直接失败，便于对照实验。未使用仓库 YAML 的自定义配置应显式填写这些上限；配置类型的缺省值保持恢复实现时的较宽设置。

`llm.reasoning_effort` 指定初始强度，`llm.supported_reasoning_efforts` 按**从低到高**声明当前部署实际不同、且允许在任务中切换的选择。名称不限制为 low/medium/high/max；不支持强度控制时将初始值设为 null、列表设为空，只调整输出长度。没有思考能力与没有强度参数不是同一件事，应以实际接口为准。恢复每次降低一档，到最低后才在额度内提高输出上限；不会自动升档、切换模型或关闭思考。旧配置的列表曾只用于成员判断，升级时必须检查排列顺序。默认配置针对百炼 `deepseek-v4-flash-0731`，顺序为 `[low, high, max]`。Anthropic 暂不接入自动恢复。

`llm.output_limit_recovery.persist_effort_for_run` 控制是否在同一用户任务中保留已采用的较低强度：仓库 YAML 显式开启，配置类型默认关闭以兼容原有临时调整。只有恢复请求收到完整的正常完成或工具调用响应才记住配置；网络失败、再次截断或未发出的请求不会提交记忆。工具参数仍独立校验、审批，不能因为降档而绕过检查。下一轮只继承强度，不继承扩大后的输出上限；新用户任务从初始配置开始。次数与 Token 预算不因降档重置。

Provider 负责接口合法值及别名。仅 DeepSeek 官方 endpoint 按其文档合并等效名称，例如 medium/xhigh 与 high；其他兼容服务必须声明自己的有效选择，不能据模型名称推断。更换模型或服务时需重新核对声明、顺序及切换限制；接口返回成功也不能证明一个未声明参数实际生效。

每次请求前还会根据完整请求估算、`compression.context_window_tokens` 和默认 2,048 tokens 的安全余量限制输出空间。确实不足时最多尝试一次现有压缩，之后重新估算；usage 缺失不会单独触发压缩。摘要自身被截断时保留原历史。Trace 的每轮 `provider_attempts` 保存各次请求参数、停止原因和 usage，任务总消耗包含失败尝试。持久化模式在额外请求前保存预算；若进程此时退出，恢复会保守跳过已预留的尝试，避免反复重启获得无限重试。

完整 Trace 仍在任务结束时写盘；主循环与压缩摘要的每次 Provider 调用另外立即写入 Session 的 `provider_request_started` / `provider_request_finished`，通过 `request_id` 关联，包含 Run、轮次、尝试序号、请求参数、返回原因及已取得的用量。仅有 started、没有 finished 的请求尚无完成记录，用量未知；不能补成零或凭日志重放请求。恢复事件另保存已用次数和已预留 Token，预算仍以 RunState / Checkpoint 为准。

DeepSeek 兼容 Provider 的 Session 记录只保留服务实际提供的有效用量字段，缺失字段保持未知；`usage_origin=normalized` 表示其他适配器提供的归一化统计，不能冒充原始完整报告。新增请求事件不包含提示正文、响应正文或私有思考内容，错误只记类型。外部超时可能仍没有最终 Trace 或 headless 结果文件，但在正常文件写入条件下，已完成请求的 Session 记录可保留；已知用量的部分合计不能冒充整个任务的总消耗。

### 本地 HITL 审批与恢复

CLI 默认启用审批与 SQLite 会话保存。首次启动自动初始化工作区对应的数据目录，无需了解数据库参数：

```text
mini-claude                 # 当前目录启动，默认审批
mini-claude --continue      # 继续当前工作区最近活动的会话
mini-claude --resume        # 从历史列表选择会话
```

首次普通启动可自动建库；`--continue` / `--resume` 要求已有有效数据库，缺失或损坏时拒绝恢复，不会创建空库或降级执行。高级用法 `--checkpoint-db <工作区之外的数据库路径>` 仍要求显式 `--init-checkpoints` 才允许首次创建自定义库。`--hitl` 保留为兼容参数；`--no-hitl` 明确关闭审批和持久化，不可同时恢复会话。

会话可以包含多个 Run（每次新任务是一个 Run），历史列表按最近活动排序，同一对话只显示一项并标记当前会话。`--resume` 的列表支持 ↑↓ / Enter、Esc 取消与 `/` 搜索；非交互终端使用数字选择。打开历史回显最近的用户和助手消息，工具输出不会重新执行。已结束会话加载消息和笔记作为参考上下文，新输入建立新 Run，不继承旧执行游标；明确保存的本会话规则仍有效。等待审批的会话恢复原批次，不重新生成 Tool Calls。

模型最终回答在交互终端使用 Markdown 渲染（标题、列表、引用、表格与代码高亮）；`NO_COLOR` 关闭颜色但保留排版，重定向输出保留 Markdown 源文。渲染只作用于显示，消息和 Checkpoint 不保存终端样式。默认 OpenAI-compatible API 使用流式传输，但目前 Provider 收齐分片后才交给 Agent，终端尚不逐 Token 展示；Markdown 排版不会改变这一点。

工具操作通过执行控制边界后，使用 Rich 动态状态显示工具与脱敏命令摘要，结束后清除运行态，只留一行结果；长摘要按终端字符宽度省略，避免换行刷屏。完整待审批参数仍在审批详情中查看，等待审批的提议不标记为开始执行。非交互输出只保留结果记录。可用 Ctrl+C 请求停止当前任务，但已启动的子进程不保证随之终止，不应未经核验直接重试。

真实终端在 `WAITING_APPROVAL` 显示一个授权菜单，不再重复打印选项。文件编辑和覆盖展示全文件 diff、真实文件行号及增删统计，预览与执行共用精确匹配规则；无法确认预览时明确显示原因和脱敏参数。Shell 展示完整命令。长预览用 PgUp/PgDn 滚动。初始直接 Enter 保持暂停；用 ↑↓ 主动选择后 Enter 确认，也可直接按 `1` / `y` 允许一次，`2` 为本会话保存规则，`3` 为此工作区保存规则，`4` / `n` 拒绝并结束本轮，`5` 输入替代意见后重新规划。`d` 查看详情，`p` / Esc 暂停，`q` 暂停并退出。Ctrl+C / EOF 保持暂停并退出。菜单等待期间 Runtime 不持执行锁、不请求模型、不执行后续工具。

保存规则需要额外确认。内置 edit_file / write_file 按规范化文件路径联合授权，同一个文件后续修改不同内容不再反复审批，不包含整个目录或 Shell。文件路径仍相对 Workspace 解析；Shell cwd 变化不会把它解释成另一个文件。敏感文件、`.git`、工作区之外的文件以及自定义文件 handler 仅支持单次确认。Shell / 其他操作仍匹配完整参数、cwd、平台、工具和策略版本，不扩展成命令前缀或任意脚本放行。所有规则先服从当前 Feature、路径授权和 CommandPolicy。

本会话规则在退出并恢复同一 Conversation 后保留，新会话不继承；工作区规则跨会话有效。`/permissions` 只展示目标、范围及撤销入口，不倾倒原调用 JSON。旧版精确规则不会自动升级为文件规则。一次性 Approval 始终绑定原始操作，只消费一次；批准并继续仍由原 Runtime API 完成，管道输入保留高级命令协议。

| 命令 | 行为 |
| --- | --- |
| `/resume` | 打开会话选择器 |
| `/sessions [archived]` | 查看当前工作区的会话或归档记录 |
| `/new` | 开始新会话；活动任务未结束时拒绝覆盖 |
| `/status [--details]` | 查看会话完整 ID、当前 Run、模型和实际数据位置；详情包含 Recorder / Trace ID |
| `/permissions [revoke <规则 ID>]` | 查看或撤销授权规则 |
| `/archive <处理原因>` | 归档当前任务，保留执行事实，禁止原操作恢复 |
| `/runs` | 查询保存的 Run ID、状态、Workspace 和当前 Approval ID |
| `/approval <Run ID>` | 查看实际参数、cwd、原因、历史及当前审批状态 |
| `/approve <Run ID> <Approval ID>` | 仅记录批准，不执行工具 |
| `/reject <Run ID> <Approval ID>` | 仅记录拒绝；使用既有固定拒绝说明，不接受自由文本理由 |
| `/resume <Run ID>` | 取得执行独占并复用 Runtime 恢复协议 |

使用高级 `/approve` 命令时，随后显式 `/resume` 才执行原调用；菜单自动完成这两步。高级 `/reject` 保留拒绝后重新规划的协议，菜单的“拒绝并结束”与“换一种方式”明确区分，意见与拒绝决定在同一事务保存。UNKNOWN / INVALIDATED 不提供普通重新批准；经人工检查后可以归档，UNKNOWN 仍是 UNKNOWN，不核销、不补偿、不重试。归档后新计划创建新的会话和调用身份。审批期间普通输入、`/clear` 和 `/compact` 不能覆盖未完成 Run。

退出后重新进入相同 Workspace，可先用 `/runs` 查询，再输入命令；也可启动时显式恢复：

```text
mini-claude path/to/project --continue
mini-claude path/to/project --resume CONVERSATION_ID
```

`--config <配置文件>` 用于明确指定配置；恢复时配置、工具实现、权限与工作区仍需通过 Runtime 的兼容检查。源码版本变化也可能使旧审批失效，本阶段不放宽这个门槛。

默认策略自动允许核实过的内置文件查询、TodoWrite 和 Agent Note，以及极小范围的字面量 Shell 查询（Windows `cd`、POSIX `pwd`、不含展开或复合语法的 `echo`）。文件写入、测试、构建、git、网络和未知工具仍要求审批；不要把 Shell 名称或“测试”标签视为无副作用。持久化模式不会向模型提供不支持恢复的 SubAgent / 后台启动工具，实际执行入口也拒绝它们。

Windows 数据默认位于 `%LOCALAPPDATA%/mini-claude`，Linux 位于 `$XDG_DATA_HOME/mini-claude`（未设置时为 `~/.local/share/mini-claude`），按工作区分目录；可设置 `MINI_CLAUDE_DATA_DIR`。旧的硬编码 D 盘数据不会自动迁移，使用 `--checkpoint-db <旧数据库>` 定位。SQLite v1–v4 自动事务升级为 v5，保留原 runs/calls/approvals；没有记录会话身份的旧 Run 仍通过 `/runs` 查看，不猜测如何分组。

敏感字段及常见命令凭据仅在展示和诊断中脱敏，授权仍绑定原参数；审批详情不写入命令历史或命令结果日志。脱敏不能识别任意秘密，SQLite、Snapshot 和执行 Trace 仍是可信本地数据。源码指纹及整体工作区摘要的严格恢复校验仍保留，编辑无关文件也可能使旧审批失效；细化到操作前置条件属于后续独立改造。HITL 不提供 Shell 沙箱或多用户认证。

### REPL 命令

`/help` 仅列日常命令；配置、兼容命令、实验能力及脚本审批入口见 `/help advanced`。常用命令包括：

| 命令 | 作用 |
| --- | --- |
| `/help` | 查看可用命令 |
| `/new` | 开始新会话 |
| `/resume` | 选择历史会话 |
| `/sessions` | 查看会话列表 |
| `/permissions` | 查看文件或完整操作的授权范围 |
| `/compact` | 手动压缩上下文 |
| `/status` | 查看会话标识、状态和实际保存位置 |
| `/exit` | 退出 |

CLI 使用可编辑的多行输入框：Enter 换行，`Alt+Enter` 提交整段内容；也可以使用 `Esc` 后再按 Enter 提交。粘贴多行文本后可以继续移动光标和修改，不会按第一行提前发送。若没有安装 `prompt-toolkit`，才使用 `/paste` 加 `/end` 的兼容模式。

具体命令以 `src/cli/console.py`、`src/cli/commands.py` 和运行时 `/help` 输出为准。

底部状态栏显示工作区、会话短 ID、审批开关和当前状态；完整标识用 `/status` 查看。每个新任务以分隔线开头，模型请求和执行中状态在原行刷新，仅保留工具完成摘要和最终回答。工具提议在授权前不显示为正在执行。暂停时运行 `/status` 或 `/permissions` 只查询状态，不重复弹出审批菜单。

## 测试与评测

运行集成测试：

```bash
python -m pytest tests/integration -q
```

运行评测任务：

```bash
python eval_runner.py --validate-only
python eval_runner.py --version baseline
python eval_runner.py --version local_experiment --task task_001 --runs 3
```

在 PowerShell 中需要使用反引号 `` ` `` 换行：

```powershell
py eval_runner.py `
  --version local_experiment `
  --task task_001 `
  --runs 3
```

`--validate-only` 只检查任务契约，不启动 Agent。正式运行时，评测任务位于 `sandbox/tasks/`，每个任务由 `config.json`、`baseline/` 和可选的 `verify.py` 组成。评测会创建 shadow workspace，避免直接修改任务的 baseline 文件。

每次运行会在 `sandbox/eval_results/<version>/` 生成：

- `run_manifest_<run_id>.json`：Agent commit、运行环境、任务集哈希和 fixture 哈希；
- `run_results_<run_id>.json`：每个 case 的执行状态、verify 诊断和 Trace 状态；
- `trace_<case>.json` 或带 `_rNN` 后缀的多次运行 Trace。

比较历史结果：

```bash
python compare_reports.py --versions baseline,local_experiment --detail
python compare_reports.py --tasks task_001_db_port,task_004_large_file_edit
```

报告会展示运行条件、任务集一致性、用例覆盖、成功率、轮数、Token、工具调用和失败原因。不同任务集、历史结果缺少 manifest、无 Trace 或 Trace 无效时，报告会明确告警。

评测依赖真实 provider 时需要 API key。项目声明 `tiktoken` 用于 estimated token 估算；它不是当前 DeepSeek 模型的精确 tokenizer。若包或编码资源初始化失败，压缩模块会自动回退到粗略估算。

### 用评测结果优化 harness

建议固定一个任务子集和运行次数，先建立基线，再一次只改一个 harness 机制：

1. 运行 `--validate-only`，确认任务契约没有问题。
2. 用 `--runs 3` 或更多重复运行，记录成功率、平均轮数、Token 和耗时。
3. 用 `compare_reports.py --detail` 对比基线和改造版本。
4. 先看是否改变任务成功率，再看轮数和 Token；最后结合 Trace、LoopGuard、压缩次数和 verify 输出定位原因。
5. 将结论和失败样本记录到本地 `docs/evolution/`，再决定下一轮只改 runtime、工具、上下文或可靠性策略中的一个方向。

不要把“工具调用更少”直接等同于更好：必须同时确认成功率没有下降，并检查是否出现未覆盖、无 Trace 或 verify 失败。

## 目录结构

```text
mini-claude/
├── src/
│   ├── cli/                 CLI、workspace 确认与权限边界
│   ├── agent/               MiniClaudeAgent 与精简 Agent
│   ├── core/
│   │   ├── runtime_context/ workspace、路径、shell、命令策略
│   │   ├── tools/            文件和 shell 工具
│   │   ├── tracing/          结构化 trace
│   │   ├── failure_intelligence/ 失败分类与恢复策略
│   │   ├── evaluation/      trace 指标
│   │   ├── messaging/       MessageBus
│   │   └── ...               任务、子 Agent、后台和功能管理
│   ├── models/               配置、任务、队友和 todo 数据模型
│   ├── providers/            LLM provider 抽象及实现
│   └── skills/               skills 发现与加载
├── configs/default.yaml      默认配置
├── tests/integration/        集成测试
├── sandbox/tasks/            评测任务 baseline 与验证脚本
├── eval_runner.py            评测运行器
├── compare_reports.py        评测结果比较工具
├── s_full.py                 历史单文件参考实现
└── docs/                     架构、决策、测试和演进记录
```

运行时数据默认写入工作区之外的项目级目录：

```text
<用户数据目录>/mini-claude/<project-name>-<short-hash>/
├── checkpoints.sqlite3  对话索引、Run 快照、执行日志、审批及授权规则
├── sessions/       每次 Runtime 实例的诊断 JSONL，非会话恢复权威来源
├── traces/         任务级 Trace 和评测过程数据
├── tasks/          持久化任务
├── team/           队友状态
├── inbox/          队友消息
├── transcripts/    上下文压缩摘要
└── notes/          会话级 Agent 工作笔记
```

用户数据目录按上述 Windows LOCALAPPDATA / POSIX XDG 规则选择。`/status` 展示已解析的完整路径，显式 `--checkpoint-db` 可以与其他诊断数据分开保存。Conversation ID 标识多轮对话；Run ID 标识一次任务；Recorder ID 标识本次实例诊断日志；Trace ID 标识任务轨迹，不应互相替代。

评测结果仍归档在 `sandbox/eval_results/`。仓库中残留的 `.tasks/`、`.team/`、`.traces/` 和 `logs/` 是历史运行产物，新代码不会继续写入它们。

## 架构主线

```text
CLI
  -> workspace confirmation / authority
  -> MiniClaudeAgent
      -> ProviderManager
      -> RuntimeContext
          -> PathResolver / ShellSession / CommandPolicy
      -> BaseTools
      -> LoopController / LoopGuard
      -> Failure Intelligence
      -> TraceManager
      -> optional: SubAgent / Team / Background / Skills
```

工具调用的核心循环是：LLM 响应 -> 解析 tool calls -> 执行工具 -> 写入 trace -> 把结果交回 LLM。失败时，系统会结合工具错误、循环保护和失败分类决定重试、升级或结束任务。

## 文档

项目的主题演进记录保存在 `docs/evolution/`；每个方向只维护一份文档。当前代码、测试和对应主题文档是主要参考资料。

## 已知问题与边界

- 项目仍是单机、单进程为主的 runtime，不承诺生产级并发、分布式队列或多租户隔离。
- Python API 默认使用内存 RunState，受控暂停可导出 JSON 快照移交新实例；CLI 默认使用 SQLite Checkpoint 和本地审批。SQLite 可以跨进程继续已保存的原始工具批次，RUNNING 而结果未提交的调用进入 UNKNOWN，拒绝自动重试并要求人工核验。持久化模式只支持本机同一数据库的主 Agent 前台工具，拒绝后台任务和子 Agent 执行，不提供通用 exactly-once 保证。Trace 与 Session Recorder 是诊断记录，持久恢复的权威状态来自数据库。
- `s_full.py` 作为历史实现保留；当前正式 Agent Runtime 位于 `src/agent/mini_claude_agent.py`。
- `src/core/compression.py` 使用 `tiktoken` 做 estimated token 估算；包未安装或编码资源初始化失败时会安全回退到粗略估算，不提供 provider-specific tokenizer。Provider 返回的 `usage.prompt_tokens` 才是实际请求用量。
- 评测结果中的过程指标用于工程分析，不等同于通用 Agent 能力排名。

主 Agent 已支持显式启用的持久化 HITL Runtime API，Python API 默认关闭且要求先启用 SQLite Checkpoint；CLI 默认启用。ASK 保存原工具调用并暂停，批准后继续原批次；拒绝会关闭剩余调用后重新规划。可信本地终端已提供审批、查询与显式恢复入口；SubAgent 和直接工具 API 不在此保护范围内。详见 [工具与 Runtime 演进](docs/evolution/tool_registry.md)。

开发接口示例（审批决定由调用方显式提供）：

```python
agent.enable_checkpoints(checkpoint_path)
agent.enable_hitl()
agent.run(task)
run_id = agent._run_state.run_id  # 当前内部 RunState 身份
approval = agent.get_pending_approval(run_id)  # 仅在等待审批时查询
agent.decide_approval(run_id, approval.approval_id, approved=user_approved, actor=user_identity)
agent.resume_checkpoint(run_id)  # 重查上下文；不会重新生成待执行调用
```

`actor` 用于审计，不提供身份认证。审批消费后的未知执行状态仍需人工检查，不能通过再次批准自动重试。

## 工程讨论主题

这个项目最适合用来讨论 Agent runtime 的工程问题：workspace 边界、工具调用循环、失败恢复、循环防护、trace/evaluation、模块化 provider，以及从单文件原型迁移到分层架构时的取舍。
## 运行时数据与调试

工作区保持干净。运行时数据默认保存在工作区之外：

```text
<用户数据目录>/mini-claude/<project-name>-<short-hash>/
  sessions/   # 每个 Runtime 实例的诊断事件
  traces/     # benchmark and task traces
  tasks/      # persistent /task data
  team/       # teammate state
  inbox/      # teammate messages
  transcripts/# compressed context summaries
  checkpoints.sqlite3 # CLI 默认启用的对话、快照、执行日志和审批记录
  checkpoints.sqlite3.locks/ # permanent local run lock files
```

可以设置 `MINI_CLAUDE_DATA_DIR` 使用其他数据根目录。

SQLite 模式同时提供上述 CLI 和 Python API：创建 Agent 时传入 `checkpoint_enabled=True`，或在新 Run 前调用 `agent.enable_checkpoints()`（自定义数据库必须在工作区外）。新实例使用相同工作区、数据库及兼容配置，然后调用 `agent.resume_checkpoint(run_id)`；该入口持锁读取、校验并继续执行。完成 Run 的重复恢复只返回已保存结果。新独立 JSON 快照使用 schema v10，持久化投影使用绑定数据库的 v11（Checkpoint-only）或 v12（HITL），保存输出恢复预算与任务内强度记忆，不能走普通 `restore_run_snapshot()`；旧 v1–v9 格式仍可读取，缺少的旧版强度记忆默认为空，不退还已有恢复预算。执行恢复仍须通过源码、配置、实际 Provider 部署及当前权限校验，能读取旧格式不代表能跨代码升级继续旧审批。

同进程继续也校验已提交的暂停边界。待执行文件调用另存实际目标指纹，覆盖工作区摘要忽略的目录及额外授权目录；文件检查不是文件系统锁，不能消除检查后外部进程修改文件的竞态。没有审批绑定的待执行 Shell 缺少通用作用域证明，暂停续跑或导入快照会阻断。现有 PREPARED 调用的显式审批流程保留；审批不证明全部外部环境未变化，也不能放行 UNKNOWN。

工具结果分别保存 `result`（SUCCESS / FAILED / UNKNOWN）与 `effect`（NONE / APPLIED / PARTIAL / UNCERTAIN）。NONE 仅用于能证明 handler 未启动的调用，必须携带匹配的白名单原因码。Runtime 在参数绑定通过后、handler 调用前记录进入执行；此后的嵌套调用异常也不能变成 NONE。Store 再次检查原因码和 PREPARED 阶段；缺少明确效果声明时默认 UNCERTAIN。工具启动后的超时以及副作用不确定的失败，在持久化模式中保留 UNKNOWN 并停止后续执行。

内置文件写入/编辑在 PREPARED 阶段保存执行前指纹与预期内容哈希；`/approval <Run ID>` 展示 UNKNOWN 的文件证据，Python API 为 `get_file_recovery_evidence(run_id, call_id, turn=...)`。证据只区分当前满足预期、与执行前相同、冲突、无法判断，不推断原调用成功或未执行，不解除阻断。旧记录没有证据时显示无法判断。

数据库保存失败时立即停止，该实例不能继续 dispatch 或开始新任务。即使重新核验发现 COMMIT 已成功，也须由新实例从权威数据库恢复。UNKNOWN 没有自动解除或补偿接口；Shell / 文件系统副作用与 SQLite 不是同一个事务，父进程退出也不证明它启动的外部子进程已经结束。运行数据可能含任务文本、Note 和工具输出，应按敏感项目数据保护；不保存 Provider 凭据或完整环境。详细提交边界和限制见 [工具与 Runtime 演进](docs/evolution/tool_registry.md)。

不启动 LLM 也可以查看已记录的会话：

```powershell
mini-claude path/to/project --debug latest
mini-claude path/to/project --debug errors
mini-claude path/to/project --debug flow --session <session_id>
```

JSONL 文件是详细记录的来源；`--debug` 是人类阅读入口，用于查看流程、工具失败、循环防护和运行时错误。
