# Benchmark Author Skill——怎么避免出一道“看起来像评测”的假题

> 这篇是 `03_Benchmark驱动迭代——简历驱动追问链.md` 的配套资料。
>
> `03` 讲的是整个评测系统怎样从早期 Harness 一路演进到可信 Baseline；这篇只深挖其中一个关键转折：**为什么项目要专门给 Codex 写一个 Benchmark Author Skill，以及它到底在防什么。**
>
> 先明确边界：这个 Skill **不是 MiniClaude Runtime 的 Skill**。它是仓库级规则，供 Codex 在设计、生成或审核 MiniClaude Benchmark 时使用。

---

## 一、为什么“会写 Benchmark Case”还远远不够

假设你想测 MiniClaude 一个很合理的能力：

> 遇到依赖失败时，判断还有没有合法恢复路径；有就继续，没有才停止。

最容易写出来的题可能是：

```text
README：enterprise adapter 永久不可用，没有替代实现。

Prompt：请完成任务；如果环境确实不可恢复，请说明 blocker 并停止。
```

再准备一个 Verifier：最后看到 `BLOCKED_ENVIRONMENT` 就 PASS。

这道题看起来完整：有 Prompt、有 Fixture、有结果判断，Agent 也很可能稳定做对。

但它实际上没有很好地测试“判断恢复路径”的能力，因为 README 已经替 Agent 做完最关键的判断。

换句话说：

> **一道 Agent 能稳定做对的题，不一定是一道有效的 Benchmark。**

这就是 Benchmark Author Skill 出现的原因。

项目希望把命题顺序从：

```text
看看当前 Runtime 怎么实现
→ 给它造一个容易验证的 Case
```

改成：

```text
先定义要测的 Capability
→ 定义什么事实能证明 / 推翻它
→ 设计 Agent 能正常调查的环境
→ 设计无法直接读答案的 Prompt / Fixture
→ 设计不绑定唯一实现的 Verifier
→ 最后才让 Runtime 来考试
```

也就是：

> **Capability → Benchmark → Runtime**
>
> 而不是：
>
> **Runtime implementation → Benchmark。**

---

## 二、这个 Skill 到底是谁在用

目录：

```text
.agents/skills/miniclaude-benchmark-author/
```

执行关系：

```text
用户
↓
Codex
↓
Benchmark Author Skill
↓
读取 mini-claude 仓库
↓
审计 / 设计 Benchmark
↓
必要时生成 sandbox/tasks/task_xxx
```

Skill 的作用更像“项目里的命题规范 + 审稿流程”。

MiniClaude Agent 自己不会在做 Coding Task 时调用这个 Skill。

为什么要强调？因为面试时如果说：

> “我给 MiniClaude 加了一个 Benchmark Skill。”

很容易让人理解成 Runtime 新增了 Skills Loader / Tool。

更准确的说法是：

> **我把 Benchmark 设计规则做成了 Codex 可执行的仓库级 Skill，让后续出题和审核不再依赖临时 Prompt。**

---

## 三、Skill 第一件事不是写文件，而是问“你到底想测什么”

这一步叫 Capability Spec，但没必要背英文。

核心就是先把能力说成人话。

例如坏定义：

```text
识别 ModuleNotFoundError 后停止
```

为什么不好？因为它太贴当前错误文本。Runtime 很容易演化成：

```python
if "ModuleNotFoundError" in error:
    stop()
```

Benchmark 分数提高了，却不代表换成 Maven artifact missing、Node package missing 或 shell executable missing 后还能正确判断。

更好的能力定义是：

> **依赖失败后，根据环境事实和可用替代路径判断任务是否仍有合法恢复方案。**

这时候 Python 的 `ModuleNotFoundError` 只是这个能力的一种表面。

所以 Skill 会先问：

```text
要证明哪项能力？
什么结果算正证据？
失败能说明什么？
失败又不能说明什么？
哪些恢复方案都是合法的？
```

这一步看起来慢，但能防最危险的 Case Chasing。

---

## 四、为什么一定先写 Internal Oracle，再写 Prompt

Internal Oracle 可以理解为“命题人自己先把这题为什么该停 / 该继续想清楚”。

例如同样是依赖下载失败：

### Case A

```text
公网下载失败
但仓库 vendor/ 中有合法本地依赖
```

应该继续恢复。

### Case B

```text
公网下载失败
本地无缓存
无 vendor
无替代工具
任务明确依赖该组件
```

才可能合理停止。

如果命题人自己都没有先确定：

```text
到底哪个环境事实决定 STOP / RECOVER？
```

很容易写出一种题：Prompt 里直接告诉 Agent 应该停，然后 Verifier 再验证它停了。

所以 Skill 强制先在 evaluator/author 一侧冻结：

- 预期行为；
- 合法路径；
- 最小充分 evidence；
- 什么是假成功；
- 什么是相反标签。

然后才写 Agent 看得到的 Prompt。

> 【核心提炼】标准答案先存在于命题人的因果模型里，但不能直接流进 Agent 可见信息。

---

## 五、最难的平衡：既不能泄答案，又不能让题只能靠猜

这一对概念贯穿整个 Skill：

```text
Leakage：答案是不是漏出来了？
Solvability：不看答案，正常工程师能不能自己调查出来？
```

它们必须一起看。

### 错误方案 1：为了可解，直接泄题

```text
README：This dependency is permanently unavailable.
```

Agent 直接知道 permanent blocker。

### 错误方案 2：为了保密，把证据全部删掉

```text
只有一句：依赖失败了，请处理。
```

仓库没有 probe、没有 package 状态、没有 fallback 信息，Agent 根本无从判断。

### 更合理的方案

给它一个真实工程调查面：

```text
build / probe / package manager / HTTP endpoint / config resolution
```

Agent 自己跑：

```text
probe
→ 404
→ 检查本地 fallback
→ 不存在
→ 检查允许的 alternative
→ 仍不存在
→ 最后得出无法继续
```

这才是在测能力。

Skill 会审计的不只是 Prompt，还包括：

```text
源码注释
README
visible tests
文件名
变量名
错误消息
环境变量
HTTP response
Runtime 注入文本
```

因为答案不一定从 Prompt 泄露，也可能藏在 `missing_and_no_fallback.lock` 这种文件名里。

---

## 六、为什么要做 Counterfactual：防止 Runtime 学成“一见 503 就停”

如果 Suite 里所有依赖失败 Case 都是：

```text
失败
→ 应该 STOP
```

Runtime 很容易通过一个错误规则刷高分：

```text
依赖错误 = STOP
```

所以更好的测试通常成对出现。

同样的用户目标：

```text
请完成这个依赖任务
```

但环境事实不同：

```text
Case A
registry unavailable
+ 本地 vendor fallback 存在
→ RECOVER

Case B
registry unavailable
+ 无任何合法 fallback
→ STOP
```

Prompt 本身不告诉 Agent标签。

两道题的区别来自环境事实。

于是简单规则：

```text
“看到 dependency error 就 STOP”
```

在 A 上必然失败。

这就是 Counterfactual 真正的作用：**不是为了 Case 数量翻倍，而是逼 Runtime 根据决定行为的事实做判断。**

---

## 七、为什么不能机械地 Python / JVM / Node / Shell 各来一题

Skill 还专门防另一种“看起来覆盖很广”的假丰富。

比如一个纯文件读写能力：

```text
Python 文件
Java 文件
Node 文件
Shell 文件
```

如果 Observation、Action、推理都没变化，只是换后缀，那四道题并没有真正增加多少 coverage。

什么时候跨生态有意义？

例如 Failure Intelligence：

```text
Python：ModuleNotFoundError
Maven：Could not resolve artifact
Node：Cannot find module
Shell：command not found
```

这里生态会真实改变：

- 错误表达；
- 可用工具；
- package manager；
- 合法 fallback；
- 调查路径。

所以跨生态才有价值。

原则不是“多语言越多越高级”，而是：

> **只有变化真的改变了 Observation、Action 或合法推理，才算新的有效表面。**

---

## 八、Verifier 为什么既要防作弊，又不能绑死标准答案

这是实际 Hardening 中踩坑最多的地方之一。

一个差的 Verifier 可能写成：

```text
检查第 42 行是不是改成某字符串
```

这样 Reference Solution 可以过，但另一个完全合法的重构可能失败。

这叫把 Verifier 绑在“标准 Patch”上。

理想情况应该测：

```text
最终行为 / 状态 / invariant
```

而不是：

```text
你是不是用了我的实现方式
```

Skill 因此要求命题时做几类最基本的反例：

| 测试 | 应该怎样 |
|---|---|
| untouched baseline | FAIL |
| reference solution | PASS |
| obvious cheat | FAIL |
| alternate legal implementation | PASS |

这里 `obvious cheat` 不是一句口号。

项目真实审过：

- 修改 visible tests；
- 删除 tests；
- 改 test runner；
- 手写 expected artifact；
- hardcode output；
- fake health / READY；
- unrelated failure 后 STOP；
- sentinel 文件；
- 伪造普通 stdout/stderr。

但也不能无限写“作弊样例列表”。真正应该建立的是 Trust Boundary：

```text
Agent 能控制什么？
Evaluator 真正能信什么？
```

如果一个 PASS 最终只依赖 Agent 自己能修改的文件或输出，那就要非常警惕。

---

## 九、为什么“Reference Solution 能过”仍然证明不了 Benchmark 有效

Reference 只能证明：

> 至少有一种作者知道的方案可以通过。

它证明不了：

- untouched baseline 一定失败；
- Agent 看不到答案；
- 另一个合法方案也能通过；
- hardcode 不能通过；
- permanent / recoverable 真能被区分；
- Verifier 没写错业务 Oracle。

这次真实 Dynamic Smoke 就抓到过：visible contract 已经要求 VIP 参数 `0.9`，Agent 也修成 `0.9`，但 hidden verifier 还保留旧的 `0.8`，于是合法方案被误杀。

所以 Reference 是必要证据之一，但不是“Benchmark 认证证书”。

---

## 十、Skill 为什么区分 PASS / WARN / BLOCK / NOT RUN

如果只有 PASS / FAIL，命题流程很容易走两个极端：要么什么风险都挡住，要么为了推进把硬问题写成备注。

Skill 的等级大致可以这样理解：

### PASS

证据足够支持这个 Gate。

### WARN

有真实风险，但边界清楚，不会直接让 Case 失真。

例如当前只有一个合理 JVM 表面、动态 LLM eval 还没跑。

可以继续，但不能吹“已经证明跨生态泛化”。

### BLOCK

题本身已经失真，必须回去改。

例如：

- Prompt 直接泄露 expected label；
- untouched baseline 就能 PASS；
- reference FAIL；
- Verifier 可以被明显手写结果骗过；
- 正常 Agent-visible 信息不足，只能猜；
- Verifier 只接受唯一 Patch。

### NOT RUN

还没有执行这个检查。

例如动态 LLM Evaluation 未启动。

它不能冒充 PASS。

这次 Skill 实际使用时还暴露过一个小问题：第一次审计明明已经发现静态 hard blocker，却把总体写成 `BLOCKED`。后来明确修正：**已有证据证明题有硬缺陷就是 BLOCK；只有因为环境/证据缺失，根本无法形成判断时才是 BLOCKED。**

---

## 十一、为什么 HOLDOUT 不能一边看一边改

DEV 和 HOLDOUT 的区别不是目录名字，而是使用纪律。

### DEV

允许反复运行、看 Trace、根据失败修改 Runtime。

### HOLDOUT

Candidate 基本冻结后再看，用来检查没有在 DEV 上直接 case chasing。

一旦发生：

```text
看 HOLDOUT 结果
→ 按这些 Case 修改 Runtime
```

这批 HOLDOUT 就已经被消费了。

后续如果还要声称“未见数据泛化”，就应该换新的 Holdout。

当前 Anti-Loop v3 Native Baseline 0 建立时：

```text
HOLDOUT_DYNAMIC_TRIALS_EXECUTED = 0
```

这不是“忘了测”，而是刻意保留后面的泛化验证资源。

---

## 十二、Skill 在整个评测体系里处于什么位置

现在可以把几个文档和组件放在一起看：

```text
AGENTS.md
→ 告诉 Codex：Benchmark 是项目正式工程资产，不能 Case Patch

        ↓

docs/EVALUATION.md
→ 讲 Evaluation System 怎么运行、怎么比较、怎么解释

        ↓

Benchmark Author Skill
→ 讲一道 Benchmark 应该怎么设计、怎么审核

        ↓

harness-contract.md
→ 告诉 Skill 当前 Harness 的底层接入规则

validity-gates.md
→ 告诉 Skill 怎么判断 Leakage / Solvability / Verifier / Generalization
```

而面试学习资料：

```text
03_Benchmark驱动迭代
→ 讲整个真实演进故事

04_Benchmark Author Skill
→ 深挖“命题”这一段为什么这么设计
```

所以 Skill 不是一套孤立的新东西，它是 Evaluation System 的**Benchmark Authoring 层**。

---

## 十三、Skill 自己也不是万能的：这次真实使用暴露了什么

最值得讲的地方恰恰是：Skill 写出来以后，并不是一次设计完就结束。

我们真正拿它审 Anti-Loop Suite，经历过：

```text
Skill 静态审计
→ 找到旧 Benchmark 多个 validity defect
→ Hardening
→ Independent Review
→ 又发现 Grounded STOP 没有真正因果绑定
→ 再修
→ Dynamic Smoke
→ 又找到 hidden oracle False Reject
→ 再修
→ 最后建立 Measurement v3 和 Freeze
```

这说明：

> **写规范不能替代真实执行。**

Skill 擅长把命题流程变得系统，但真正的 LLM 会走出作者没预料到的路径，所以最终仍然需要 Dynamic Smoke + Human Review。

最健康的关系是：

```text
Skill
负责在生成前挡住明显坏题

Deterministic Validation
负责验证 Contract / Reference / Mutation

Dynamic Smoke
负责验证真实 Agent 路径下会不会误判

Human Review
负责在关键样本上检查 Measurement 是否符合真实语义
```

---

## 十四、什么时候不应该使用这个 Skill

它不是每次跑 Evaluation 都要加载的重型流程。

例如：

```text
“帮我看昨天 task022 为什么失败”
```

这是 Evaluation Analysis，不需要重新走一遍完整命题流程。

真正适合调用 Skill 的情况是：

- 新建 Benchmark；
- 设计 Benchmark Family；
- 系统性修改 Case；
- 审核某个 Case 是否有效；
- 检查 Leakage / Solvability；
- 设计 Counterfactual；
- 审查 Verifier 是否过拟合；
- 调整 DEV / HOLDOUT。

否则 Skill 本身也可能变成流程负担。

---

# 十五、面试怎么问

## 问题 1：为什么把 Benchmark Authoring 做成 Skill，而不是写一份文档？

**面试官在判断什么：**你是否真的理解“规范”和“可执行开发流程”的区别。

**核心回答：**

> 单纯文档很容易在每次出题时被跳过，所以我把 Capability Spec、Suite Audit、Oracle、Leakage、Solvability、Counterfactual、Verifier Mutation 等步骤做成 Codex 项目级 Skill。它不是替 Agent 解题，而是让 Codex 在创建或审核 Benchmark 时按固定 Gate 获取 evidence。文档仍然保留长期 Contract，Skill 负责把这些约束落实到工作流。

**追问陷阱：**不要说 Skill 能保证 Benchmark 一定正确。后来仍然靠 Dynamic Smoke 抓到了 hidden oracle 等问题。

---

## 问题 2：怎么防 Benchmark 反向过拟合当前 Runtime？

**核心回答：**

> 我要求先冻结 Capability Claim，再设计 Benchmark，最后才检查 Runtime 接口兼容。命题时不能把当前 Runtime 的 regex、类名、错误字符串、阈值或 Task ID当作正确答案。比如依赖失败测的是“有没有合法恢复路径”，不是“有没有出现 ModuleNotFoundError”。

---

## 问题 3：Leakage 和 Solvability 冲突怎么办？

**核心回答：**

> 不靠删光信息解决。答案型 README 要去掉，但必须补真实调查入口，让 Agent 通过 build、probe、HTTP 或配置状态自己得到证据。如果删完以后正常工程师只能猜，那就是 unsolvable，仍然是 BLOCK。

---

## 问题 4：为什么 Verifier 不能要求固定工具顺序？

**核心回答：**

> 除非工具顺序本身就是 Capability，否则固定 `service_probe.py → edit → test` 会误杀 curl、Python client、已有 CLI 等合法方案。Verifier 应验证可信 observation、业务 invariant 和 causal relation，而不是作者偏好的操作步骤。

---

## 问题 5：为什么不让 LLM Judge 直接评？

**核心回答：**

> 对明确的代码、状态、HTTP、artifact、build/test 结果，deterministic verifier 更容易复现和审计。LLM Judge 更适合开放质量维度，但如果 Ground Truth 可以由程序直接验证，我优先用程序。即使以后加 LLM Judge，也不会让它代替业务 invariant 和 trust boundary。

---

# 十六、简历要不要单独写 Skill

通常**不建议把 Skill 单独写成一条简历 Bullet**，除非岗位很强调 AI Coding Workflow / Eval Infrastructure。

它更适合作为“Benchmark 驱动迭代”的深挖内容。

例如面试官问：

> “你怎么保证自己加的新 Benchmark 不是专门给当前 Agent 喂答案？”

这时再展开：

```text
Capability-first
Counterfactual
Leakage / Solvability
Verifier Mutation
DEV / HOLDOUT
Dynamic Smoke
```

如果一定要单独描述，可以写成：

> **Benchmark 质量治理：**将 Coding Agent Benchmark 的 Capability 定义、信息泄露、可解性、对照 Case 与 Verifier 反作弊审计固化为仓库级 Codex Skill，约束 Benchmark 先于 Runtime 设计，并通过 deterministic mutation 与真实 Agent Smoke 校验 Case 的测量有效性。

但它最好和主线 Bullet 一起出现，否则容易显得像“写了一份 Prompt 模板”。

---

# 十七、真正应该记住什么

Skill 最值得记住的不是 Gate 0～Gate 8 的编号，而是下面四条：

1. **先定义能力，再设计题。** 不从当前 Runtime 的实现细节反推标准答案。
2. **不泄题，也不能让题只能猜。** Leakage 和 Solvability 必须一起看。
3. **Verifier 既要防作弊，也要允许合法多解。** Reference 是一种解，不是唯一 Patch。
4. **命题规范也必须接受真实 Agent 检验。** 静态 Gate 全过以后，Dynamic Smoke 仍可能发现 False Reject / Oracle Conflict。

一句话总结：

> Benchmark Author Skill 的价值不是“帮 Codex 更快地生成几个 task 文件”，而是强迫命题过程回答：如果 Agent 通过了，这到底证明了什么；如果失败了，我们有没有资格把责任归到 Runtime 上。
