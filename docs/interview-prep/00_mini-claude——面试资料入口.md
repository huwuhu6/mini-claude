# mini-claude——面试资料入口

> 这套资料只围绕简历主动暴露的点准备，不按源码目录顺序背项目。
>
> 目标：简历每写一个 Hook，都必须能承受 5～10 轮连续追问；如果一个点只能讲概念、讲不出失败 Case、代码裁决点和评测口径，就不应该出现在简历上。

## 一、建议简历只主动暴露三条主线

### 1. Agent 失败循环治理

建议 Hook：

> **Agent 失败循环治理：**针对 Coding Agent 在网络不可达、依赖安装和重复编辑等场景中的持续空转，将工具调用归一化为 `action + target` 的 Intent Fingerprint，并结合 Failure Category / Strategy Fingerprint、环境探针和工作区状态守卫识别重复失败与 0-Diff 写入；对不可恢复循环执行 Runtime 级硬熔断。离线依赖专项 Benchmark 中通过率由 2/5 提升至 5/5，平均轮次由 17.4 降至 1.0。

这条线的核心不是“做了一个 hash”，而是回答 Coding Agent 最关键的 Runtime 问题之一：**什么时候应该继续探索，什么时候必须停止。**

对应资料：`01_Agent失败循环治理——简历驱动追问链.md`

最希望面试官第一问：

> 你的 Intent Fingerprint 和 Strategy Fingerprint 到底分别是什么？怎么避免把合理探索误判成死循环？

---

### 2. 上下文成本治理

建议 Hook：

> **上下文成本治理：**针对测试日志和 Shell 输出直接回填上下文造成 Token 膨胀的问题，将超长 Tool Output 落盘并仅返回 Head/Tail 与可定位路径，配合窗口化读取、分层压缩和 Tool Call 链完整性清洗；两个长日志专项 Benchmark 均保持 5/5 完成率，Token 分别降低 66.6% 和 68.8%。

这条线不要讲成“做了 summarization”。真正值得讲的是：**如何减少上下文，同时不把真正的错误信息删掉。**

对应资料：`02_上下文成本治理——简历驱动追问链.md`

最希望面试官第一问：

> 你把日志截断以后，如果真正的报错刚好在中间，不就把 Agent 搞瞎了吗？

---

### 3. Benchmark 驱动迭代

建议 Hook：

> **Benchmark 驱动迭代：**构建 `baseline/config/verify` + Shadow Workspace + Trace + Run Manifest 的自动化评测框架，截至当前主线沉淀 17 个专项任务；固定 Fixture、切换 Agent Commit 做对照实验，统一比较完成率、Token、轮次、工具调用和失败路径，并据此淘汰过度特化的验证工具和无收益规则。

这条线不是为了证明“我会写测试框架”，而是证明：**Agent 的行为具有随机性，不能凭单次 Trace 或主观感觉决定 Harness 是否变好。**

对应资料：`03_Benchmark驱动迭代——简历驱动追问链.md`

最希望面试官第一问：

> Agent 输出有随机性，你怎么证明一个 Runtime 改动真的变好了，而不是这次模型刚好抽卡抽得好？

---

## 二、三条线之间怎么串起来

不要把三条 bullet 当三个独立功能。面试时最好形成同一条工程闭环：

```text
观察 Trace
  ↓
发现 Agent 空转 / 上下文爆炸
  ↓
提出一个最小 Runtime 改造
  ↓
设计能稳定复现问题的 Benchmark
  ↓
固定 Fixture + 多次运行建立 Baseline
  ↓
改 Runtime
  ↓
比较完成率、轮次、Token、失败路径
  ↓
有效：保留
无效或副作用大：回退 / 删除
```

因此：

- Failure Loop 是“可靠性问题”；
- Context Governance 是“成本与可用性问题”；
- Benchmark 是判断前两者是否真的改善的证据系统。

这比把 mini-claude 讲成“我仿了一个 Claude Code”更有价值。

---

## 三、项目 40 秒总述

> mini-claude 是一个面向本地代码仓库的 Coding Agent Runtime。我没有把重点放在模型训练，而是研究 Harness：模型如何安全调用文件和 Shell 工具、怎么保持 workspace 和 Shell 状态、什么时候继续 Agent Loop、什么时候应该硬停止，以及如何控制长任务的上下文成本。后面我又搭了一套 Shadow Workspace + Trace + deterministic verify 的 Benchmark，用固定任务反复比较 Runtime 改动，很多功能不是越加越多，反而是通过评测发现过度工程后删掉的。

这个总述后，主动停住，让面试官从三个 Hook 里选。

---

## 四、项目整体链路必须会讲

牛客近期 Coding Agent / Agent 后端面经经常直接问：

> “从给 Claude Code / Codex 输入一句话，到最终结果出来，中间到底发生了什么？”

mini-claude 的口径：

```text
CLI 接收用户任务
→ 确认 / 绑定 WorkspaceAuthority
→ RuntimeContext 建立 workspace + ShellSession
→ Provider 发送 messages + tool schemas 给 LLM
→ LLM 返回普通文本或 tool_calls
→ Runtime 在工具执行前经过权限、环境、LoopGuard / StateGuard 等检查
→ 执行 read/edit/bash 等工具
→ Tool Result 写 Trace / Session JSONL，并回填模型上下文
→ LLM 基于结果继续下一轮
→ 满足任务完成条件后结束
→ Benchmark 场景下再由独立 verify.py 验证 Shadow Workspace 最终状态
```

这里必须明确：**LLM 决定“想调用什么”，Runtime 决定“允不允许执行、执行后如何记录、是否应该继续循环”。**

---

## 五、不要主动写进简历的内容

这些不是没用，而是作为追问后的储备：

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
```

原因很简单：简历攻击面有限。三条主线已经足够面试官连续深挖，没必要再把所有模块平铺出来。

尤其不要主动写：

- “生产级 Claude Code 替代品”——当前项目明确不是；
- “通用语义死循环识别”——当前主要是 deterministic normalization + rule-based failure classification；
- “完整多 Agent 系统”——SubAgent / Team 属于扩展能力，不是主线；
- “Benchmark 证明通用 Agent 能力”——它是工程回归与策略比较工具，不是学术 benchmark。

---

## 六、当前三个最危险的口径问题

### 1. Intent Fingerprint ≠ Strategy Fingerprint

这两个不要混说。

- Intent Fingerprint：`LoopController.CommandNormalizer` 生成，核心是 `tool + action + target`，用于判断近期是否重复同一操作意图。
- Strategy Fingerprint：`Failure Intelligence` 中的更粗粒度策略类别，例如 `NETWORK_PACKAGE_INSTALL`、`NETWORK_DOWNLOAD`、`LOCAL_FILE_IO`。
- Failure Fingerprint：当前 `FailureAnalyzer` 使用 `FailureCategory::StrategyFingerprint` 组合，判断同类根因 + 同类策略是否持续失败。

面试官一旦问到，先把三者拆开，再讲组合关系。

### 2. 失败循环的“25% 轮次下降”不要和错误实验混用

历史上 `26.3 → 19.7`、`82.9s → 63.2s` 来自上下文 Stubbing / Todo 相关实验，不适合直接当作 Failure Loop 的专项收益。

失败循环当前更可防守的数字来自 `task_015_offline_dependency_block`：固定专项 Fixture 下，改造后 5/5 通过，平均轮次从 17.4 降到 1.0。简历如果要报数字，必须注明“专项 Benchmark”。

### 3. Benchmark 数量已经不是 16

当前 `main` 已存在 `task_017_stateful_shell_env`，所以旧简历“16 个专项 Benchmark”已经过期。当前口径是 **17 个**。

---

## 七、牛客面经映射：为什么重点准备这三条

近期牛客 AI Agent / Coding Agent 面经出现的高频问题，与 mini-claude 三条线高度重合：

1. Agent Loop 在什么条件下继续、什么条件下结束？
2. Agent 失败 / 中断后怎么处理，重试如何保证安全？
3. Agent 上下文窗口满了怎么办，有哪些压缩方式？
4. Claude Code / Coding Agent 怎么检索代码？
5. 评测集怎么构建？观测哪些指标？
6. 线上大量 Trace / Log 怎么转成有限的离线评测集？
7. 如何记录每轮实验的耗时、Token、结果和失败路线？
8. 改 AGENTS.md / Skill / Harness 后，怎么证明效果真的提升？

这套资料不会把这些题重新做成一份“Agent 八股大全”，而是尽量让它们从你的简历项目自然长出来。

---

## 八、建议复习顺序

```text
01 Agent 失败循环治理
→ 02 上下文成本治理
→ 03 Benchmark 驱动迭代
→ 回头用 40 秒项目总述把三条串起来
```

每一条都按固定结构复习：

```text
简历写法
→ 第一问 30 秒回答
→ 当前代码真实机制
→ 一个真实 Bad Case
→ 方案演进
→ 指标怎么测
→ 反例 / 已知局限
→ 牛客高频追问
```

---

## 九、主要事实来源

### 仓库

- `README.md`
- `docs/HANDOFF.md`
- `src/core/loop_controller.py`
- `src/core/failure_intelligence/`
- `src/core/runtime_context/`
- `src/core/compression.py`
- `src/core/tools/base_tools.py`
- `eval_runner.py`
- `compare_reports.py`
- `sandbox/tasks/`
- `eval_reports/anti_loop_refactor/`
- `eval_reports/log_to_file_refactor/`

### 关键 Commit

- `f3044da`：Runtime Core / Failure Intelligence / Eval Harness 初版
- `63880b7`：LoopGuard 意图指纹增强
- `4d8cc16`：Stubbing 微压缩实验
- `c8db40a`：禁用 TodoWrite schema，验证“功能越多不一定越好”
- `3d2a9a4`：移除过度特化的内置静态验证工具
- `d360134`：长 Tool Output 文件化 + 渐进式读取
- `b115d5b`：环境探针 + Workspace State Guard
- `821e292`：修正 stalled-code-edit Benchmark 评判契约
- `73b3283`：Shell 环境持久化 Benchmark，新增 task_017

### 牛客外部面经（用于补追问，不作为项目事实）

- 字节跳动 AI Agent 开发岗面经：评测集构建、指标、工具调用、上下文压缩、Claude Code 检索方式
  - https://www.nowcoder.com/discuss/922659486983036928
- 火山引擎方舟 Managed Agent 27 届秋招：Agent Loop、Context Engineering、实验记录与效果验证
  - https://www.nowcoder.com/discuss/917561494512861184
- Java 后端 AI Agent 方向面经：Agent、Memory、Tool、RAG、SSE、监控等
  - https://www.nowcoder.com/feed/main/detail/5a4f138ab80f4d05a104244ff72052f0
- Agent 开发面经总结：Agent 失败/中断处理、重试安全、RAG 更新等
  - https://www.nowcoder.com/discuss/877151327091027968
