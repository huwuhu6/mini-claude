# AGENTS.md

本文件定义 Coding Agent 在 mini-claude 仓库中长期遵循的工程约束。

这里只维护稳定、跨任务有效的规则。项目当前能力与使用方式见 `README.md`；当前开发进度见 `docs/HANDOFF.md`；重要技术主线的历史演进见 `docs/evolution/`；Evaluation System 的详细 Contract 见 `docs/EVALUATION.md`。

## 1. 项目定位与主线

mini-claude 是一个面向本地代码仓库的 Coding Agent Runtime，重点研究和实现 Agent Runtime 的工具调用、Workspace 边界、上下文管理、失败恢复、循环治理、Trace、Evaluation 和可观测性。

当前主实现位于 `src/`。修改代码前先确认目标模块和真实调用链，不要因为文件名或历史实现看起来相关就直接修改。

主要模块包括：

- `src/agent/`：主 Agent Loop 与执行流程。
- `src/core/runtime_context/`：Workspace、路径、Shell Session、Command Policy。
- `src/core/tools/`：文件、Shell 等工具。
- `src/core/tracing/`：执行 Trace。
- `src/core/failure_intelligence/`：失败分类、恢复与治理。
- `src/core/evaluation/`、`eval_runner.py`、`compare_reports.py`：Evaluation System。
- `src/providers/`：模型 Provider 与消息协议转换。
- SubAgent、Team、Background、Skills 等其他模块按具体任务调查后再修改。

`s_full.py`、历史 Agent 实现以及兼容代码主要用于理解项目演进，不应默认视为当前主链路。

当文档与当前代码产生冲突时，应通过代码、配置、测试和实际运行行为确定当前事实，并同步修正文档。不要为了符合过期文档而修改正确代码。

## 2. 开发工作原则

开始开发前先执行 `git status --short`，识别用户已有的未提交修改。不得覆盖、回退或顺手整理与当前任务无关的用户改动。

调查问题时优先通过搜索定位符号、调用链和相关测试，再按需读取具体文件。不要为了理解局部问题无差别扫描整个仓库，也不要重复读取已经获得的上下文。

修改遵循最小充分原则：一次解决一个明确问题，优先利用现有抽象和机制。除非当前任务本身要求架构调整，否则不要顺手重构无关代码，也不要仅为了“更优雅”增加新的抽象层。

修改前应检查相关测试、配置以及对应的 `docs/evolution/` 主线记录，避免重新实现已经验证失败或明确放弃的方案。

涉及 Agent 行为、可靠性、Token、工具调用、循环治理、上下文管理或 Evaluation 的“优化”，不能只根据代码观感判断。应尽可能使用测试、Trace、JSONL、Benchmark 或其他可重复证据验证。

## 3. Python 与本地开发环境

开发机器可能同时安装多个 Python Runtime，不允许根据历史文档、绝对路径或之前会话假设当前 Python 版本。

执行 Python 相关开发、测试或脚本前，首先执行：

```powershell
py --version
```

将该命令当前解析到的 Python Runtime 作为本轮默认解释器，不得硬编码 `D:\python...` 等本机解释器绝对路径。

如果版本与预期不符、任务与 Python 版本有关，或者需要确认机器上安装的其他 Runtime，再执行：

```powershell
py --list
```

必要时再显式选择具体 Runtime。不得因为机器中同时存在多个 Python 版本而自行切换。

项目要求 Python 3.10+。Windows 开发环境中的 Python 命令统一优先使用：

```powershell
py -m pip ...
py -m pytest ...
py eval_runner.py ...
```

如果当前环境不是 Windows 或不存在 `py`，先确认实际解释器及版本，再使用该环境提供的 Python 命令，不要机械执行 Windows 专属命令。

## 4. 测试与验证

代码修改后至少执行与改动直接相关的测试。优先运行最小相关测试集；修改 Agent Loop、Runtime Context、Tools、Trace、Failure Intelligence 等共享基础设施时，应根据影响面扩大验证范围。

不能把“代码能够导入”“没有语法错误”或“Agent 最终文本声称成功”当作功能验证。

Unit Test、Integration Test、静态检查以及不消耗真实模型额度的 deterministic validation 可以主动运行。

真实 Provider Benchmark、批量 Evaluation 或其他会消耗 API 配额、产生正式实验数据的操作，不默认启动。需要运行时说明目的和命令，由用户决定是否执行。

Benchmark 的 `--validate-only`、Fixture 静态检查和其他不启动真实 LLM 的低成本验证可以主动执行。

没有实际执行的测试不得声称通过。由于网络、Provider、余额、依赖或运行环境导致无法验证时，必须明确区分代码失败与 Infrastructure Failure。

## 5. Evaluation 与 Benchmark

Evaluation 是 mini-claude 的核心工程基础设施，用于验证 Runtime 修改后的任务正确性、执行行为、可靠性和成本变化。

主要入口：

- `eval_runner.py`：运行 Evaluation。
- `compare_reports.py`：比较评测结果。
- `src/core/evaluation/`：指标与结果分析。
- `sandbox/tasks/`：Benchmark Case。
- `sandbox/eval_results/`：Evaluation Result。
- `docs/EVALUATION.md`：Evaluation System 的使用方式、运行流程、结果解释和工程约束。

当任务涉及以下任一行为时，在执行或修改前必须先阅读 `docs/EVALUATION.md`：

- 运行 Evaluation；
- 分析或比较评测结果；
- 根据 Benchmark 判断 Runtime 改造效果；
- 修改 Evaluation Harness；
- 修改 `eval_runner.py`、`compare_reports.py` 或 `src/core/evaluation/`；
- 新建、修改或审核 Benchmark；
- 修改 Shadow Workspace、Verifier、Fixture、Manifest、Trial Ledger 等评测基础设施。

普通 Runtime 开发不要求预先读取完整 Evaluation 文档。只有实际进入 Evaluation / Benchmark 工作时再按需加载。

Benchmark 用于测量 Runtime 能力。不得为了让现有 Case 通过而针对 Task ID、Fixture 名称、固定错误文本、Verifier 实现或单个 Case 在 Runtime 中增加特殊逻辑。

需要设计、构造、系统性修改或审核 Benchmark 时，在阅读 `docs/EVALUATION.md` 的基础上，使用 `.agents/skills/miniclaude-benchmark-author/` 中的 Benchmark Author Skill。

## 6. Evolution 开发记录

`docs/evolution/` 是 mini-claude 的长期工程演进记录，用于保存重要问题、方案、实验、工程决策和最终取舍，而不是普通 Change Log。

Evolution 按长期技术主线维护，而不是按每次修改创建文件。同一主线原则上持续维护同一份文档。

可能的主线包括但不限于：

- 死循环治理 / Failure Governance
- 上下文管理 / Compression
- Runtime / Tooling / Workspace
- Evaluation / Benchmark
- UX / CLI / UI
- Provider / Model Protocol
- Observability / Trace / Debug

这些分类不是固定清单。开始开发前先检查 `docs/evolution/` 是否已有对应主线；存在则更新原文档，不存在且当前工作已经形成值得长期维护的独立技术主线时，根据实际情况新建。

普通 Bug Fix、小型测试补充和无行为变化的代码整理通常不需要 Evolution 记录。涉及架构、重要机制、关键行为变化、实验结论、失败方案、重要 Trade-off 或后续开发需要理解的历史背景时，应维护对应主线。

### 6.1 写作要求

Evolution 文档必须通俗、直接，并且能够脱离当时聊天记录独立理解。

优先按照下面的因果链描述：

> 当时遇到了什么现象 → 为什么这是问题 → 原因是什么 → 尝试了什么方案 → 实际结果如何 → 为什么最终保留或放弃 → 现在还剩什么问题。

不要把文档写成类名、方法名、Commit Hash 和指标的堆砌。

可以使用必要的工程术语，但项目特有机制第一次出现时，应说明它在实际运行中解决什么问题。

一个不了解本次开发对话、但熟悉 mini-claude 基本架构的开发者，在只阅读对应 Evolution 文档后，应能够理解这条技术主线为什么演变成当前方案，而不需要重新翻 Git Diff 或历史聊天。

代码片段只用于解释关键机制，不要大量复制当前实现；具体实现始终以当前代码为准。

记录指标时必须说明指标测量的对象以及它支持什么结论，不能只记录数字。

不要只记录最终成功方案。有工程价值的失败方案、反例以及被否决设计同样需要保留，避免后续开发重新走已经验证失败的路径。

### 6.2 日期与 Commit

每次形成有意义的工程阶段后，应在对应 Evolution 主线追加记录。

正式记录至少维护：

- 日期：精度到天，统一使用 `YYYY-MM-DD`。
- Commit Hash：与该阶段实现直接关联的 Git Commit，推荐使用短 Hash。
- Commit Description：该 Commit 的提交说明。
- Description：本次解决的问题、核心变化和原因。

涉及实验或方案取舍时，还应记录 Result / Evidence 与 Decision / Limitation。

推荐格式：

```markdown
## 2026-09-11

Commit: `abcdef1`
Commit Description: `refactor: 重构 Progress-aware Failure Governance`

### Description

当时的问题是……

之所以需要修改，是因为……

本次将……调整为……，主要原因是……

### Result / Evidence

测试结果……

Benchmark / Trace 表明……

### Decision / Limitation

最终保留……方案，因为……

目前仍然存在……
```

Commit Hash 不得猜测或伪造。

Git Commit Hash 依赖 Commit 内容，因此不能要求一个 Commit 在自身内容中记录自己的最终 Hash。推荐流程：

```text
实现代码
→ 用户创建实现 Commit
→ 获取真实 Commit Hash
→ 回填对应 Evolution 记录
→ 后续文档 Commit 保存该记录
```

如果实现尚未提交，可以先写 Evolution 内容并将 Commit 标记为 `PENDING`，但它只是临时状态；获得真实实现 Commit 后应回填。

历史结论被新证据推翻时，不要直接删除旧记录，应增加新的阶段说明新的证据以及为什么改变决策。

Evolution 回答的是“为什么系统变成今天这样”；当前代码回答“系统现在实际是什么”。

## 7. README、HANDOFF、EVALUATION 与 Evolution 的边界

`README.md` 面向项目使用者和开发者，描述当前项目定位、能力、运行方式、主要架构和使用入口。

`AGENTS.md` 面向 Coding Agent，维护长期稳定的工程约束、开发流程以及文档路由。

`docs/HANDOFF.md` 用于新的 Agent 会话快速恢复当前开发现场，只保存近期状态，例如：

- 当前正在处理什么；
- 最近完成了什么；
- 当前关键实验或验证结果；
- 当前已确认结论；
- 尚未解决的问题；
- 下一步建议从哪里继续。

长期开发规则不得写入 HANDOFF；长期技术演进不得依赖 HANDOFF 保存。

`docs/EVALUATION.md` 是 Evaluation System 的长期使用与工程 Contract。

`docs/evolution/` 保存跨会话、跨版本仍有价值的技术演进与工程决策。

避免同一个事实长期在多个文档中重复维护。存在重复信息时，应明确唯一 Source of Truth，其他文档只提供入口或引用。

阶段性开发完成、当前主线发生明显变化，或者 HANDOFF 中的信息已经失效时，应更新 `docs/HANDOFF.md`，确保下一次新会话可以快速恢复现场。

## 8. Git 与变更边界

默认由用户负责最终 Commit 和 Push。

除非用户明确要求，否则 Agent 不执行 `git commit`、`git push`、`git reset --hard`、强制 Checkout 或其他可能改变用户 Git 历史的操作。

可以主动使用只读 Git 命令调查仓库：

```powershell
git status --short
git diff
git log
git show
```

需要提交时，根据实际修改提供建议 Commit Message。

提交类型优先使用：

```text
feat:
fix:
refactor:
test:
docs:
chore:
```

Commit Description 使用中文，保证仅查看 Git History 就能理解该 Commit 的主要工程目的。

## 9. 语言与代码规范

与用户沟通以及项目内部面向用户的说明默认使用中文。

mini-claude 自身产生的用户可见控制台信息、错误提示和项目日志原则上使用中文；第三方原始错误、外部协议或兼容性要求需要保留原文时除外。

变量名、函数名、类名、模块名等代码标识符使用规范英文命名。

代码注释根据上下文选择中文或英文。注释优先解释“为什么”，避免重复描述代码本身已经清楚表达的行为。

## 10. 任务完成检查

完成开发任务前检查：

1. 修改是否集中在当前问题，没有夹带无关重构。
2. 用户已有修改是否完整保留。
3. 是否执行了与改动匹配的确定性测试或验证。
4. 没有执行的验证是否明确说明。
5. 本次修改是否影响 README、EVALUATION 或 HANDOFF。
6. 本次变化是否属于需要维护的 Evolution 主线。
7. Evolution 中是否存在需要回填的 Commit 信息。
8. 是否因为本次修改产生新的代码与文档事实冲突。

最终向用户说明实际修改内容、验证结果、未执行或被阻塞的验证、已知风险，以及必要时给出建议 Commit Message。