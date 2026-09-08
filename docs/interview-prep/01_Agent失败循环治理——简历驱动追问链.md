# Agent 失败循环治理——简历驱动追问链

## 0. 简历怎么写

推荐最终 bullet：

> **Agent 失败循环治理：**针对 Coding Agent 在网络不可达、依赖安装和重复编辑等场景中的持续空转，将工具调用归一化为 `action + target` 的 Intent Fingerprint，并结合 Failure Category / Strategy Fingerprint、环境探针和工作区状态守卫识别重复失败与 0-Diff 写入；对不可恢复循环执行 Runtime 级硬熔断。离线依赖专项 Benchmark 中通过率由 2/5 提升至 5/5，平均轮次由 17.4 降至 1.0。

这句话最希望面试官抓住的词只有：

```text
Intent Fingerprint
Strategy Fingerprint
Failure Category
硬熔断
```

不要再主动塞 `EnvironmentBlocker`、`WorkspaceStateGuard`、FailureMemory、CircuitBreaker 阈值等内部名词，这些留给追问。

---

# 一、第一问：Intent Fingerprint 和 Strategy Fingerprint 到底是什么？

## 30 秒回答

> 我把两层概念拆开。Intent Fingerprint 是 Runtime 对“这一次工具调用想做什么”的归一化，主要是 `tool + action + target`，例如不同噪声参数的 `pip install pygame` 都会落到安装 `pygame` 这个意图，用于近窗口重复检测。Strategy Fingerprint 属于 Failure Intelligence，是更粗的策略类别，比如包安装、网络下载、文件 IO。失败后再把 Failure Category 和 Strategy Fingerprint 组合，判断是不是同一类根因下反复使用同一种策略。前者主要防操作级空转，后者主要判断失败是否值得继续重试。

## 当前代码机制

### 1. Intent Fingerprint

`CommandNormalizer` 会把 Bash 命令 token 化并去除环境前缀、重定向等噪声，再抽取：

```text
tool + action + target
```

例如：

```text
pip install pygame
pip install pygame --index-url xxx

→ bash:INSTALL_PACKAGE:pygame
```

对于文件工具则直接按工具语义构造：

```text
read_file + file + line range
edit_file + file + edit hash
search_code + path set + pattern hash
```

这样不是所有同文件操作都被视为同一 Intent。

### 2. Strategy Fingerprint

Failure Intelligence 里的 Strategy Fingerprint 更粗，例如：

```text
NETWORK_PACKAGE_INSTALL
NETWORK_DOWNLOAD
PACKAGE_QUERY
LOCAL_FILE_IO
CODE_EXECUTION
CODE_COMPILE
VCS_OPERATION
```

### 3. Failure Fingerprint

失败分类后得到：

```text
FailureCategory::StrategyFingerprint
```

例如：

```text
NETWORK_UNREACHABLE::NETWORK_PACKAGE_INSTALL
PERMISSION_DENIED::LOCAL_FILE_IO
PACKAGE_NOT_FOUND::NETWORK_PACKAGE_INSTALL
```

这一步的目的不是证明两个命令字符串一样，而是判断：**根因是否一样，Agent 是否还在重复同类失败策略。**

---

# 二、为什么不能直接对 Tool Name + Args 做 Hash？

因为 raw args 对 Coding Agent 来说噪声太大。

典型例子：

```text
python test.py
python -u test.py
python test.py 2>&1
cd repo && python test.py
```

四条命令文本不同，但真正意图都可能是：

```text
EXECUTE:test.py
```

如果直接 hash 原始参数，会发生 false negative：Agent 只要稍微改一下 flag 就能绕过 LoopGuard。

反过来，归一化也不能过粗。例如：

```text
read_file(a.py, 1~100)
read_file(a.py, 101~200)
```

这是合理的渐进读取，不能因为都是 `read_file:a.py` 就阻断。因此 `read_file` 的目标会带行区间；搜索工具也会把 pattern 纳入目标指纹。

这一点来自项目早期真实踩坑：LoopGuard 过粗时会把不同读取窗口、不同搜索条件误判成重复调用，后来专门补了 path/paths、line range、patterns 等 target 维度。

---

# 三、如果 grep → find → rg，是合理探索还是重复策略？

这是高风险追问。

不要回答“系统能理解语义上是不是同一策略”。当前系统没有做通用语义聚类。

更稳的回答：

> 当前 Runtime 对重复判断是 deterministic normalization，不试图理解所有“策略升级”。像 `grep → find → rg` 这类搜索工具切换，如果归一化结果不同，会保守放行。我的设计原则是死循环治理宁可有一定 false negative，也不要为了多拦一次就把合理探索误杀。真正不可恢复的网络、权限、包不存在等问题还有 Failure Category 和 Environment Blocker 兜底，所以不需要让 Intent Fingerprint 承担全部判断。

关键点：

```text
Intent Guard 解决重复行为
Failure Intelligence 解决重复根因
Environment Blocker 解决明确环境硬阻断
Workspace State Guard 解决连续 0-Diff 写入
```

四层职责不要混在一起。

---

# 四、LoopGuard 到底是提示模型，还是代码硬拦？

当前是两级。

## 一级：LoopGuard 物理拦截当前 Tool

当相同 Intent 在滑动窗口中重复达到阈值时：

```text
本次 Tool 不执行
→ 返回“重复操作提醒”
→ 把 LOOP_GUARD_PREVENTED 记入 FailureMemory
→ CircuitBreaker 累加 strike
```

所以不是单纯在 Prompt 里说“别重复”。

## 二级：CircuitBreaker 硬结束 Agent Loop

同一 Intent 累计失败达到阈值后：

```text
raise RuntimeEscalationException
→ 当前 task final_status = CIRCUIT_BROKEN
→ 不再把普通 tool result 喂回 LLM
→ Runtime 直接结束循环
```

回答面试官时可以强调：

> 我一开始也尝试过软提醒，但 Coding Agent 经常会无视文字提示继续换皮重试，所以真正的停止条件必须由 Runtime 裁决，而不是让模型自己决定是否听话。

---

# 五、Failure Category 和 Recoverability 怎么来的？

当前是 rule-based，不是 ML classifier。

错误文本会按有序 regex 规则归一为：

```text
PERMISSION_DENIED
NETWORK_UNREACHABLE
TIMEOUT
PACKAGE_NOT_FOUND
FILE_NOT_FOUND
SYNTAX_ERROR
COMMAND_NOT_FOUND
OUT_OF_MEMORY
DISK_FULL
TOOL_CRASH
UNKNOWN
```

同时赋 Recoverability：

```text
SELF_HEALABLE
PARTIALLY_RECOVERABLE
USER_INTERVENTION_REQUIRED
NON_RECOVERABLE
UNKNOWN
```

例如：

```text
Permission denied
→ PERMISSION_DENIED
→ USER_INTERVENTION_REQUIRED

No matching distribution found
→ PACKAGE_NOT_FOUND
→ USER_INTERVENTION_REQUIRED

SyntaxError
→ SYNTAX_ERROR
→ SELF_HEALABLE
```

为什么不用 LLM Judge 判断失败类型？

> 这是 Runtime 的安全与停止决策，要求低延迟、稳定、可测试。高频错误模式用 deterministic rules 更容易做单测和回归；无法识别的错误进入 UNKNOWN，默认不立即硬杀，让模型继续探索。只有当规则覆盖不足成为真实问题时，才值得再考虑模型分类。

---

# 六、FailureEscalationPolicy 怎么决定“继续、升级、终止”？

当前主要有三类规则：

```text
1. 同 Failure Category 达到高水位
   → 不管策略多样性，升级

2. 同类错误重复 >= 阈值
   + 尝试策略数很少
   + 属于需要用户干预的类别
   → 升级

3. NON_RECOVERABLE
   → 立即升级
```

重点不是背具体数字，而是理解两个维度：

```text
失败是否反复出现？
Agent 是否真的换过策略？
```

如果：

```text
NETWORK_UNREACHABLE × 4
strategy diversity = 1
```

继续换 `pip` 参数意义很小。

但如果是：

```text
FILE_NOT_FOUND
→ Agent 调整路径
→ 找到文件
```

它应该被允许自愈。

---

# 七、Environment Blocker 为什么还需要？Failure Intelligence 不够吗？

后期专项 Benchmark 暴露出一个问题：

即使 Failure Intelligence 已经能识别 `PACKAGE_NOT_FOUND` / `NETWORK_UNREACHABLE`，Agent 仍可能先做很多无意义尝试，等累计次数达到阈值才停。

于是增加 Preflight + EnvironmentBlocker：

```text
Agent 启动
→ 预先探测有限环境事实
→ System Prompt 注入环境上下文

Tool 执行前 / Tool 结果返回后
→ EnvironmentBlocker 检查是否命中明确硬阻断
→ 命中则直接 record circuit breaker
→ 立即返回终止信息
```

这就是为什么离线依赖专项能从十几轮空转压到约 1 轮。

不要说成“任何环境问题都能提前识别”。它只覆盖明确的 hard blocker pattern。

---

# 八、连续 0-Diff 修改为什么不能靠 LoopGuard？

因为 Agent 可能每次给 `edit_file` 的参数都不完全一样，但实际上 Workspace 根本没有变化。

例如：

```text
第一次 edit_file
→ 匹配歧义，未写入

第二次换 search block
→ 仍未写入

第三次再换一点上下文
→ 仍未写入
```

从 Tool Args 看它在“探索”；从真实 Workspace State 看，它一直没有前进。

于是引入 `WorkspaceStateGuard`：

```text
写工具调用前：snapshot(workspace)
执行后：snapshot(workspace)
比较 mutation
连续无状态变化
→ STATE_STALLED
→ Circuit Breaker
```

这个思想很重要：

> Agent Progress 不能只从“它调用了多少不同工具”判断，还要看 Environment / Workspace 是否真的发生了有效状态迁移。

---

# 九、你怎么证明防循环没有误杀正常任务？

这是整个 Hook 最重要的“怎么验证”。

不能回答“我跑了一下感觉没问题”。

评测要拆成：

```text
Positive Case
→ 本来就应该触发 guard 的失败任务

Negative Case
→ 正常探索 / 正常编辑任务，不应该被 guard 打断
```

例如历史中 `task_006_cross_file_drift` 用来观察增强意图指纹后是否产生误熔断；当时结果是 `loop_guard_trigger_count=0`，说明这类正常跨文件重构没有被误杀。

后期更专门的：

```text
task_015_offline_dependency_block
→ 必须尽快识别不可用依赖并结束

task_016_stalled_code_edit
→ 必须对连续 0-Diff 编辑触发状态守卫
```

这里一定要承认：`task_016` 并不是第一次就做对了。早期 Benchmark 契约本身也有问题，出现系统实际行为和 verify 期望不一致，后来专门修改了 fixture / contract。这是一个很好的面试 Case：**评测本身也可能错。**

---

# 十、专项 Benchmark 的数字怎么讲？

旧简历里 `26.3 → 19.7 (-25.3%)` 不是 Failure Loop 专项实验，而来自 Stubbing / Todo 相关上下文实验，不应该拿来证明 Failure Loop。

当前更稳的失败循环数字：

```text
task_015_offline_dependency_block

Baseline:
2/5 通过
17.4 平均轮次
146.1k Token

Refactor:
5/5 通过
1.0 平均轮次
3.5k Token 左右
```

如果面试官问为什么数字这么夸张：

> 因为这不是全任务集平均，而是故意构造的 failure-specific Benchmark。Baseline 会在不存在的依赖上反复尝试下载、换包管理器、换命令；改造后 Environment Blocker 很早就把问题识别成外部硬阻断。因此这个数字证明专项机制有效，不代表所有 Coding Task 都能减少 90% 以上轮次。

这句话必须主动讲清，避免被认为“拿极端 Case 包装全局收益”。

---

# 十一、给一个你这套机制判错的 Case

最好讲 `task_016`。

第一版状态守卫后，Benchmark 没有像预期那样稳定触发 `CIRCUIT_BROKEN`，一度出现：

```text
Runtime 实际已经进行了部分 blocking
但 verify 仍判失败
```

后来检查发现问题不只在 Runtime，也在 Benchmark contract：fixture 对“什么叫停滞”和预期终态约束得不够准确，于是调整任务版本和验证契约。

回答重点：

> 我不把评测红灯自动等同于生产代码错。先看 Trace，再区分是 Runtime 行为错误、Fixture 泄露、Verify 条件错误，还是模型随机性。这个 Case 让我意识到评测系统本身也是软件，也需要版本化和测试。

---

# 十二、为什么不直接限制最大轮数？

最大轮数只能做最后保险，不能解决“什么时候应该停”。

例如：

```text
任务 A：正常需要 30 轮
任务 B：第 3 轮就已经确认网络不可达
```

如果统一 `maxTurns=20`：

```text
A 被误杀
B 还浪费 17 轮
```

所以真正需要的是：

```text
Progress-aware stopping
Failure-aware stopping
Environment-aware stopping
```

最大轮数可以继续保留，但它只是 watchdog，不是主要决策逻辑。

---

# 十三、为什么不用“连续 N 次相同 Tool Call”这么简单？

因为 Coding Agent 最常见的空转不是完全相同调用，而是：

```text
同一目的
+ 参数微调
+ shell noise
+ 换一种近似调用方式
```

而且不同工具的“重复”定义也不一样：

```text
read_file
→ 行区间变了，可能是合理探索

edit_file
→ edits 内容变了，也许确实是新尝试

package install
→ 只是换 index / redirect，可能还是原策略
```

因此需要 tool-specific normalization，而不是单一 raw hash。

---

# 十四、为什么不用 Embedding / LLM 做 Strategy 相似度？

30 秒回答：

> 因为这里是 Runtime stop gate。误判一次就可能提前杀掉一个本来能成功的任务，所以我更需要稳定、低成本、可测试的判据。当前高频死循环主要来自包安装、网络、文件、代码执行等有限工具语义，rule-based normalization 已经能覆盖主要问题。Embedding 或 LLM 相似度可以提高泛化，但会引入阈值、延迟和不可解释误杀，我目前没有证据证明收益值得这个复杂度。

如果面试官继续：

> 那现在扩展新工具怎么办？

答：

> 新工具先走默认 `tool + toolName + target`，只有 Trace 里观察到稳定的重复失败模式，才加专用 normalization rule，并补 positive / negative Benchmark，不先设计通用 ontology。

---

# 十五、最值得讲的开发演进

可以压缩成：

```text
Raw Tool 重复检测
→ 发现参数 / 路径 / 行区间造成误判
→ tool-specific Intent Fingerprint
→ 发现相同根因仍能换皮重试
→ Failure Category + Strategy Fingerprint
→ 软提醒仍可能被模型忽略
→ Runtime Hard Circuit Breaker
→ 明确环境硬错误仍停得太慢
→ Preflight / Environment Blocker
→ 参数不同但 Workspace 一直没变化
→ WorkspaceStateGuard
→ 专项 Benchmark + Trace 校验
```

这条演进链比背类名重要得多。

---

# 十六、牛客风格追问题库

以下问题要能不看资料连续回答：

1. Agent Loop 的终止条件有哪些？
2. 为什么不能完全交给 LLM 自己判断“任务结束了”？
3. LoopGuard 和传统熔断器有什么区别？
4. Circuit Breaker 为什么按 Intent 计数，而不是全局计数？
5. 如何避免正常重试被误杀？
6. TIMEOUT 是可恢复还是不可恢复？为什么？
7. 网络错误为什么不能无限重试？
8. 工具返回成功但 Workspace 没变，算成功吗？
9. Agent 被 Runtime 熔断后，下一轮用户还能继续吗？状态怎么清？
10. 为什么 FailureMemory 是 task-scoped？
11. maxTurns、LoopGuard、Failure Intelligence 三者分别是什么职责？
12. 如果模型不停生成完全不同的命令，但都失败，怎么处理？
13. 如果误熔断发生了，你会看哪些 Trace 字段定位？
14. 怎么构造 anti-loop Benchmark？
15. 为什么 Benchmark 一定要有 Negative Case？
16. 如果所有 Case 都能被你的规则识别，会不会过拟合 Benchmark？
17. 新增一种包管理器，比如 pnpm / yarn，怎么扩展？
18. 为何不直接用 LangGraph recursion_limit？
19. 如何处理用户主动要求“再试一次”？
20. 硬熔断之后为什么不自动 Re-plan？

---

# 十七、最后背这一段

> mini-claude 的死循环治理不是一个 hash 去重。第一层先把工具调用归一成 `tool + action + target`，用于识别近期重复 Intent；第二层把真实失败归类成 Failure Category，再结合更粗粒度 Strategy Fingerprint 看根因和策略有没有变化；对于网络、权限、包不存在这类明确外部阻断，再用环境探针提前终止；而对于参数一直变化但 Workspace 连续没有 Diff 的编辑空转，则看真实状态迁移。最后停止不是靠 Prompt 劝模型，而是 Runtime 代码硬拦 Tool 或触发 Circuit Breaker。所有策略都通过专项 Benchmark 同时看 Positive Case 和误杀 Case，不拿一次 Trace 当结论。
