# Benchmark 驱动迭代——简历驱动追问链

## 0. 简历怎么写

推荐 bullet：

> **Benchmark 驱动迭代：**构建 `baseline/config/verify` + Shadow Workspace + Trace + Run Manifest 的自动化评测框架，截至当前主线沉淀 17 个专项任务；通过固定 Fixture、切换 Agent Commit 和多次运行比较完成率、Token、轮次、工具调用与失败路径，并据此淘汰过度特化的验证工具和无收益规则。

最希望面试官第一问：

> Agent 本身有随机性，你怎么证明一次 Harness 改动真的变好了，而不是这次模型刚好抽卡抽得好？

---

# 一、第一问：你这个 Benchmark 和普通单测有什么区别？

## 30 秒回答

> 普通单测更适合验证确定性函数，而 Coding Agent 的核心输出是“执行轨迹 + 最终工作区状态”，中间会受模型随机性、工具选择和上下文影响。所以我把评测拆成两层：最终结果由独立 `verify.py` 做 deterministic validation，过程由 Trace 记录轮数、Token、Tool Call、失败、LoopGuard 等行为指标。一次 Case 不是只看 Agent 最后说“我完成了”，而是看 Shadow Workspace 最终是不是真的符合任务 Contract，同时保存执行轨迹用于解释为什么变好或变差。

---

# 二、一个 Benchmark Task 长什么样？

当前任务目录基本是：

```text
sandbox/tasks/task_xxx/
├── baseline/
│   └── 任务开始前的初始仓库 / fixture
├── config.json
│   ├── case_id
│   ├── prompt
│   ├── task_version
│   └── verify_script_file
└── verify.py
```

执行时：

```text
读取 config
→ 校验任务 Contract
→ 创建 Shadow Workspace
→ 复制 baseline
→ Agent 只操作 Shadow Workspace
→ Agent 结束后复制并执行 verify.py
→ 读取 Trace
→ 聚合结果与行为指标
→ 归档 run_results / run_manifest / trace
```

`verify.py` 不暴露给 Agent，否则模型可能“为了通过测试”直接阅读验证脚本。

---

# 三、为什么要 Shadow Workspace？

三个原因：

1. **隔离副作用**：Agent 会真实 edit/write/bash，不能让评测污染 baseline；
2. **可复现**：每次 Run 都从相同初始状态开始；
3. **可验证**：Agent 结束后，Verifier 可以检查一个干净、可比较的最终工作区。

如果直接在同一目录反复跑：

```text
Run1 已经改了文件
Run2 从 Run1 结果开始
```

那么第二次根本不是同一个实验。

---

# 四、为什么 verify.py 要独立于 Agent？

因为模型最终文本不可信。

例如 Agent 可能说：

```text
“已完成全部重构并验证通过”
```

但实际：

```text
漏改 1 个文件
语法仍错误
测试没真正执行
```

所以结果判定必须来自 Agent 外部。

核心原则：

> Agent 是被测对象，不能同时当裁判。

这也是为什么 `verify_status` 与 `final_status` 要分开。Agent 正常结束不等于任务完成；反过来，某些测试甚至可能最终 Workspace 正确，但 Agent 状态异常，这两种信息都应该保留。

---

# 五、如何处理模型随机性？

不要说“设 temperature=0 就没有随机性”。实际 Provider、工具选择、采样和外部环境仍可能带来波动。

当前比较方式主要是：

```text
固定同一 Task Fixture
固定同一 Prompt / Config
固定同一 verify Contract
Baseline 与 Refactor 使用不同 Agent Commit
每组跑多次（常见 3～5 次）
比较分布而不是只看一次
```

重点指标：

```text
第一层：任务成功率 / verify pass
第二层：total turns / total tokens / latency
第三层：tool call count / failure count / loop guard / circuit breaker
第四层：具体 Tool Sequence 和 Trace
```

排序原则：

> 先保证成功率不下降，再讨论 Token、轮次和 Tool Call 是否下降。

如果成功率从 5/5 变成 3/5，即使 Token 省 80%，也不能叫优化。

---

# 六、为什么 Run Manifest 很重要？

历史上评测最容易犯的错误之一是：

```text
Baseline 用旧任务 fixture
Refactor 用新 fixture
```

最后数字不能比较。

因此 Run Manifest 记录：

```text
Agent commit
worktree dirty / clean
Python / platform
任务集 hash
task config hash
baseline fixture hash
```

比较报告发现任务集或运行条件不一致时应该报警，而不是继续计算一个看起来漂亮的 Δ。

面试可以一句话概括：

> 我不仅版本化代码，也版本化实验条件，否则 Agent 的指标差异无法归因。

---

# 七、为什么“切换 --version 名称”不等于切换实验版本？

`--version baseline` 只是输出目录标签。

真正的 A/B 必须是：

```text
旧 Agent Commit + 同一版 Benchmark Fixture
vs
新 Agent Commit + 同一版 Benchmark Fixture
```

如果两边实际跑的是同一份工作区代码，只改报告名字，那是假实验。

项目文档后来专门补了版本隔离规范，就是因为这个坑很容易发生。

---

# 八、Benchmark 怎么选？为什么不是越多越好？

当前 17 个任务不是为了做“大而全排行榜”，而是按真实 failure mode 增长。

大体覆盖：

```text
代码编辑
非唯一上下文
跨文件重构
搜索回归
大日志定位
中间日志定位
离线依赖阻断
连续 0-Diff 编辑
Shell 环境持久化
```

一个新 Benchmark 值得加入，通常满足：

```text
真实 Trace 里出现过
可以稳定复现
有清晰 deterministic outcome
能区分改造前后行为
不是专门给某条 if-else 喂答案
```

---

# 九、怎么防 Case Chasing / Benchmark 过拟合？

虽然 mini-claude 没有 DP-Plus 那套正式 Holdout 分层，但工程上仍然要防“为了某个 Case 加规则”。

常用做法：

1. 一个修复至少看 Positive + Negative Case；
2. 规则只针对稳定 failure pattern，不针对具体 fixture 文件名；
3. 评测失败先看 Trace，区分 Runtime Bug、Verifier Bug、Fixture Bug；
4. 新功能若只让一个特定任务变好、其他任务变差，不合入；
5. 对参数阈值不宣称“最优”，除非做过系统搜索。

最重要的是：

> Benchmark 用来暴露 Harness 的系统性问题，不是让生产代码背答案。

---

# 十、一个非常值得讲的 Case：verify_symbol_rename 为什么最后被删了？

这个故事很适合回答“有没有做过失败的优化”。

演进：

```text
跨文件重命名任务容易反复写 verify.py
→ 增加 search_code / count_occurrences / syntax_check / verify_symbol_rename
→ 再给 verify_symbol_rename 加 scope / targets / confidence
→ 又加停止启发式
→ 轮次和 Token 仍然可能暴涨
→ 发现模型会在 Todo 很早就规划自定义 verify.py
→ 继续加 Prompt / Tool 描述约束，效果有限
→ 最终移除过度特化的 syntax_check / verify_symbol_rename
→ 改回语言原生工具 + 更简单 Harness
```

核心结论：

> Coding Agent 不是工具越多越强。专门为一个 Benchmark 造“超级验证工具”，可能让 Harness 复杂化，也让模型更依赖工具 schema。评测最终让我选择删功能，而不是继续堆规则。

这比说“我做了很多工具”更成熟。

---

# 十一、TodoWrite 为什么也被弱化/移除？

Trace 观察到：

```text
模型在第二轮 Todo
就提前写入“写 verify.py / 跑额外验证”
```

一旦这个动作进入显式 Plan，后面即使任务已经静态验证充分，模型也倾向完成计划，造成额外轮次和 Token。

尝试过：

```text
改 Tool description
改 System Prompt
对 Todo 做 State Folding
改历史消息写回方式以保护 Prompt Cache
```

都有一定作用，但无法根治。

后来直接从 Tool Schema 禁用 TodoWrite，指标明显下降。

这个 Case 可以回答：

> 为什么 Agent Harness 里“规划工具”不一定总是正收益？

因为 planning 本身也会形成行为承诺和上下文成本。

---

# 十二、长日志优化实验怎么做到可比？

以 `task_013` 为例：

```text
Baseline commit: ab5d58e9
Refactor commit: 270887a3
Task suite hash: 相同
Case: 相同
每组 5 runs
```

结果：

```text
通过率：5/5 → 5/5
Token：143.5k → 48.0k
Peak Turn Tokens：18.5k → 5.7k
```

`task_014`：

```text
通过率：5/5 → 5/5
Token：254.0k → 79.3k
```

这里的价值不只是数字，而是 Manifest 能证明两边 Fixture 一致，因此差异更能归因到 Harness 改造。

---

# 十三、为什么要记录 Peak Turn Tokens？平均 Token 不够吗？

因为 Context 爆炸往往是“某一轮突然塞入巨量输出”。

例如：

```text
平均每轮 6k
但某一轮 40k
```

这个 spike 可能：

```text
触发 Context 上限
导致 latency 突增
让后续每轮都背着巨量历史
```

所以 Peak Turn Tokens 能直接观察单轮输入压力。

长日志改造里 Peak 降幅甚至比总 Token 更有解释力。

---

# 十四、Tool Call Precision 是什么？能当准确率吗？

不要把它包装成通用“Agent 工具调用准确率”。

它只是项目内工程指标，用来观察工具调用是否大量落在预期有效路径、是否存在失败/冗余调用。

真正结果正确性仍由 `verify.py` 决定。

面试如果被问“Precision 的 Ground Truth 怎么定义”，要先说清项目自己的 metric semantics，而不是硬套分类任务公式。

---

# 十五、为什么 Trace 很重要？只有最终指标不够吗？

两版都失败：

```text
Case A：卡在网络下载循环
Case B：编辑完成了，但最后 verify 写错
```

最终都是 FAIL，但修复责任完全不同。

Trace 会记录：

```text
task
turn
Tool Call
args hash / intent
成功失败
Failure Category
Recoverability
Strategy Fingerprint
LoopGuard / CircuitBreaker
Token / latency
Workspace / Shell Context
```

所以：

> Metric 告诉我“哪里变差了”，Trace 告诉我“为什么变差”。

---

# 十六、为什么需要 Session JSONL，Trace 不够吗？

Trace 更偏评测指标和任务级结构化行为；Session JSONL 更接近真实交互回放：

```text
session_id
round_id
step
thinking.turn
call_id
UI / tool / model event
```

一个用于性能 / Benchmark 分析，一个用于排障和人类回放。

简历不需要主动写，但面试官问可观测性时可以讲。

---

# 十七、Benchmark 本身出错怎么办？

`task_016_stalled_code_edit` 就是代表 Case。

第一次改状态守卫后，报告显示 Runtime 已有 blocking，但最终 verify 仍不符合预期。

进一步排查后发现：

```text
不是简单“代码错了”
而是 fixture / expected final status / verify contract 也需要校准
```

后续专门提升 task_version 并修正评判契约。

面试可以讲：

> 我不会看到一个红 Case 就立刻改 Runtime。Evaluation Harness 自己也会有 Bug，所以失败要先做 attribution：Model、Runtime、Fixture、Verifier、Environment 哪一层出了问题。

---

# 十八、如果一个优化只跑 3 次，有统计意义吗？

这个问题要谨慎。

> 3～5 次运行只能算工程对照样本，不足以做严格统计显著性结论。我主要用它发现大幅、稳定的行为变化，例如 Token 从 250k 降到 80k 这种数量级差异；如果两个版本只差 3%～5%，我不会仅凭 3 次 Run 宣称优化成立，而会增加 runs 或补更多任务。

这比硬讲置信区间更稳。

---

# 十九、为什么不用 SWE-bench 直接评？

可以从两个层次回答。

> SWE-bench 更适合衡量完整 Coding Agent 在真实 GitHub issue 上的端到端修复能力；我的目标是研究 Harness 某个具体机制，比如长输出处理、LoopGuard、Shell Session。用大而复杂的外部 Benchmark 很难归因某次 Runtime 改动。因此我先用小而可控的 failure-specific Benchmark 做机制实验。如果项目要证明通用 Coding 能力，后续才应该补 SWE-bench / RepoBench 等更外部化的数据集。

不要说自建 Benchmark 比 SWE-bench 更好，它们职责不同。

---

# 二十、牛客风格追问题库

1. 你怎么构建 Agent 评测集？
2. 离线评测和线上评测分别看什么？
3. Agent 是非确定性的，怎么做 A/B？
4. 为什么要多次运行？几次够？
5. 为什么不能只看最终 Answer？
6. deterministic verifier 有哪些优点和局限？
7. verify.py 会不会过拟合？
8. 如何从线上 Trace 抽 Case？
9. Case 怎么版本化？
10. Fixture 改了以后历史结果还能比较吗？
11. 如何防 Benchmark 泄露给 Agent？
12. Shadow Workspace 和 Docker Sandbox 有什么区别？
13. 怎么避免 Agent 修改 verify.py？
14. 如何比较两个 Agent Commit？
15. Token、Latency、Success Rate 冲突时怎么取舍？
16. 一个优化成功率不变、Token 降 30%，就一定值得合入吗？
17. 为什么 Tool Call 少不等于更好？
18. Agent 最后说成功但 verify 失败，状态怎么算？
19. 如何处理 flaky Case？
20. Benchmark 自己有 Bug 怎么发现？
21. 为什么要 Run Manifest？
22. Holdout 有吗？如果没有如何防 case chasing？
23. 为什么不直接用 LLM Judge？
24. 开放式代码质量怎么评？
25. 如何评 Agent 的“过程质量”？
26. 失败归因怎么做？
27. 什么指标最能说明 Agent Reliability？
28. 为什么要记录 Peak Turn Tokens？
29. 什么时候应该删除一个工具而不是继续优化？
30. 如果让你把这套 Benchmark 扩到生产，你会怎么做？

---

# 二十一、最后背这一段

> mini-claude 的 Benchmark 不是为了做一个通用排行榜，而是为了让 Harness 改动可归因。每个任务都有固定 baseline、config 和独立 verify，运行时复制到 Shadow Workspace，Agent 只修改隔离副本；结束后再由外部 verifier 判断最终状态，同时 Trace 记录轮次、Token、工具调用和失败路径。做 A/B 时我固定 Fixture，只切 Agent Commit，并通过 Run Manifest 校验任务集和代码版本一致。最重要的是先看成功率，再看成本指标。历史上我甚至通过评测删掉了 verify_symbol_rename、TodoWrite 这类看起来“能力更强”但实际增加行为复杂度的设计，所以这套评测对我最大的价值不是打分，而是防止靠主观感觉堆 Agent 功能。
