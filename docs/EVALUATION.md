# mini-claude Evaluation System

本文描述 mini-claude Evaluation System 的长期使用方式、核心 Contract、结果解释规则以及修改 Evaluation Harness 时需要遵守的工程边界。

本文主要回答四个问题：

1. Evaluation System 如何工作；
2. 如何正确运行和比较 Evaluation；
3. 如何理解 Evaluation Result；
4. 修改 Evaluation / Benchmark 时哪些边界不能破坏。

本文不承担完整的 Benchmark 设计方法论。设计、构造、系统性修改或审核 Benchmark 时，应进一步使用 `.agents/skills/miniclaude-benchmark-author/`。

如果本文与当前代码发生冲突，以 `eval_runner.py`、`compare_reports.py`、`src/core/evaluation/`、任务配置和测试体现的真实行为为准，同时更新本文。

## 1. Mental Model

mini-claude Evaluation 不只是“运行一个测试”。

一次 Evaluation 的基本链路是：

```text
Benchmark Case
    ↓
准备 Shadow Workspace
    ↓
复制 baseline
    ↓
启动 mini-claude Agent
    ↓
Agent 调查并修改 Shadow Workspace
    ↓
生成 Runtime Trace
    ↓
Agent 结束
    ↓
加载隐藏 Verifier
    ↓
验证最终 Outcome
    ↓
解析 Trace / Runtime Behavior
    ↓
写入 Trial Result
    ↓
生成 Manifest / Report
    ↓
compare_reports.py 进行版本比较
```

Evaluation 同时关心两个维度：

**Outcome**：Agent 最终是否真正完成任务。

**Process / Behavior**：Agent 是如何完成或失败的，包括工具调用、失败恢复、循环、停止决策、Token、轮次、耗时等。

Agent 最终回复“已经完成”不构成 Outcome Success；工具调用更少也不自动意味着 Runtime 更好。

## 2. 核心组件

### `eval_runner.py`

Evaluation 主入口。

负责读取 Benchmark、校验任务 Contract、创建隔离 Workspace、启动 Agent、收集 Trace、执行 Verifier、写入 Result 和 Manifest，并完成运行后的清理。

### `compare_reports.py`

用于比较不同 Evaluation Run。

它不仅比较 Success Rate，还用于检查运行条件是否一致，并分析轮数、Token、工具调用、失败行为以及其他 Evaluation 指标。

### `src/core/evaluation/`

保存 Evaluation 的指标计算和结果分析逻辑。

修改这里意味着可能改变“如何解释一次 Agent Run”，因此修改前必须确认是否会导致历史 Evaluation Result 与新结果失去直接可比性。

### `sandbox/tasks/`

Benchmark Case 的长期存放位置。

每个 Case 提供 Agent 的初始任务环境以及 Evaluation 所需 Contract。

### `sandbox/eval_runtime/`

Evaluation 专用的 deterministic runtime / fixture infrastructure。

它可以用于模拟服务、依赖、资源状态或其他不能安全依赖公网和宿主环境的 Evaluation 条件。

### `sandbox/eval_results/`

保存 Evaluation Run 的 Manifest、Trial Result、Trace 等运行结果。

这些文件是分析真实 Agent 行为的重要证据，不应只根据控制台摘要判断结果。

## 3. Benchmark Case

Benchmark Case 通常位于：

```text
sandbox/tasks/task_<三位序号>_<简短名称>/
```

典型结构：

```text
task_xxx/
├── baseline/
├── config.json
├── verify.py
└── reference_solution/     # 仅在对应 Benchmark Contract 需要时存在
```

### baseline

Agent 开始任务时看到的初始 Workspace。

baseline 应只包含任务真实需要的初始文件，不应包含：

- `__pycache__`
- `node_modules`
- 临时日志
- 本地运行结果
- Evaluation 答案
- Verifier 逻辑
- Reference Solution

### config.json

定义 Benchmark 的基本 Contract 和 Evaluation Metadata。

至少应保证：

- `case_id` 与任务目录一致；
- prompt 有效；
- baseline 存在；
  -声明的 Verifier 文件真实存在；
- Evaluation Metadata 满足对应 Suite 的 Contract。

不要根据历史 Case 机械复制所有字段。字段是否需要存在，应由当前 Evaluation Contract 和 Benchmark Claim 决定。

### Verifier

Verifier 判断 Agent 最终是否真正达到目标 Outcome。

Verifier 默认对 Agent 隐藏，Agent 执行完成后再由 Evaluation Runner 使用。

Verifier 应验证业务结果、系统状态或可观察行为，不应该只检查唯一 Patch、固定代码结构或作者预设的实现方式。

Agent 自己生成的“成功标记”不能代替独立 Verifier。

### Reference Solution

只有对应 Benchmark Contract 需要证明 Case 确实存在合法解法时才需要 Reference Solution。

Reference Solution 的作用是证明：

> 至少存在一种合法结果能够通过当前 Verifier。

它不意味着 Agent 必须采用相同实现，也不能成为 Agent 可见信息。

## 4. Shadow Workspace 与信息隔离

Evaluation 不应该让 Agent 直接修改 Benchmark Baseline。

Runner 创建 Shadow Workspace，将 baseline 复制进去，然后让 Agent 只操作 Shadow Workspace。

基本边界：

```text
Benchmark baseline
        │
        │ copy
        ▼
Shadow Workspace
        │
        ├── Agent 可见并修改
        │
        └── 产生最终 Outcome

Verifier / evaluator-private data
        │
        └── Agent 运行期间不可作为答案暴露
```

Evaluation 结束后，再使用隐藏 Verifier 检查 Shadow Workspace 中的最终状态。

如果 Benchmark 使用 Fixture Controller 或环境变量向 Agent 暴露服务地址、Token、资源状态等信息，应把这些信息视为正常 Environment Evidence，而不是偷偷携带 Expected Label 或标准答案。

设计和修改 Evaluation 时必须始终检查两件事：

**Solvability**：Agent 是否拥有足够真实证据解决问题。

**Isolation**：Agent 是否因为 Evaluation Infrastructure 泄漏而提前知道答案。

不能为了避免 Leakage 把任务设计成只能猜；也不能为了保证可解直接把 Root Cause 或 Expected Behavior 写给 Agent。

## 5. Trace、Result 与 Manifest

Evaluation Result 不是一个单独的 Pass/Fail。

### Trace

Trace 用于回答：

> Agent 实际做了什么？

它可以包含模型轮次、Tool Call、Tool Result、Failure Evidence、Workspace 变化、Progress / Governance 信息以及 Final Status。

分析 Agent 行为问题时，应优先查看相关 Trial 的原始 Trace，而不是只看最终文本。

### Trial Result

Trial Result 保存单次尝试的 Outcome、Verifier、Trace 状态以及 Evaluation 分类。

失败、Crash、缺失 Trace、Infrastructure Failure 等 Trial 不应因为“不方便统计”而静默从结果中删除。

### Manifest

Manifest 描述：

> 这批结果是在什么条件下产生的？

正式 A/B Comparison 时，需要关注：

- Agent Commit；
- Worktree 是否 Dirty；
- Benchmark Suite / Case；
- Task / Config / Baseline / Verifier 等 Fixture Hash；
- Python / Platform；
- Provider；
- Model；
- Temperature / Max Tokens 等模型参数；
- 与 Evaluation 相关的 Feature Flag 和 Runtime 参数。

`--version` 只是结果目录和实验标签。

它不能证明两个 Evaluation 使用了不同代码，也不能证明两次运行具有可比性。

## 6. Outcome 与 Runtime Behavior

任何 Evaluation 指标都必须明确自己测量什么。

最重要的区分是：

```text
Outcome Correctness
!=
Runtime Behavior Correctness
!=
Execution Cost
```

Agent 可以没有被错误熔断，但最终仍未完成任务。

Agent 也可以最终完成任务，但过程中发生大量重复调用、无意义重试或极高 Token 消耗。

因此分析 Evaluation 时，通常按照下面的优先级：

```text
任务是否真正完成
        ↓
Runtime 行为是否正确
        ↓
是否存在错误停止 / 错误恢复 / 循环
        ↓
轮数、Token、工具调用和耗时是否改善
```

不能为了降低 Token 或 Tool Call 数量牺牲 Success Rate。

不能把某个过程指标改善直接描述为“Agent 能力提升”，除非 Benchmark Claim 和结果确实支持这个结论。

## 7. Anti-Loop / Failure Governance 的特殊解释

Anti-Loop Evaluation 中必须区分：

1. 任务 Outcome 是否正确；
2. Runtime 是否应该停止。

对于 Recoverable Case：

Runtime 没有错误停止，只能证明“没有 False Stop”。

它不能证明任务已经成功恢复。

对于 Permanent / Must Stop Case：

正确停止说明 Governance Decision 合理，但仍需要确认没有伪造业务结果或者产生其他错误行为。

因此类似 TN、TP、FP、FN 的 Governance 分类与 `outcome_success` 不应混为一个指标。

分析 Anti-Loop 结果时同时检查：

- Verifier Outcome；
- Governance Classification；
- Final Status；
- Failure / Blocker Evidence；
- Trace；
- 必要的 Token / Round / Tool 指标。

不要根据单一布尔字段推断整个 Trial 的真实含义。

## 8. 运行 Evaluation

首先确认当前 Python：

```powershell
py --version
```

### Contract Validation

修改 Benchmark 或 Evaluation Infrastructure 后，优先进行 deterministic validation：

```powershell
py eval_runner.py --validate-only
```

该操作用于检查任务 Contract 和可以确定性执行的 Reference / Fixture 条件，不启动正式 Agent Evaluation。

因此 `--validate-only` 通过不代表 Runtime 已经通过 Benchmark。

### 单 Case 实验

需要运行特定 Case 时，可以使用对应 Task：

```powershell
py eval_runner.py --version <version> --task <task_id> --runs <N>
```

正式命令应根据当前 `eval_runner.py --help` 和 Case Contract 确认，不要长期依赖文档中的参数示例。

### Suite Evaluation

支持 Suite 的 Benchmark 应显式选择对应 Suite 和 Split。

例如 Anti-Loop：

```powershell
py eval_runner.py --suite anti_loop --split dev --version <version> --runs <N>
```

Holdout 只有在 Candidate 已基本冻结、确实需要验证泛化时才运行。

真实 LLM Evaluation 会消耗 Provider 资源，不应因为代码修改完成就自动运行完整 Benchmark。

## 9. A/B Comparison

比较 Runtime 改造前后的效果时，不能只是运行：

```text
--version baseline
--version candidate
```

然后比较两个目录。

有效的 A/B Comparison 至少应保证：

```text
旧 Agent Commit + 同一 Benchmark Fixture
vs
新 Agent Commit + 同一 Benchmark Fixture
```

重点检查：

- Baseline / Candidate 的 Agent Commit 是否确实不同；
- Benchmark Fixture 是否一致；
- Suite / Split 是否一致；
- Provider 和 Model 是否一致；
- 模型参数是否一致；
- 关键 Feature Flag 是否一致；
- Worktree 是否处于预期状态；
- Trial 数量是否完整；
- Crash / Invalid / Missing Trace 是否被保留。

如果 Benchmark Fixture 同时发生变化，则必须判断这次结果还能否直接比较。

不要为了得到更好看的数字丢弃失败 Trial。

比较历史结果时使用：

```powershell
py compare_reports.py --versions <baseline>,<candidate> --detail
```

然后结合具体 Trace 解释差异，而不是只读取 Summary。

## 10. DEV 与 HOLDOUT

DEV Case 用于 Runtime 开发过程中反复运行和定位问题。

HOLDOUT 用于 Candidate 基本冻结后的泛化验证。

一旦查看 Holdout 结果并根据这些结果继续修改 Runtime，该批 Holdout 已经被开发过程消费，不能继续把它描述成未见过的独立验证集。

不要为了提高 Holdout 分数针对具体 Holdout Case 修 Runtime。

如果 Holdout 暴露通用 Runtime 问题，应先抽象出通用失败机制，再通过新的证据和后续 Holdout 验证。

## 11. Benchmark 构造与审核

本文描述 Benchmark 如何接入和运行于 Evaluation System，但不承担完整的 Benchmark 设计方法论。

需要执行以下工作时：

- 新建 Benchmark；
- 设计 Benchmark Family；
- 系统性修改现有 Case；
- 审核 Benchmark 是否有效；
- 检查 Leakage / Solvability；
- 设计 Counterfactual；
- 设计 DEV / HOLDOUT；
- 审查 Verifier 是否过拟合；
- 判断 Case 是否真正测量某项 Runtime Capability；

使用：

```text
.agents/skills/miniclaude-benchmark-author/
```

其中：

```text
SKILL.md
```

定义 Benchmark Author 的完整工作流程。

```text
references/harness-contract.md
```

记录 Benchmark Author 需要遵守和重新核实的 Evaluation Harness Authoring Contract。

```text
references/validity-gates.md
```

定义 Leakage、Solvability、Generalization、Verifier Validity 等 Benchmark 质量 Gate。

职责关系为：

```text
docs/EVALUATION.md
    → Evaluation System 怎么使用、分析和修改

Benchmark Author Skill
    → Benchmark 应该怎么设计和审核

harness-contract.md
    → Benchmark Author 接入当前 Harness 时必须遵守什么底层 Contract

validity-gates.md
    → 如何判断 Benchmark 本身是否有效
```

不要用 `docs/EVALUATION.md` 代替 Benchmark Author Skill，也不要要求普通 Evaluation 分析任务加载整个 Benchmark Author 流程。

## 12. 修改 Evaluation Harness

修改 Evaluation Harness 前先判断问题究竟属于哪一层：

```text
Runtime Bug
Benchmark Design Bug
Verifier Bug
Evaluation Harness Bug
Infrastructure Failure
```

不要因为一个 Benchmark 跑失败就直接修改 Runner。

修改 `eval_runner.py`、`compare_reports.py`、`src/core/evaluation/`、Fixture Controller 或 Trial Accounting 时，需要特别检查：

- 是否改变了旧 Result 的解释方式；
- 是否影响历史数据可比性；
- 是否改变 Agent 可见信息；
- 是否引入答案 Leakage；
- 是否可能丢失失败 Trial；
- 是否改变 Manifest / Hash；
- 是否改变 Verifier 执行时机；
- 是否破坏 Shadow Workspace 隔离；
- 是否需要同步修改 Benchmark Author Harness Contract。

Evaluation Infrastructure 的修改必须优先使用 deterministic test 验证。

如果修改改变正式 Evaluation Contract，同时更新本文。

如果变化影响 Benchmark Author 的底层假设，同时更新：

```text
.agents/skills/miniclaude-benchmark-author/references/harness-contract.md
```

## 13. Evaluation 的基本工程原则

使用 Evaluation System 时始终遵守：

1. 先证明 Outcome，再讨论成本。
2. Agent 自述成功不能代替独立验证。
3. Trace 用于解释行为，不用于伪造成功。
4. Benchmark 测 Runtime，不允许 Runtime 针对 Benchmark Case Patch。
5. Verifier 验证结果，不绑定唯一实现。
6. Benchmark 必须同时考虑 Solvability 与 Leakage。
7. 正式 A/B 必须检查运行条件和 Fixture 一致性。
8. Crash、Invalid、Missing Trace 和 Infrastructure Failure 不能静默消失。
9. 没有运行的 Evaluation 不得声称通过。
10. 单次随机结果不足以支撑 Runtime 优化结论。