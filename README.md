# mini-claude

一个面向本地代码仓库的 coding agent runtime。项目从 `s_full.py` 的单文件原型演进而来，目前的主实现位于 `src/`，重点探索以下问题：

- Agent 如何绑定并约束自己的 workspace
- 如何安全地读写文件、执行 shell 命令并保持工作目录状态
- 如何处理工具调用失败、重复调用、上下文压缩和有限重试
- 如何记录结构化 trace，并用评测任务观察 Agent 的执行质量
- 如何在单机环境中组织任务、子 Agent、队友和后台任务

这是一个以学习、架构实验和秋招面试展示为目标的项目，不是生产级的 Claude Code 替代品。

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
- `TraceManager`：把任务、轮次和工具调用写入 `.traces/`

### 扩展能力

- DeepSeek 和 Anthropic provider 抽象及 provider manager
- JSON 文件持久化的任务和队友状态
- 子 Agent、MessageBus、后台任务和 skills loader
- `eval_runner.py`：基于 `sandbox/tasks/` 的隔离评测运行器
- `compare_reports.py`：比较历史评测 trace 并生成报告

这些模块的成熟度不完全相同。CLI、runtime context、工具循环、trace 和评测代码属于当前主线；队友、后台任务、skills 和部分历史兼容逻辑更适合视为实验性或扩展模块。

## 快速开始

### 环境

- Python 3.10+
- 一个可用的 DeepSeek 或 Anthropic API key
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
```

默认 provider、模型、功能开关和运行时目录可在 `configs/default.yaml` 中查看。部分配置也会从环境变量读取。

### 启动 CLI

```bash
# 当前目录
mini-claude

# 指定 workspace，并跳过交互确认
mini-claude . --yes
mini-claude path/to/project -y
```

也可以在仓库根目录直接运行模块入口：

```bash
python -m cli.entrypoint . --yes
```

### REPL 命令

常用内置命令包括：

| 命令 | 作用 |
| --- | --- |
| `/help` | 查看可用命令 |
| `/tasks` | 查看任务 |
| `/team` | 查看队友状态 |
| `/inbox` | 查看 Agent 消息 |
| `/compact` | 手动压缩上下文 |
| `/status` | 查看当前运行状态 |
| `/exit` | 退出 |

具体命令以 `src/core/console.py` 和运行时 `/help` 输出为准。

## 测试与评测

运行集成测试：

```bash
python -m pytest tests/integration -q
```

运行评测任务：

```bash
python eval_runner.py
python eval_runner.py --version local_experiment
python eval_runner.py --task task_001
```

评测任务位于 `sandbox/tasks/`，结果默认写入 `sandbox/eval_results/<version>/`。评测会创建 shadow workspace，避免直接修改任务的 baseline 文件。

比较历史结果：

```bash
python compare_reports.py
```

评测依赖真实 provider 时需要 API key。测试环境还需要能正常初始化 `tiktoken` 的编码资源；当前仓库对此存在已知的首次加载问题，见下方“已知问题”。

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

运行后可能出现以下目录：

- `.tasks/`：持久化任务
- `.team/`：队友状态
- `.traces/`：任务 trace
- `logs/`：运行日志或评测汇总
- `sandbox/eval_results/`：评测归档结果

这些目录大多是运行时产物，不应当作为源码阅读入口。

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

项目的主题演进记录保存在本地 `docs/evolution/`，不纳入 Git；每个方向只维护一份文档。当前代码、测试和对应主题文档是主要参考资料。

## 已知问题与边界

- 项目仍是单机、单进程为主的 runtime，不承诺生产级并发、分布式队列或多租户隔离。
- 当前对话状态主要保存在进程内；进程异常退出后，完整 LLM 对话上下文不会自动恢复。
- `s_full.py`、`minimal_agent.py`、模块化 Agent 和评测脚本并存，历史兼容代码仍增加了一定维护成本。
- `src/core/compression.py` 会尝试初始化 `tiktoken`。即使 Python 包已安装，首次初始化也可能尝试联网下载编码资源；在受限网络环境中会导致测试收集失败。
- 评测结果中的过程指标用于工程分析，不等同于通用 Agent 能力排名。

## 适合展示的工程主题

这个项目最适合用来讨论 Agent runtime 的工程问题：workspace 边界、工具调用循环、失败恢复、循环防护、trace/evaluation、模块化 provider，以及从单文件原型迁移到分层架构时的取舍。
