# Project Audit

> 本文基于 Claude Code 对当前 repo 的只读审计结果整理。由于本轮没有直接运行测试、没有打开源码逐行复核，也没有执行 CLI 或评测脚本，因此所有结论应视为“审计视角下的初版判断”。未被审计明确证明的部分统一标记为 `Needs verification` 或 `Unknown`。

## 1. 审计范围

当前项目是一个模块化的 AI Coding Agent 运行时，源自原始单体文件 `s_full.py` 的重构。项目目标是构建类似 Claude Code 的本地工作区 Agent，具备工具调度、任务管理、团队协作、SubAgent、后台处理、上下文压缩、循环防护、追踪可观测性、故障智能分析和运行时工作区绑定等能力。

当前 repo 主要包含：

- `src/cli/`：CLI 入口、工作区确认、权限模型。
- `src/agent/`：主 Agent 与精简 Agent。
- `src/core/`：工具、运行时上下文、追踪、故障智能、评测、LoopGuard、SubAgent、后台任务、消息总线、队友管理等核心系统。
- `src/models/`：配置、任务、队友、TODO 等数据模型。
- `src/providers/`：DeepSeek、Anthropic 等 LLM provider。
- `src/skills/`：技能加载器。
- `tests/integration/`：集成测试。
- `eval_runner.py`：基准评测运行器。
- `s_full.py`：原始单体实现。
- `skills/pdf/`：PDF 表单填充相关实验性技能。
- `.team/`、`.tasks/`：历史运行状态或持久化文件。
- `build/`、`src/mini_claude.egg-info/`：疑似构建产物。

## 2. 当前已实现功能

以下功能在 Claude Code 审计中被标记为已实现，但仍建议在后续通过测试、手动演示或源码复核确认真实可用性。

| 功能 | 状态 | 证据/说明 | 面试价值 |
| --- | --- | --- | --- |
| CLI 入口与工作区确认 | Implemented / Needs verification | `mini-claude [path] [-y]`，包含工作区解析、非 TTY 守卫、确认提示 | 很高。体现本地 Agent 对工作目录和权限边界的控制 |
| WorkspaceAuthority | Implemented / Needs verification | 使用 `primary_root + additional_roots` 统一路径权限模型 | 很高。可讲安全边界、最小权限、路径授权 |
| RuntimeContext | Implemented / Needs verification | 聚合 `workspace_root`、`ShellSession`、`PathResolver` | 很高。可讲 Agent 在用户目录中运行的上下文模型 |
| ShellSession 持久化 | Implemented / Has known bug | 跨工具调用维护 cwd、命令历史、纯 `cd` 优化；但 `reset()` 存在已知 bug | 高。可讲状态保持，也可讲 bug 修复与回归测试 |
| PathResolver | Implemented / Needs verification | 支持相对路径到绝对路径、`~` 展开、绝对路径直通 | 中高。可讲路径解析与跨平台风险 |
| CommandPolicy | Implemented / Needs verification | 基于正则阻止危险命令，如 `rm -rf`、管道炸弹、subshell；允许 `&&` | 很高。可讲 Agent 命令执行安全 |
| 三层追踪系统 | Implemented / Needs verification | `TaskTrace -> TurnTrace -> ToolTrace` 生命周期 | 很高。可讲可观测性、调试、评测数据来源 |
| Trace JSON 持久化 | Implemented / Needs verification | 写入 `.traces/task_.json`，不覆盖旧文件 | 高。可讲审计日志与可复盘性 |
| Failure Intelligence | Implemented / Needs verification | 11 类失败分类、规则引擎、策略指纹推断 | 很高。AI 应用开发岗位中的亮点模块 |
| FailureMemory | Implemented / Needs verification | 按 `task_id` 隔离失败记忆，跟踪失败次数与策略多样性 | 很高。可讲“失败不是只重试，而是分类后调整策略” |
| FailureEscalationPolicy | Implemented / Needs verification | 3 条升级规则：5 次必升级、3 次同类低多样性提示用户干预、不可恢复直接升级 | 很高。可讲安全中止、人工介入边界 |
| LoopGuard | Implemented / Needs verification | 检测连续重复与频率阈值，注入强制反思信息 | 高。可讲 Agentic loop 的失控防护 |
| Trace-driven Evaluation | Implemented / Needs verification | 6 个过程质量指标，`eval_runner.py` 执行 10 个基准任务 | 很高。可讲用过程指标衡量 Agent 行为，而不只看最终答案 |
| BaseTools 工具调度 | Implemented / Needs verification | 字典路由，包含 bash、read/write/edit/list 等工具 | 高。可讲工具抽象与调度 |
| 文件读写编辑 | Implemented / Needs verification | 包含 `safe_path` 验证 | 高。Coding Agent 基础能力 |
| SubAgent 系统 | Implemented / Needs verification | 4 种类型、隔离上下文 | 中高。可讲多 Agent 分工，但需证明真实使用场景 |
| Teammate 系统 | Implemented / Needs verification | 持久化队友、自动认领任务 | 中。概念有趣，但面试中需避免显得过度设计 |
| MessageBus | Implemented / Needs verification | 基于文件收件箱通信 | 中。适合讲简单可靠的本地通信，不宜夸大 |
| BackgroundProcessor | Implemented / Needs verification | 异步执行与通知队列 | 中高。可讲长任务不阻塞交互 |
| Compressor | Implemented / Needs verification | 自动压缩、微压缩、手动 `/compact` | 高。可讲上下文窗口管理 |
| ConsoleCommandSystem | Implemented / Needs verification | `/help`、`/compact`、`/tasks` 等命令 | 中。提升可用性 |
| FeatureManager | Implemented / Needs verification | 6 个功能开关 | 中高。可讲渐进式复杂度与可控实验 |
| Skills Loader | Implemented / Needs verification | YAML frontmatter + `SKILL.md` | 中高。可讲插件化扩展 |
| LLM Providers | Implemented / Needs verification | DeepSeek + Anthropic 双 provider | 中高。可讲 provider abstraction |
| 配置管理 | Implemented / Needs verification | YAML 配置与 `${ENV_VAR}` 环境变量替换 | 中。工程成熟度 |
| 打包配置 | Implemented / Needs verification | `pyproject.toml`，src-layout，console_scripts | 中。可讲从脚本到可安装 CLI 的演进 |

## 3. 半成品或存在已知问题的功能

| 功能/模块 | 问题 | 影响 | 建议优先级 |
| --- | --- | --- | --- |
| `ShellSession.reset()` | `_original_root` 属性返回当前 `cwd`，没有保存初始根目录，导致 reset 后 cwd 不变 | 影响运行时上下文可靠性，可能导致状态泄漏 | P0 |
| `eval_runner.py` CSV 输出 | `logs/eval_results.csv` 追加逻辑疑似没有验证 `logs/` 目录存在 | 首次运行评测可能失败，影响展示 | P1 |
| `src/core/__init__.py` 导出 | `evaluation`、`runtime_context`、`tracing`、`failure_intelligence` 未统一重新导出 | 影响公共 API 清晰度，但不一定是 bug | P2 |
| Rollback State Leak fault injection | 使用 `mock.patch` 修改 `BaseTools.run_bash` 并通过 `ToolResult(success=False)` 模拟失败，较脆弱 | 测试可能没有真实覆盖回滚场景 | P1 |
| `skills/pdf/` | 功能复杂、用途窄、测试状态不明确 | 面试叙事容易分散主线 | P3，除非确定要作为插件化案例 |
| 并发安全 | `FailureMemory` 注释标明 currently single-threaded，后台处理器可能存在竞争条件 | 影响真实生产可靠性 | P2/P3，取决于是否展示并发 |
| 跨平台路径 | `BaseTools.safe_path()` 中 `(self.workdir / path).resolve()` 对绝对路径在 POSIX 上行为可能不同 | Windows/POSIX 一致性风险 | P1 |
| CLI entrypoint 测试 | argparse、非 TTY 守卫、确认逻辑缺少 pytest 覆盖 | 影响最重要入口的可信度 | P0/P1 |
| WorkspaceAuthority 测试 | 目前只有手动验证脚本，缺少 pytest | 权限模型是核心卖点，但缺测试会削弱面试说服力 | P0 |
| Authority 与 BaseTools 委托链 | 未验证 authority 启用时 `BaseTools.safe_path()` 行为变化 | 安全边界可能只是局部实现 | P0 |

## 4. 未验证功能

以下功能审计中出现了实现描述，但当前缺少足够证据证明端到端可用。后续应通过 pytest、CLI demo、trace 样例或手动录屏来验证。

| 功能 | 未验证点 | 建议验证方式 |
| --- | --- | --- |
| CLI 主入口 | `mini-claude [path] [-y]` 是否在 fresh install 后可直接运行 | 添加 CLI pytest；本地执行 `pip install -e .` 后运行 smoke test |
| 工作区确认 | 非 TTY 守卫与 `--yes` 行为是否符合预期 | 用 monkeypatch 模拟 stdin/stdout 与 argv |
| WorkspaceAuthority | 是否真正拦截工作区外路径 | 单元测试 primary root、additional roots、外部路径、symlink 边界 |
| BaseTools 与 Authority 集成 | 工具层是否完全使用统一权限模型 | 集成测试 read/write/edit/bash 的路径访问 |
| Trace 持久化 | trace 是否完整记录 task、turn、tool 的生命周期 | 运行一次 one-shot 任务并检查 `.traces/*.json` |
| Failure Intelligence | 分类、策略指纹、升级策略是否在真实工具失败中被调用 | 设计真实失败任务，例如缺依赖、命令不存在、权限错误 |
| Evaluation Runner | 10 个基准任务能否稳定运行并生成 CSV | 创建 `logs/`，运行 `py eval_runner.py`，检查结果 |
| SubAgent | 是否真实接入主 Agent 任务循环 | 构造需要 delegated reasoning 的任务 |
| BackgroundProcessor | 是否有资源清理、异常传播、并发冲突处理 | 添加并发任务测试与失败任务测试 |
| Compressor | 自动压缩阈值是否可触发，压缩后上下文是否仍可用 | 构造大上下文回归测试 |
| ProviderManager | DeepSeek 与 Anthropic 切换是否可靠 | mock provider 层，避免真实 API key |
| Skills Loader | YAML frontmatter 是否健壮处理异常格式 | 添加 malformed SKILL.md 测试 |

## 5. 疑似废弃、实验性或应清理内容

| 路径/模块 | 当前判断 | 原因 | 建议处理 |
| --- | --- | --- | --- |
| `s_full.py` | 疑似遗留单体 | 模块化重构已完成，但原始单体仍存在 | 标记 deprecated，保留迁移说明；确认无引用后移入 `legacy/` 或删除 |
| `build/lib/` | 构建产物 | 通常不应提交到源码仓库 | 加入 `.gitignore`，从版本控制移除 |
| `src/mini_claude.egg-info/` | 构建/安装元数据 | `pip install -e .` 产物 | 加入 `.gitignore`，从版本控制移除 |
| `skills/pdf/` | 实验性技能 | 与 Coding Agent 主线关系弱，复杂且测试不明 | 暂时标记 experimental；除非转成插件化案例，否则不要作为核心卖点 |
| `scripts/test_llm.py` | 孤立脚本 | 未集成测试套件 | 要么纳入 smoke test，要么归档 |
| `example/normalize_messages.txt` | 孤立示例 | 与主线不清晰 | 删除、归档或补充说明 |
| `ARCHITECTURE_SNAPSHOT.md` | 可能过时 | 创建于 RuntimeContext 之前 | 用新的 `docs/architecture.md` 替代，旧文档标注 obsolete |
| `.team/teammate_*.json` | 历史状态 | 可能是运行时数据 | 加入 `.gitignore` 或改为 fixtures |
| `.tasks/task_*.json` | 历史状态 | 可能是运行时数据 | 加入 `.gitignore` 或改为 fixtures |

## 6. 当前最高风险

### 6.1 核心安全模型缺少验证

项目最有面试价值的部分之一是“Coding Agent 如何安全地操作本地工作区”。但 `WorkspaceAuthority`、`BaseTools.safe_path()`、CLI 工作区确认和路径委托链目前缺少完整测试覆盖。这个风险会直接削弱项目最核心的可信度。

### 6.2 入口路径过多

当前存在推荐 CLI、原始 `s_full.py`、模块直接运行、Agent one-shot 旧入口、`eval_runner.py` 等多个入口。面试中如果解释不清，会显得项目边界混乱。需要明确唯一推荐入口，并把其他入口标记为 legacy、dev-only 或 evaluation-only。

### 6.3 “功能很多”但缺少演示闭环

项目已经包含大量 Agent 子系统，但面试最需要的是可演示闭环。例如：输入一个用户任务，Agent 读文件、执行命令、失败分类、调整策略、写 trace、生成评测指标。如果没有这个闭环，功能列表容易被质疑为堆模块。

## 7. 建议下一步审计动作

1. 修复并测试 `ShellSession.reset()`。
2. 为 `WorkspaceAuthority` 与 `BaseTools.safe_path()` 添加 P0 测试。
3. 为 CLI entrypoint 添加最小 smoke test。
4. 固化一个可演示任务：在临时 repo 中完成小代码修改、运行测试、输出 trace。
5. 跑通 `eval_runner.py`，确保 `logs/eval_results.csv` 可生成。
6. 清理或标记 `s_full.py`、`build/`、`egg-info/`、`.team/`、`.tasks/` 等非核心内容。
7. 将 `docs/architecture.md`、`docs/roadmap.md`、`docs/interview_notes.md` 作为长期文档入口。
