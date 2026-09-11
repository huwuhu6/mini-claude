# mini-claude——面试资料入口

> 这套资料不是按源码目录背项目，而是围绕几个真正值得面试追问的工程问题组织。
>
> 目标：隔一段时间以后不打开源码，也能重新讲清楚“为什么会出现这个问题、旧方案哪里不行、现在怎么做、怎么证明改动真的有效、还有什么边界”。

## 一、建议把项目理解成三条主线 + 一条深入资料

### 1. Agent 失败循环治理

核心问题：

> Coding Agent 遇到网络不可达、依赖失败、重复编辑、环境 blocker 时，什么时候应该继续尝试，什么时候应该停止？

这条线重点看：

```text
重复动作为什么不能直接等于死循环
→ Intent / Failure / Strategy evidence 怎么形成
→ Workspace 是否真的发生有效变化
→ Recoverable / Permanent 怎么区分
→ 为什么会误停或空转
→ Runtime 最终如何治理
```

对应资料：

`01_Agent失败循环治理——简历驱动追问链.md`

面试最希望被问：

> 你怎么避免把 Agent 合理的重复验证误判成死循环？

---

### 2. 上下文成本治理

核心问题：

> Coding Agent 跑测试、Shell、构建以后产生大量日志，如果全部塞回上下文，Token 和延迟会迅速膨胀；但直接截断又可能把真正错误删掉，怎么办？

这条线重点看：

```text
长 Tool Output 为什么会污染后续所有轮次
→ 为什么简单 truncate 不够
→ 输出落盘 + Head/Tail + 可定位路径
→ read_file 窗口化读取
→ 怎么用 Benchmark 证明成功率没掉、Token 真降了
```

对应资料：

`02_上下文成本治理——简历驱动追问链.md`

面试最希望被问：

> 日志落盘以后，真正报错如果在中间，Agent 怎么找到？

---

### 3. Benchmark 驱动迭代

这是现在最重要的一条“证据主线”。

它已经不只是：

> 我写了几个 Case，然后多跑几次看成功率。

真正的演进是：

```text
早期 Shadow Workspace + hidden verifier + Trace / Manifest
↓
发现 Benchmark 自己也可能泄题、不可解、误判
↓
设计 Benchmark Author Skill
↓
反审现有 Anti-Loop Suite
↓
修 False Accept / False Reject / Oracle Conflict
↓
增加 Observation / Evidence provenance
↓
把单一 PASS/FAIL 拆成
Business Outcome / Grounded Capability / Final Governance / Overall
↓
增加 Evaluation Freeze，防止长 Run 中代码和 Config 漂移
↓
冻结 Revision，建立 Native DEV Baseline 0
```

这条线最重要的不是某个分数，而是：

> **怎么证明一把用来指导 Runtime 优化的尺子本身可信。**

对应主资料：

`03_Benchmark驱动迭代——简历驱动追问链.md`

面试最希望被问：

> Agent 最后确实 STOP 了，你怎么证明它是因为正确的 blocker 停，而不是碰巧被另一个错误熔断？

---

### 4. Benchmark Author Skill（03 的深入资料）

这不是第四条独立 Runtime 主线，而是第 3 条评测主线里的“命题层”。

目录：

`.agents/skills/miniclaude-benchmark-author/`

它是给 **Codex** 用的仓库级 Skill，不是 MiniClaude Runtime 的 Skill。

它主要解决：

```text
要测的能力到底是什么？
↓
题目有没有偷偷泄答案？
↓
删掉提示以后是否还能正常解？
↓
STOP / RECOVER 有没有真正的 Counterfactual？
↓
Verifier 能不能被改测试、硬编码、假 artifact 骗过？
↓
另一个合法实现能不能通过？
↓
这题是在测通用 Capability，还是在迎合当前 Runtime？
```

对应深入资料：

`04_Benchmark Author Skill——怎么避免出一道“看起来像评测”的假题.md`

复习时先看 03，再看 04。否则容易只记住一堆 Gate，而不知道为什么项目后来需要这些 Gate。

---

## 二、三条主线是怎么串起来的

整个项目可以用一条研发循环理解：

```text
真实 Agent Trace
↓
发现系统性失败
├─ 空转 / 误停 / 错误恢复 → 失败循环治理
└─ 上下文爆炸 / 日志污染   → 上下文成本治理
↓
把失败抽成可复现 Benchmark
↓
先确认 Benchmark 本身有效
↓
固定 Evaluation Revision 建立 Baseline
↓
做最小 Runtime 改造
↓
用同一把尺子比较
↓
有效则保留，无效或副作用大则回退
```

因此 Benchmark 不是项目旁边的一套测试脚本，而是前两条 Runtime 主线的**证据系统**。

如果没有可信评测，很容易出现：

```text
“我感觉这个 Guard 更聪明了”
“这一轮好像少跑了几次”
“这个 Case 终于绿了”
```

但无法回答：

> 到底是 Runtime 能力真的提升，还是模型随机性、Fixture 变化、Verifier 漏洞或者 Case Chasing？

---

## 三、项目 40 秒总述

> mini-claude 是一个面向本地代码仓库的 Coding Agent Runtime。我主要研究的不是模型训练，而是 Agent Harness：模型怎样安全调用文件和 Shell 工具、如何保持 workspace 和 shell 状态、失败后什么时候继续、什么时候停止，以及长任务怎么控制上下文成本。项目后面又建立了一套独立 Evaluation System，用 Shadow Workspace、隐藏 Verifier、Trace/Manifest 和 DEV/HOLDOUT Benchmark 验证 Runtime 改动；实际迭代中还反过来发现过 Benchmark 泄题、Verifier False Reject 和不可比实验，所以进一步补了 Benchmark Author Skill、四维 Measurement 和 Evaluation Freeze，最后才建立冻结 Revision 下的 Native DEV Baseline。

这段说完就停，让面试官自己选择追 Failure Loop、Context 或 Evaluation。

---

## 四、项目整体链路必须会讲

```text
用户给 Coding Task
↓
CLI / Runtime 建立 Workspace 与 Shell 环境
↓
Provider 把消息和 Tool Schema 发给 LLM
↓
LLM 决定下一步想调用什么工具
↓
Runtime 做权限 / 环境 / Loop / Failure / State 等治理
↓
执行 read / edit / bash 等 Tool
↓
Tool Result + Observation 写入 Trace，并回到模型上下文
↓
继续 Agent Loop
↓
成功、失败或治理条件结束任务
```

如果是在 Benchmark 中：

```text
固定 baseline
↓
复制 Shadow Workspace
↓
运行上面的 Agent 链路
↓
隐藏 Verifier 检查 evaluator-owned 事实
↓
Grader 分析 Business / Capability / Governance / Overall
↓
Manifest / Freeze 证明整批实验条件可追溯
```

必须明确：

> **LLM 决定“想做什么”；Runtime 决定“怎么执行和怎么治理”；Evaluation 决定“我们是否有资格说这次能力真的发生了”。**

---

## 五、不要主动写进简历的内容

下面这些适合作为追问储备，不要把简历变成组件清单：

```text
WorkspaceAuthority
ShellSession
CommandPolicy
Session JSONL
ProviderManager
SubAgent / Team / MessageBus
后台任务
Skills Loader
Prompt Cache 细节
Windows 兼容处理
Evaluation Controller 内部字段
Observation ID 具体 schema
```

尤其不要主动写：

- “生产级 Claude Code 替代品”；
- “通用语义死循环识别”；
- “完整多 Agent 系统”；
- “Benchmark 证明 MiniClaude 通用 Coding 能力”；
- “DEV Baseline 成功率等于 Agent 准确率”。

---

## 六、几个最危险的口径问题

### 1. Benchmark DEV Baseline ≠ 通用 Agent 排行榜

Anti-Loop Benchmark 主要服务于失败恢复、停止治理、Progress / Completion 等机制实验。

即使有 36 次 Native DEV Run，也不能写成“MiniClaude 整体准确率”。

### 2. Verifier FAIL ≠ Runtime 一定做错

真实开发里已经出现过：

```text
Hidden Oracle 写错
Baseline 自己就能过
Verifier 因环境变量崩溃
Provider timeout
Freeze false positive
```

所以评测红了以后先 attribution，再决定改 Runtime 还是改 Evaluation。

### 3. Outcome PASS ≠ Grounded Capability PASS

Agent 可能最终把 Java 代码修对了，但没有展示 Benchmark 声称的 failure→recovery trajectory。

这时可以：

```text
Business PASS
Grounded Recovery FAIL
```

不能把事实压成一个模糊 PASS/FAIL。

### 4. Grounded Recovery PASS ≠ Final Governance PASS

真实 task022 曾出现：

```text
503 → 恢复 → READY → 订单完成
→ Agent 仍继续行动
→ 后续撞上无关 blocker
```

正确解释是：

```text
Business PASS
Recovery PASS
Governance FAIL
Overall PARTIAL
```

真正需要优化的是 Completion，不是 Recovery。

### 5. HOLDOUT 不是看完还能一直叫 HOLDOUT

DEV 可以反复用于开发。

HOLDOUT 一旦看结果并据此修改 Runtime，就被消费了。当前 Native DEV Baseline 建立阶段保持：

`HOLDOUT_DYNAMIC_TRIALS_EXECUTED = 0`

---

## 七、当前最值得面试讲的真实失败故事

如果面试官愿意深挖，不要把所有类名都背出来，优先讲这几个故事：

### 故事 A：STOP 了，但原因不对

用于讲：Grounded Capability、Evidence Provenance、为什么最终状态不够。

### 故事 B：task032 Agent 修对 0.9，hidden verifier 还要求 0.8

用于讲：False Reject、为什么 Benchmark 自己也需要 Dynamic Smoke。

### 故事 C：task022 业务已经完成，Agent 又把自己跑进 blocker

用于讲：Business Outcome、Recovery、Completion 为什么必须分层。

### 故事 D：Freeze Gate 把正常 fixture state 当源码变化

用于讲：Evaluation Source 和 Generated Runtime State 的边界，以及可复现实验不是“记个 Git SHA”那么简单。

### 故事 E：verify_symbol_rename / TodoWrite 最后反而被删

用于讲：Benchmark 不只是证明“新增功能有用”，也可以证明“复杂功能应该删除”。

---

## 八、建议复习顺序

第一遍先建立项目故事：

```text
00 入口
→ 01 Agent 失败循环治理
→ 02 上下文成本治理
→ 03 Benchmark 驱动迭代
```

第二遍再深入 Evaluation：

```text
03 Benchmark 主线
→ 04 Benchmark Author Skill
→ docs/EVALUATION.md（需要查工程 Contract 时再看）
```

每条资料都不要只背结论，至少能讲：

```text
真实失败
→ 为什么旧方案不行
→ 当前怎么做
→ 一条真实数据怎么流
→ 如何评测
→ 有什么 trade-off
→ 现在还有什么边界
```

---

## 九、面试准备的底线

简历里任何数字都必须能回答：

```text
为什么做这组评测？
一条 Case 怎样才算通过？
跑了几次？
固定了什么条件？
这个数字能证明什么？
它不能证明什么？
```

如果只能记住“提升了 XX%”，却说不清 Fixture、Verifier 和实验条件，这个数字宁愿不写。

整个 mini-claude 项目最值得表现出来的成熟度，也不是“我造了很多 Agent 功能”，而是：

> **我会从真实失败中抽象机制，用可信评测约束自己的判断；如果证据证明一个功能没有收益，甚至会把它删掉。**
