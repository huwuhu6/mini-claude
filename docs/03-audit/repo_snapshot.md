# 1. 项目目的

一个模块化的 AI Agent 运行时，源于单体文件 s_full.py（740 行）的重构。实现一个coding agent，具备工具调度、团队协作、任务管理、后台处理、SubAgent、技能加载、上下文压缩、循环防护、追踪可观测性、故障智能分析和运行时工作区绑定等能力。设计目标是像 Claude Code 一样在用户工作目录中运行。

# 2. 目录结构总览

```text
mini-claude/
├── pyproject.toml              # src-layout 打包配置
├── s_full.py                   # 原始单体实现（740 行）
├── CLAUDE.md                   # Claude Code 项目指引
├── eval_runner.py              # 基准测试运行器（10 个任务）
├── ARCHITECTURE_SNAPSHOT.md    # 架构文档
├── configs/default.yaml        # 默认配置
├── requirements.txt            # 依赖列表
├── docs/workflow.md            # 工作流文档
├── skills/pdf/                 # PDF 技能模块（表单填充）
│
├── src/
│   ├── __init__.py             # __version__ = "1.0.0"
│   ├── cli/                    # 【新增】CLI 入口 + 工作区权限
│   │   ├── __init__.py
│   │   ├── entrypoint.py       # main(): mini-claude [path] [--yes]
│   │   ├── confirmation.py     # 工作区确认提示
│   │   └── authority.py        # WorkspaceAuthority 权限模型
│   ├── agent/
│   │   ├── mini_claude_agent.py # 主 Agent 类（1280 行）
│   │   └── minimal_agent.py    # 精简版 Agent（223 行）
│   ├── core/
│   │   ├── __init__.py         # 导出 8 个核心模块
│   │   ├── runtime_context/    # 工作区绑定 + 持久化 Shell
│   │   │   ├── workspace.py    # RuntimeContext 聚合
│   │   │   ├── shell_session.py# 持久化 cwd 跟踪
│   │   │   ├── path_resolver.py# 相对→绝对路径解析
│   │   │   └── command_policy.py# 基于规则的命令安全策略
│   │   ├── tracing/            # 三层次追踪系统
│   │   │   ├── models.py       # TaskTrace/TurnTrace/ToolTrace
│   │   │   ├── manager.py      # TraceManager 编排
│   │   │   └── writer.py       # JSON 持久化
│   │   ├── failure_intelligence/ # 故障智能分析
│   │   │   ├── models.py       # FailureCategory, FailureSignature
│   │   │   ├── signatures.py   # 规则引擎（11 个分类模式）
│   │   │   ├── analyzer.py     # FailureAnalyzer 门面
│   │   │   ├── memory.py       # 按 task_id 隔离故障记忆
│   │   │   └── policy.py       # 升级策略（3 条规则）
│   │   ├── evaluation/         # 跟踪驱动的评测系统
│   │   │   ├── metrics.py      # 6 个质量指标纯函数
│   │   │   └── analyzer.py     # TraceAnalyzer 加载+计算
│   │   ├── tools/base_tools.py # 文件读写编辑 + bash 工具
│   │   ├── loop_guard.py       # 死循环检测
│   │   ├── subagent.py         # SubAgent 管理
│   │   ├── background.py       # 后台任务处理器
│   │   ├── compression.py      # 上下文压缩
│   │   ├── console.py          # 控制台命令系统
│   │   ├── teammate_manager.py # 队友管理
│   │   ├── messaging/bus.py    # 消息总线
│   │   └── features/manager.py # 功能开关管理
│   ├── models/                 # 数据模型
│   │   ├── config.py, task.py, teammate.py, todo.py
│   ├── providers/              # LLM 提供者
│   │   ├── base.py, manager.py, deepseek.py, anthropic.py
│   └── skills/loader.py        # 技能加载器
│
├── tests/integration/
│   ├── test_all_modules.py      # 13 个子系统测试
│   ├── test_failure_intelligence.py  # 28 个故障智能测试
│   └── test_runtime_context.py       # 27 个运行时上下文测试
│
├── .team/                      # 队友状态文件
├── .tasks/                     # 持久化任务文件
├── build/lib/                  # pip install 构建产物
└── src/mini_claude.egg-info/   # pip install 元数据

```

**代码规模**

| 区域 | 文件数 | 行数 |
| --- | --- | --- |
| src/cli/ | 4 | ~100 |
| src/agent/ | 2 | ~1503 |
| src/core/ | 22 | ~3350 |
| src/models/ | 4 | ~550 |
| src/providers/ | 5 | ~825 |
| src/skills/ | 2 | ~195 |
| s_full.py（原始单体） | 1 | 740 |
| 测试文件 | 3 | ~1300 |
| **总计** | **~43** | **~7900** |

# 3. 主要模块及职责

| 模块 | 文件 | 职责 |
| --- | --- | --- |
| MiniClaudeAgent | src/agent/mini_claude_agent.py | 主 Agent 类：初始化所有子系统、LLM 循环、工具调度、追踪埋点、故障智能集成 |
| CLI 入口 | src/cli/entrypoint.py | mini-claude [path] [--yes] CLI，工作区解析、非 TTY 守卫、确认提示 |
| WorkspaceAuthority | src/cli/authority.py | 统一路径权限模型：primary_root + additional_roots，替换分散的白名单 |
| RuntimeContext | src/core/runtime_context/workspace.py | 运行时上下文聚合：workspace_root、ShellSession、PathResolver |
| ShellSession | src/core/runtime_context/shell_session.py | 持久化 shell 状态：跨调用 cwd 跟踪、命令历史、cd 优化 |
| PathResolver | src/core/runtime_context/path_resolver.py | 路径解析：相对→绝对、~展开、绝对路径直通 |
| CommandPolicy | src/core/runtime_context/command_policy.py | 基于正则的命令安全：阻止 rm -rf、管道炸弹、subshell，允许 && |
| TraceManager | src/core/tracing/manager.py | 追踪编排：TaskTrace→TurnTrace→ToolTrace 三层次生命周期 |
| TraceWriter | src/core/tracing/writer.py | 追踪持久化：漂亮打印 JSON 到 .traces/task_.json |
| LoopGuard | src/core/loop_guard.py | 死循环检测：连续重复检测 + 频率阈值，注入强制反思信息 |
| FailureAnalyzer | src/core/failure_intelligence/analyzer.py | 故障分类门面：11 类 FailureCategory + 策略指纹推断 |
| FailureMemory | src/core/failure_intelligence/memory.py | 按 task_id 隔离的故障计数和策略多样性跟踪 |
| FailureEscalationPolicy | src/core/failure_intelligence/policy.py | 升级规则引擎：3 同类别+低多样性→升级，5 次→必升级 |
| TraceAnalyzer | src/core/evaluation/analyzer.py | 加载追踪 JSON 并计算 6 个过程质量指标 |
| BaseTools | src/core/tools/base_tools.py | 核心工具：bash、read/write/edit_file、list_files |
| SubAgentManager | src/core/subagent.py | 隔离 SubAgent 的创建和运行 |
| Compressor | src/core/compression.py | 上下文压缩：自动压缩（>100K tokens）、微压缩、手动 /compact |
| ConsoleCommandSystem | src/core/console.py | /help、/compact、/tasks 等控制台命令 |
| BackgroundProcessor | src/core/background.py | 后台异步命令执行 + 通知队列 |
| MessageBus | src/core/messaging/bus.py | Agent 间文件收件箱通信 |
| TeammateManager | src/core/teammate_manager.py | 持久化队友管理和自动认领任务 |
| FeatureManager | src/core/features/manager.py | 功能开关管理 |

# 4. 已实现功能

| 功能 | 状态 | 说明 |
| --- | --- | --- |
| CLI 入口 + 工作区确认 | ✅ 完整 | mini-claude [path] [-y]，非 TTY 守卫 |
| WorkspaceAuthority | ✅ 完整 | primary_root + additional_roots，统一路径权限 |
| ShellSession 持久化 | ✅ 完整 | cwd 跟踪、cd 优化（纯 cd 跳过子进程） |
| CommandPolicy | ✅ 完整 | 允许 &&，阻止 5 类危险模式 |
| RuntimeContext | ✅ 完整 | workspace_root、PathResolver、ShellSession 聚合 |
| 追踪系统（三层次） | ✅ 完整 | TaskTrace→TurnTrace→ToolTrace |
| 追踪 JSON 持久化 | ✅ 完整 | .traces/task_.json，从不覆盖 |
| 故障智能分析 | ✅ 完整 | 11 个 FailureCategory、规则引擎 |
| 策略多样性检测 | ✅ 完整 | 语义等效检测（pip install 不同 flags=同一策略） |
| 故障升级策略 | ✅ 完整 | 3 条规则：5 次必升级、3 次+低多样性+用户干预、不可恢复 |
| 循环防护（LoopGuard） | ✅ 完整 | 连续重复 + 频率阈值检测 |
| 追踪驱动的评测系统 | ✅ 完整 | 6 个质量指标、eval_runner.py |
| 工具调度（字典路由） | ✅ 完整 | 7 个工具，dict-based dispatch |
| 文件读写编辑 | ✅ 完整 | safe_path 验证 |
| SubAgent 系统 | ✅ 完整 | 4 种类型、隔离上下文 |
| 队友系统 | ✅ 完整 | 持久化、自动认领 |
| 消息总线 | ✅ 完整 | 文件收件箱通信 |
| 后台任务 | ✅ 完整 | 异步执行 + 通知 |
| 上下文压缩 | ✅ 完整 | 自动+手动 |
| 控制台命令 | ✅ 完整 | /help、/compact、/tasks 等 |
| 功能开关 | ✅ 完整 | 6 个功能可独立启用/禁用 |
| 技能加载 | ✅ 完整 | YAML frontmatter SKILL.md |
| LLM 提供者 | ✅ 完整 | Deepseek + Anthropic 双提供者 |
| 配置管理 | ✅ 完整 | YAML + 环境变量替换 ${ENV_VAR} |
| pyproject.toml 打包 | ✅ 完整 | src-layout，console_scripts |

# 5. 部分或不完整功能

* **ShellSession.reset() 有 Bug**：⚠️ 轻微 Bug _original_root 属性直接返回 self.cwd（只读属性，从未保存初始值），调用 reset() 后 cwd 不变
* **eval_runner.py 的 logs/eval_results.csv**：⚠️ 可能不完整 CSV 追加逻辑在 eval_runner.py:467-476，但从未验证目录是否存在
* **src/core/**init**.py 未导出 evaluation 模块**：⚠️ 缺少导出 evaluation，runtime_context，tracing，failure_intelligence 均未在 core/**init**.py 中重新导出，只能直接导入
* **任务 I（Rollback State Leak）的 fault injection**：⚠️ 脆弱 使用 mock.patch 修改 BaseTools.run_bash，通过 ToolResult(success=False) 模拟失败
* **PDF 技能**：⚠️ 实验性 skills/pdf/ 包含多个脚本和测试，但功能复杂且用途狭窄

# 6. 重要入口点

| 入口点 | 命令 | 说明 |
| --- | --- | --- |
| CLI（推荐） | mini-claude [path] [-y] | cli.entrypoint:main，含工作区确认 |
| 传统 REPL | py s_full.py | 原始单体版本 |
| 模块直接运行 | py -m src.cli.entrypoint . --yes | 开发中，通过 sys.path.insert |
| Agent main() | py src/agent/mini_claude_agent.py --one-shot "..." | 旧入口，无工作区绑定 |
| 评测运行器 | py eval_runner.py | 10 个基准测试任务 |
| 测试 | py -m pytest tests/integration/ | 68 个集成测试 |

# 7. 配置和环境要求

**环境变量**

* DEEPSEEK_API_KEY：Deepseek LLM 提供者（默认）
* ANTHROPIC_API_KEY：Anthropic LLM 提供者（备选）
* ANTHROPIC_BASE_URL：Anthropic API 端点
* MODEL_ID：模型标识符

**Python 环境（Windows 特有）**

* py → Python 3.14.3（SSL 兼容性好，推荐）
* python → Python 3.8.6（SSL/TLS 有问题，不推荐）

**配置文件**

* configs/default.yaml：Agent 名称/版本、LLM 提供者选择、6 个功能开关、任务/团队/压缩/后台/技能/日志配置。

**依赖（来自 pyproject.toml + requirements.txt）**

* anthropic>=0.25.0, openai>=1.0.0, pyyaml>=6.0, python-dotenv>=1.0.0，可选 pathlib2 (< Python 3.12)。

# 8. 现有测试及运行方法

**测试文件（3 个，68 个测试）**

| 测试文件 | 测试数 | 内容 |
| --- | --- | --- |
| tests/integration/test_all_modules.py | 13 | 13 个子系统各一个测试函数：config、tools、features、tasks、message bus、teammate、background、subagent、console、compression, providers, agent |
| tests/integration/test_failure_intelligence.py | 28 | 7 个 TestClass：分类（7）、策略指纹（6）、升级（5）、记忆（3）、ToolTrace 字段（3）、消息生成（1）、pygame 回归（3） |
| tests/integration/test_runtime_context.py | 27 | 6 个 TestClass：PathResolver（3）、CommandPolicy（8）、ShellSession（7）、RuntimeContext（3）、Snake Game 回归（1）、Trace 字段（5） |

**运行命令**

* py -m pytest tests/integration/ -v # 全部 68 个
* py -m pytest tests/integration/test_runtime_context.py -v # 仅运行时上下文

测试使用 sys.path.insert(0, .../src) 而非已安装包，无 API 密钥要求（使用模拟/隔离测试）。

# 9. 缺失测试或可靠性差距

* ShellSession.reset() 测试缺失 — 当前 Bug：_original_root 属性返回实时 self.cwd，非初始值
* WorkspaceAuthority 单元测试缺失 — 仅有手动验证脚本，无 pytest 测试
* WorkspaceAuthority.check() 对外部消费者的影响测试缺失 — 未测试 authority 启用时 BaseTools.safe_path() 的行为变化
* PathAccessPolicy 作为独立概念未测试 — 无集成测试验证 authority 与 BaseTools 的委托链
* entrypoint.py 缺少测试 — argparse 解析、非 TTY 守卫、确认逻辑均未通过 pytest 覆盖
* 并发安全 — FailureMemory 注释标注 "currently single-threaded"；后台处理器可能创建竞争条件
* 跨平台路径问题 — base_tools.py 的 safe_path() 中 (self.workdir / path).resolve() 在 POSIX 上对绝对路径 path 的行为可能不同
* eval_runner CSV 写入 — 未验证 logs/ 目录是否存在，可能在首次运行时失败

# 10. 潜在死代码或实验性代码

* build/lib/ 和 src/mini_claude.egg-info/ — pip install -e . 的构建产物，应添加 .gitignore 排除
* s_full.py — 原始单体（740 行），模块化重构已完成，可能是遗留引用
* skills/pdf/ — 大型 PDF 表单填充技能（多个脚本、LICENSE），可能是实验性添加
* scripts/test_llm.py — 独立脚本，未集成到测试套件中
* example/normalize_messages.txt — 孤立示例文件
* ARCHITECTURE_SNAPSHOT.md — 旧架构快照，可能已过时（创建于 RuntimeContext 之前）
* .team/teammate_*.json 和 .tasks/task_*.json — 之前会话的持久化状态，非测试数据

# 11. 需要人工澄清的问题

* ShellSession.reset() 的预期行为是什么？ 当前 _original_root 属性存在 Bug（返回当前 cwd 而非初始根目录），reset() 实际上是空操作。是否需要修复？
* evaluation 等核心子包未在 core/**init**.py 中导出 — 是设计决定还是遗漏？当前只能通过 from core.evaluation import ... 直接导入。
* s_full.py 是否应删除或标记为已弃用？ 单体原始实现与模块化版本共存，没有集成测试覆盖。
* build/ 和 egg-info/ 目录是否应 .gitignore？ 它们是 pip install -e . 的产物，当前已提交。
* skills/pdf/ 是生产级功能还是实验性原型？ 包含 10 个脚本 + LICENSE 文件，功能复杂且无相关测试。
* 是否需要对 Deepseek 和 Anthropic 两个提供者都维护？ 当前 default.yaml 默认 Deepseek，但两个实现并存。