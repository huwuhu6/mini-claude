# Repository Audit & Snapshot

> 本文合并了原始 `project_audit.md`（基于 Claude Code 只读审计）与 `repo_snapshot.md`（目录结构、代码规模、测试状态）。所有结论应视为"审计视角下的初版判断"，未被审计明确证明的部分统一标记为 `Needs verification`。

## 1. 项目目的

一个模块化的 AI Agent 运行时，源于单体文件 `s_full.py`（740 行）的重构。实现一个 coding agent，具备工具调度、团队协作、任务管理、后台处理、SubAgent、技能加载、上下文压缩、循环防护、追踪可观测性、故障智能分析和运行时工作区绑定等能力。设计目标是像 Claude Code 一样在用户工作目录中运行。

## 2. 目录结构

```text
mini-claude/
├── pyproject.toml              # src-layout 打包配置
├── s_full.py                   # 原始单体实现（740 行）
├── CLAUDE.md                   # Claude Code 项目指引
├── eval_runner.py              # 基准测试运行器（10 个任务）
├── ARCHITECTURE_SNAPSHOT.md    # 旧架构文档（可能过时）
├── configs/default.yaml        # 默认配置
├── requirements.txt            # 依赖列表
├── docs/                       # 项目文档
├── skills/pdf/                 # PDF 技能模块（实验性）
├── src/
│   ├── cli/                    # CLI 入口 + 工作区权限
│   ├── agent/                  # Agent 主循环
│   ├── core/                   # 核心系统
│   │   ├── runtime_context/    # 工作区绑定 + ShellSession + PathResolver + CommandPolicy
│   │   ├── tracing/            # 三层次追踪（TaskTrace→TurnTrace→ToolTrace）
│   │   ├── failure_intelligence/ # 故障智能分析
│   │   ├── evaluation/         # 追踪驱动评测
│   │   ├── tools/base_tools.py # 文件 + bash 工具
│   │   ├── loop_guard.py       # 死循环检测
│   │   ├── subagent.py         # SubAgent 管理
│   │   ├── background.py       # 后台任务
│   │   ├── compression.py      # 上下文压缩
│   │   ├── console.py          # 控制台命令
│   │   ├── teammate_manager.py # 队友管理
│   │   └── messaging/bus.py    # 消息总线
│   ├── models/                 # 数据模型
│   ├── providers/              # LLM 提供者（DeepSeek + Anthropic）
│   └── skills/loader.py        # 技能加载器
├── tests/integration/          # 集成测试（68 个）
├── .team/                      # 队友状态文件
├── .tasks/                     # 持久化任务文件
├── build/lib/                  # 构建产物
└── src/mini_claude.egg-info/   # 安装元数据
```

**代码规模 ~7900 行 / ~43 个文件**

## 3. 主要模块及职责

| 模块 | 文件 | 职责 |
| --- | --- | --- |
| MiniClaudeAgent | src/agent/mini_claude_agent.py | 主 Agent 类：子系统初始化、LLM 循环、工具调度、追踪埋点、故障智能集成 |
| CLI 入口 | src/cli/entrypoint.py | mini-claude [path] [--yes]，工作区解析、非 TTY 守卫、确认提示 |
| WorkspaceAuthority | src/cli/authority.py | primary_root + additional_roots 统一路径权限模型 |
| RuntimeContext | src/core/runtime_context/workspace.py | 运行时上下文聚合 |
| ShellSession | src/core/runtime_context/shell_session.py | 持久化 shell 状态 |
| PathResolver | src/core/runtime_context/path_resolver.py | 路径解析 |
| CommandPolicy | src/core/runtime_context/command_policy.py | 基于正则的命令安全策略 |
| TraceManager/TraceWriter | src/core/tracing/ | 三层次追踪编排与 JSON 持久化 |
| LoopGuard | src/core/loop_guard.py | 死循环检测与强制反思 |
| FailureAnalyzer | src/core/failure_intelligence/ | 11 类故障分类 + 策略指纹 + 记忆 + 升级策略 |
| TraceAnalyzer | src/core/evaluation/ | 6 个过程质量指标 |
| BaseTools | src/core/tools/base_tools.py | 文件读写编辑 + bash |
| SubAgentManager | src/core/subagent.py | 隔离 SubAgent 创建和运行 |
| Compressor | src/core/compression.py | 上下文自动/手动压缩 |
| BackgroundProcessor | src/core/background.py | 异步命令执行 |
| MessageBus | src/core/messaging/bus.py | 文件收件箱通信 |
| TeammateManager | src/core/teammate_manager.py | 持久化队友 + 自动认领 |
| FeatureManager | src/core/features/manager.py | 功能开关 |

## 4. 已实现功能

| 功能 | 状态 | 面试价值 |
| --- | --- | --- |
| CLI 入口与工作区确认 | Implemented / Needs verification | 很高 |
| WorkspaceAuthority | Implemented / Needs verification | 很高 |
| RuntimeContext | Implemented / Needs verification | 很高 |
| ShellSession 持久化 | Implemented / Has known bug | 高 |
| PathResolver | Implemented / Needs verification | 中高 |
| CommandPolicy | Implemented / Needs verification | 很高 |
| 三层追踪系统 | Implemented / Needs verification | 很高 |
| Trace JSON 持久化 | Implemented / Needs verification | 高 |
| Failure Intelligence | Implemented / Needs verification | 很高 |
| FailureMemory | Implemented / Needs verification | 很高 |
| FailureEscalationPolicy | Implemented / Needs verification | 很高 |
| LoopGuard | Implemented / Needs verification | 高 |
| Trace-driven Evaluation | Implemented / Needs verification | 很高 |
| BaseTools 工具调度 | Implemented / Needs verification | 高 |
| 文件读写编辑 | Implemented / Needs verification | 高 |
| SubAgent 系统 | Implemented / Needs verification | 中高 |
| Teammate 系统 | Implemented / Needs verification | 中 |
| MessageBus | Implemented / Needs verification | 中 |
| BackgroundProcessor | Implemented / Needs verification | 中高 |
| Compressor | Implemented / Needs verification | 高 |
| ConsoleCommandSystem | Implemented / Needs verification | 中 |
| FeatureManager | Implemented / Needs verification | 中高 |
| Skills Loader | Implemented / Needs verification | 中高 |
| LLM Providers | Implemented / Needs verification | 中高 |
| 配置管理 | Implemented / Needs verification | 中 |
| 打包配置 | Implemented / Needs verification | 中 |

## 5. 半成品或存在已知问题的功能

| 功能/模块 | 问题 | 影响 | 优先级 |
| --- | --- | --- | --- |
| ShellSession.reset() | `_original_root` 未保存初始根目录，reset 后 cwd 不变 | 运行时上下文不可靠 | P0 |
| eval_runner.py CSV 输出 | `logs/eval_results.csv` 追加未验证 `logs/` 目录存在 | 首次评测可能失败 | P1 |
| src/core/__init__.py 导出 | evaluation、runtime_context、tracing、failure_intelligence 未统一导出 | 公共 API 不清晰 | P2 |
| Rollback State Leak fault injection | mock.patch 较脆弱，未真实覆盖回滚场景 | 测试可信度低 | P1 |
| skills/pdf/ | 复杂、用途窄、测试状态不明 | 面试叙事分散 | P3 |
| 并发安全 | FailureMemory 标注 single-threaded，后台处理器存在竞争条件 | 生产可靠性 | P2/P3 |
| 跨平台路径 | BaseTools.safe_path() 在 POSIX 上行为可能不同 | Windows/POSIX 一致性 | P1 |
| CLI entrypoint 测试 | argparse、非 TTY 守卫、确认逻辑无 pytest | 入口可信度 | P0/P1 |
| WorkspaceAuthority 测试 | 只有手动验证脚本，无 pytest | 核心卖点缺验证 | P0 |
| Authority 与 BaseTools 委托链 | 未验证 authority 启用时 safe_path() 行为变化 | 安全边界局部实现 | P0 |

## 6. 未验证功能（需端到端验证）

| 功能 | 未验证点 | 建议验证方式 |
| --- | --- | --- |
| CLI 主入口 | fresh install 后能否直接运行 | CLI pytest；`pip install -e .` 后 smoke test |
| 工作区确认 | 非 TTY 守卫与 --yes 行为 | monkeypatch 模拟 stdin/stdout |
| WorkspaceAuthority | 是否真正拦截工作区外路径 | 单元测试 primary/additional roots、外部路径、symlink |
| BaseTools + Authority 集成 | 工具层是否使用统一权限模型 | 集成测试 read/write/edit/bash 路径访问 |
| Trace 持久化 | 是否完整记录 task/turn/tool 生命周期 | 运行 one-shot 任务检查 .traces/*.json |
| Failure Intelligence | 分类、策略指纹、升级是否在真实失败中被调用 | 设计真实失败任务 |
| Evaluation Runner | 10 个基准任务能否稳定运行并生成 CSV | 创建 logs/，运行 py eval_runner.py |
| SubAgent | 是否真实接入主 Agent 任务循环 | 构造 delegated reasoning 任务 |
| BackgroundProcessor | 资源清理、异常传播、并发冲突 | 并发 + 失败任务测试 |
| Compressor | 自动压缩阈值可触发、压缩后上下文可用 | 大上下文回归测试 |
| ProviderManager | DeepSeek ↔ Anthropic 切换可靠 | mock provider 层 |
| Skills Loader | YAML frontmatter 异常格式处理 | malformed SKILL.md 测试 |

## 7. 疑似废弃、实验性或应清理内容

| 路径/模块 | 原因 | 建议 |
| --- | --- | --- |
| s_full.py | 模块化重构已完成，原单体仍存在 | 标记 deprecated 或移入 legacy/ |
| build/lib/ | pip install 构建产物 | .gitignore |
| src/mini_claude.egg-info/ | pip install 元数据 | .gitignore |
| skills/pdf/ | 实验性，与 Coding Agent 主线弱 | 标记 experimental |
| scripts/test_llm.py | 孤立脚本 | 纳入 smoke test 或归档 |
| example/normalize_messages.txt | 孤立示例 | 删除或补充说明 |
| ARCHITECTURE_SNAPSHOT.md | 创建于 RuntimeContext 之前，可能过时 | 用 docs/01-arch/architecture.md 替代 |
| .team/teammate_*.json | 运行时数据 | .gitignore |
| .tasks/task_*.json | 运行时数据 | .gitignore |

## 8. 重要入口点

| 入口点 | 命令 | 说明 |
| --- | --- | --- |
| CLI（推荐） | mini-claude [path] [-y] | cli.entrypoint:main，含工作区确认 |
| 传统 REPL | py s_full.py | 原始单体版本 |
| 模块直接运行 | py -m src.cli.entrypoint . --yes | 开发用 |
| Agent main() | py src/agent/mini_claude_agent.py --one-shot "..." | 旧入口，无工作区绑定 |
| 评测运行器 | py eval_runner.py | 10 个基准测试任务 |
| 测试 | py -m pytest tests/integration/ | 68 个集成测试 |

## 9. 配置和环境要求

**环境变量**: DEEPSEEK_API_KEY（默认）/ ANTHROPIC_API_KEY（备选）/ ANTHROPIC_BASE_URL / MODEL_ID

**Python**: 推荐 `py`（3.14.3），不推荐 `python`（3.8.6，SSL 问题）

**配置文件**: configs/default.yaml（Agent 名称/版本、LLM 选择、6 个功能开关等）

**依赖**: anthropic>=0.25.0, openai>=1.0.0, pyyaml>=6.0, python-dotenv>=1.0.0

## 10. 现有测试

| 测试文件 | 数量 | 内容 |
| --- | --- | --- |
| tests/integration/test_all_modules.py | 13 | 13 个子系统各一个测试 |
| tests/integration/test_failure_intelligence.py | 28 | 分类、策略指纹、升级、记忆等 |
| tests/integration/test_runtime_context.py | 27 | PathResolver、CommandPolicy、ShellSession 等 |

运行：`py -m pytest tests/integration/ -v`

测试使用 `sys.path.insert(0, .../src)` 而非已安装包，无 API 密钥要求。

## 11. 需要人工澄清的问题

- ShellSession.reset() 的预期行为？当前 `_original_root` 返回当前 cwd 而非初始根目录。
- evaluation 等子包未在 core/__init__.py 中导出——设计决定还是遗漏？
- s_full.py 是否删除或标记废弃？
- skills/pdf/ 是生产级还是实验性原型？
- 是否需要同时维护 DeepSeek 和 Anthropic 两个 provider？

## 12. 当前最高风险

### 12.1 核心安全模型缺少验证
WorkspaceAuthority、BaseTools.safe_path()、CLI 工作区确认和路径委托链缺少完整测试覆盖，削弱项目最核心的可信度。

### 12.2 入口路径过多
CLI、s_full.py、模块直接运行、Agent one-shot 旧入口、eval_runner.py 等多个入口。需明确唯一推荐入口。

### 12.3 "功能很多"但缺少演示闭环
大量 Agent 子系统，但面试最需要可演示闭环：输入任务 → Agent 读文件、执行命令、失败分类、调整策略、写 trace、生成评测指标。

## 13. 建议下一步动作

1. 修复并测试 ShellSession.reset()
2. 为 WorkspaceAuthority 与 BaseTools.safe_path() 添加 P0 测试
3. 为 CLI entrypoint 添加最小 smoke test
4. 固化可演示任务：临时 repo 中完成小代码修改、运行测试、输出 trace
5. 跑通 eval_runner.py，确保 logs/eval_results.csv 可生成
6. 清理或标记 s_full.py、build/、egg-info/、.team/、.tasks/
