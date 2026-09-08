# mini-claude——面试资料入口

> 这套资料只围绕简历主动暴露的点准备，不按源码目录顺序背项目。
>
> 目标：简历每写一个 Hook，都必须能承受 5～10 轮连续追问；如果一个点只能讲概念、讲不出失败 Case、代码裁决点和评测口径，就不应该出现在简历上。

## 一、建议简历只主动暴露三条主线

### 1. Agent 失败循环治理

建议 Hook：

> **Agent 失败循环治理：**针对 Coding Agent 在网络不可达、依赖安装和重复编辑等场景中的持续空转，将工具调用归一化为 `action + target` 的 Intent Fingerprint，并结合 Failure Category / Strategy Fingerprint、环境探针和工作区状态守卫识别重复失败与 0-Diff 写入；对不可恢复循环执行 Runtime 级硬熔断。离线依赖专项 Benchmark 中通过率由 2/5 提升至 5/5，平均轮次由 17.4 降至 1.0。

最希望面试官第一问：

> 你的 Intent Fingerprint 和 Strategy Fingerprint 到底分别是什么？怎么避免把合理探索误判成死循环？

对应资料：`01_Agent失败循环治理——简历驱动追问链.md`

### 2. 上下文成本治理

建议 Hook：

> **上下文成本治理：**针对测试日志和 Shell 输出直接回填上下文造成 Token 膨胀的问题，将超长 Tool Output 落盘并仅返回 Head/Tail 与可定位路径，配合窗口化读取、分层压缩和 Tool Call 链完整性清洗；两个长日志专项 Benchmark 均保持 5/5 完成率，Token 分别降低 66.6% 和 68.8%。

最希望面试官第一问：

> 你把日志截断以后，如果真正的报错刚好在中间，不就把 Agent 搞瞎了吗？

对应资料：`02_上下文成本治理——简历驱动追问链.md`

### 3. Benchmark 驱动迭代

建议 Hook：

> **Benchmark 驱动迭代：**构建 `baseline/config/verify` + Shadow Workspace + Trace + Run Manifest 的自动化评测框架，截至当前主线沉淀 17 个专项任务；固定 Fixture、切换 Agent Commit 做对照实验，统一比较完成率、Token、轮次、工具调用和失败路径，并据此淘汰过度特化的验证工具和无收益规则。

最希望面试官第一问：

> Agent 输出有随机性，你怎么证明一个 Runtime 改动真的变好了，而不是这次模型刚好抽卡抽得好？

对应资料：`03_Benchmark驱动迭代——简历驱动追问链.md`

---

## 二、三条线怎么串起来

```text
观察 Trace
→ 发现 Agent 空转 / 上下文爆炸
→ 设计能稳定复现问题的 Benchmark
→ 固定 Fixture + 多次运行建立 Baseline
→ 做最小 Runtime 改造
→ 比较完成率、轮次、Token、失败路径
→ 有效则保留，无效或副作用大则回退 / 删除
```

因此三条线不是三个孤立功能：Failure Loop 是可靠性问题，Context Governance 是成本问题，Benchmark 是判断前两者是否真的改善的证据系统。

## 三、项目 40 秒总述

> mini-claude 是一个面向本地代码仓库的 Coding Agent Runtime。我没有把重点放在模型训练，而是研究 Harness：模型如何安全调用文件和 Shell 工具、怎么保持 workspace 和 Shell 状态、什么时候继续 Agent Loop、什么时候应该硬停止，以及如何控制长任务的上下文成本。后面我搭了一套 Shadow Workspace + Trace + deterministic verify 的 Benchmark，用固定任务反复比较 Runtime 改动，很多功能不是越加越多，反而是通过评测发现过度工程后删掉的。

总述后主动停住，让面试官从三个 Hook 里选。

## 四、项目整体链路必须会讲

```text
CLI 接收用户任务
→ WorkspaceAuthority 确认 / 绑定 workspace
→ RuntimeContext 建立 workspace + ShellSession
→ Provider 发送 messages + tool schemas 给 LLM
→ LLM 返回文本或 tool_calls
→ Runtime 在执行前经过权限、环境、LoopGuard / StateGuard 等检查
→ 执行 read/edit/bash 等工具
→ Tool Result 写 Trace / Session JSONL，并回填模型上下文
→ LLM 继续下一轮
→ Runtime 根据完成 / 失败 / 熔断条件结束
→ Benchmark 场景由独立 verify.py 检查 Shadow Workspace 最终状态
```

必须明确：**LLM 决定“想调用什么”，Runtime 决定“允不允许执行、执行后如何记录、是否应该继续循环”。**

## 五、不要主动写进简历的内容

这些只作为被追问后的储备：

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

尤其不要主动写：

- “生产级 Claude Code 替代品”；
- “通用语义死循环识别”；
- “完整多 Agent 系统”；
- “Benchmark 证明通用 Agent 能力”。

## 六、三个最危险的口径问题

### 1. Intent Fingerprint ≠ Strategy Fingerprint

- Intent Fingerprint：`CommandNormalizer` 生成，核心是 `tool + action + target`，用于近期重复操作检测。
- Strategy Fingerprint：Failure Intelligence 中更粗粒度策略类别，如 `NETWORK_PACKAGE_INSTALL`、`NETWORK_DOWNLOAD`、`LOCAL_FILE_IO`。
- Failure Fingerprint：`FailureCategory::StrategyFingerprint`，用于判断同类根因 + 同类策略是否持续失败。

### 2. “26.3 → 19.7”不要当 Failure Loop 专项收益

该数字来自历史 Stubbing / Todo 相关实验，不适合证明死循环治理。失败循环更可防守的数字来自 `task_015_offline_dependency_block`：固定专项 Fixture 下 2/5 → 5/5，平均轮次 17.4 → 1.0。

### 3. Benchmark 数量已更新为 17

当前 `main` 已存在 `task_017_stateful_shell_env`，旧简历“16 个专项 Benchmark”已经过期。

---

## 七、牛客面经映射

以下只用来补“面试官会怎么问”，不作为项目事实。

近期牛客 Coding Agent / AI Agent 面经里，与你的三条主线直接重合的问题包括：

- 本地 Coding Agent 从输入任务到修改代码的完整链路；
- Tool 定义、注册、发现和调用的完整流程；
- Tool 失败后如何恢复 / 重试；
- 多轮上下文越来越长时怎么治理，压缩后怎么验证任务没被破坏；
- Agent 为什么会死循环，如何检测重复 Action、设置终止条件；
- Agent Memory / ChatMemory 如何设计；
- 如何评估 Agent 执行效果；
- 测试集怎么构造、参数实验怎么设计、如何验证优化有效。

实际参考：

- 牛客《面经分享》（AI Agent 开发，本地 Coding Agent）：https://www.nowcoder.com/feed/main/detail/d8f5de441b7a4f7ba32164ef37cce1fc
- 牛客《Agent 小厂面经》（状态、Tool 失败、测试集与参数实验）：https://www.nowcoder.com/feed/main/detail/d2812ee9853e41dc9d4885948143fd79
- 牛客《Java 后端一面面经》（AI Agent 方向，Memory、Tool、评估等）：https://www.nowcoder.com/feed/main/detail/5a4f138ab80f4d05a104244ff72052f0
- 牛客《基本都问的这些很多!》（Agent 重复 Tool 死循环）：https://www.nowcoder.com/feed/main/detail/f263dfc6561241b4a2ab2744c6eeb3ee
- 牛客《近期开发 AI 面经》（Agent 项目架构、复杂任务执行与评估）：https://www.nowcoder.com/feed/main/detail/e3425d60dec24ee1bcf1dc44420b0101
- 牛客《大厂 AI 编程面试：Claude Code 核心概念完整答案》（上下文清理与摘要压缩）：https://www.nowcoder.com/discuss/899962488224038912

## 八、建议复习顺序

```text
01 Agent 失败循环治理
→ 02 上下文成本治理
→ 03 Benchmark 驱动迭代
→ 回头用 40 秒项目总述把三条串起来
```

每条都按固定结构复习：

```text
简历写法
→ 第一问 30 秒回答
→ 当前代码机制
→ 一个真实 Bad Case
→ 方案演进
→ 指标怎么测
→ 反例 / 已知局限
→ 牛客高频追问
```

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
- `c8db40a`：禁用 TodoWrite schema
- `3d2a9a4`：移除过度特化的内置静态验证工具
- `d360134`：长 Tool Output 文件化 + 渐进式读取
- `b115d5b`：环境探针 + Workspace State Guard
- `821e292`：修正 stalled-code-edit Benchmark 评判契约
- `73b3283`：Shell 环境持久化 Benchmark，新增 task_017
