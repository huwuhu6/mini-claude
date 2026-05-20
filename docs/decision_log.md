# Decision Log

> 本文只记录真正重要的架构决策，不记录普通实现细节。每条决策包含背景、选择、拒绝方案、状态和后续验证。事实优先级：当前 repo 审计结果优先；历史模型总结只作为补充。

## ADR-001：项目定位为本地 AI Coding Agent Runtime，而不是普通聊天机器人

状态：Accepted

背景：项目目标是面试导向，不只是完成一个产品，而是让系统可解释、可测试、可防守。当前 repo 已从 `s_full.py` 单体重构为模块化结构，包含 CLI、Agent、RuntimeContext、Tools、Tracing、Failure Intelligence、Evaluation、Providers、Skills 等模块。

决策：将项目定位为“本地运行的 AI Coding Agent Runtime”。核心不是简单调用 LLM，而是围绕本地软件开发任务构建运行时能力：workspace 绑定、文件和 shell 工具、权限边界、shell 状态、trace 可观测性、failure intelligence、evaluation。

拒绝方案：仅定位为 Claude Code clone；仅定位为 function calling demo；仅强调 prompt engineering。

原因：AI 应用开发岗位更看重模型应用的工程化能力，包括工具调用、状态管理、安全边界、评测和可观测性。

后续验证：用端到端 demo 证明 runtime 闭环；用测试和 trace 证明不是概念堆叠。

## ADR-002：采用多模型协作开发工作流

状态：Accepted

背景：早期单独使用 Claude Code，后来引入 Gemini 作为决策者，但 Gemini 容易将项目带偏。当前工作流改为：ChatGPT 做决策和写提示词，DeepSeek 做 reviewer/challenger，ChatGPT 吸收 review 并修正方案，Claude Code 执行 repo 修改，Claude Code 输出结果，ChatGPT review 结果并更新文档/roadmap。

决策：使用 ChatGPT 作为 Tech Lead / 决策者，DeepSeek 作为 adversarial reviewer，Claude Code 作为实现工程师。

拒绝方案：单独依赖 Claude Code 决策和实现；让 Gemini 长期主导架构决策；不做 review，直接把 prompt 发给 Claude Code。

原因：多模型分工可以降低单个模型带偏项目的风险。ChatGPT 负责收敛目标和架构，DeepSeek 负责质疑，Claude Code 负责具体修改。

## ADR-003：引入 RuntimeContext 和 Persistent ShellSession

状态：Accepted / Partially needs verification

背景：Coding Agent 执行 shell 命令时，如果每次 subprocess 都是无状态的，cwd 会丢失，Agent 会反复 `cd`，并且无法形成类似 IDE 的持续工作态。

决策：引入 `RuntimeContext`，聚合 workspace_root、ShellSession、PathResolver、CommandPolicy。`ShellSession` 维护逻辑 cwd、命令历史，并对纯 `cd` 做轻量处理。当前实现不是完整 PTY，而是通过跟踪 cd 命令维护状态。

拒绝方案：每次命令都显式传 cwd；禁止多命令 shell 执行；一开始就实现完整 PTY。

原因：非 PTY 的 ShellSession 是轻量折中，复杂度较低，同时解决大部分本地开发任务中的 cwd 连续性问题。

当前问题：`ShellSession.reset()` 存在已确认 bug，需要 P0 修复；需要验证 bash 与 workspace authority 的完整结合。

## ADR-004：使用 WorkspaceAuthority 作为统一权限边界

状态：Accepted / Needs verification

背景：Coding Agent 能读写文件和执行命令，必须限制在用户明确授权的工作区中。单纯依赖 prompt 或分散白名单不可靠。

决策：使用 `WorkspaceAuthority` 表达权限边界：primary_root 和 additional_roots，并将 workspace 作为 runtime ownership boundary。

拒绝方案：只依赖当前 cwd；分散在各工具中的 path whitelist；只用 prompt 告诉模型不要访问外部文件。

原因：执行层权限约束比语言层约束更可靠。面向本地文件系统的 Agent 必须有明确边界。

当前问题：WorkspaceAuthority 测试不足；需要验证 BaseTools 是否完整接入 authority；需要明确 symlink、绝对路径、`..` escape 策略。

## ADR-005：CommandPolicy 替代 blanket shell ban

状态：Accepted / Needs verification

背景：早期如果粗暴禁止 `&&`、`|`、`$` 等 shell 控制字符，会提升安全性，但也会严重损害可用性。Coding Agent 经常需要 `cd dir && python test.py` 这类合理命令。

决策：引入 `CommandPolicy`，采用语义规则：允许常见安全组合，例如 `&&`；拦截高风险模式，例如 `rm -rf`、pipe to shell、subshell 等。

拒绝方案：完全禁止 shell 控制字符；完全放开 shell；立即做复杂 AST command parser。

原因：这是安全性和可用性的折中。规则式策略简单、可测、可迭代。

## ADR-006：采用 Task/Turn/Tool 三层 Trace，而不是复杂观测平台

状态：Accepted

背景：Agent 的执行过程很难调试。普通日志难以表达任务、模型轮次和工具调用之间的结构关系。但项目当前阶段不适合引入 DB、OpenTelemetry 或复杂异步 exporter。

决策：采用轻量三层 trace：`TaskTrace -> TurnTrace -> ToolTrace`，并以 JSON 持久化。

拒绝方案：直接接入分布式 tracing；写入数据库；只保留普通文本日志。

原因：JSON trace 低依赖、易检查、易做 fixture、易连接 evaluation，也适合面试展示。

后续验证：固化 trace schema；增加 schema version；增加脱敏策略；确保 TraceAnalyzer 与当前 schema 一致。

## ADR-007：引入 Failure Intelligence，而不是简单 retry limit

状态：Accepted / Needs verification for end-to-end behavior

背景：历史 trace 显示 Agent 在网络不可达时会陷入 pip install retry storm。简单 max iteration 或 lexical loop guard 无法识别“命令参数不同但根因相同”的失败。

决策：引入 Failure Intelligence，包括 FailureCategory、Recoverability、FailureSignature、StrategyFingerprint、FailureMemory、FailureEscalationPolicy。目标是让 runtime 从“看到失败”升级为“理解失败模式并决策”。

拒绝方案：简单 retry limit；仅基于 args_hash 的重复检测；早期引入 ML classifier。

原因：规则式 failure intelligence 可解释、可测试、可复现，适合当前项目阶段。

当前问题：需要验证真实 agent loop 中是否完整接入；分类可能过粗；escalation 被 LLM 忽略时需要硬终止兜底。

## ADR-008：Evaluation 使用 trace-driven metrics，而不只看 pass/fail

状态：Accepted / Needs verification

背景：Agent 可能最终完成任务，但过程可能很差，例如重复调用工具、失败后不换策略、不运行测试或上下文漂移。只看 pass/fail 不足以评估 Agent 质量。

决策：从 trace 中计算过程指标，例如 total_turns、duplicate_tool_ratio、loop_guard_trigger_count、compression_count、escalation_count、runtime_degradation_score。

拒绝方案：只看最终任务是否成功；只看人工主观评价；在没有 trace 的情况下做 eval。

原因：过程指标更能反映 Coding Agent 的 runtime 质量，也更符合 AI 应用开发岗位的评测思维。

## ADR-009：SubAgent / Teammate / MessageBus 暂不作为主线能力

状态：Accepted as positioning decision

背景：当前 repo 审计显示存在 SubAgent、TeammateManager、MessageBus 等模块。历史总结中还提到 Shadow Workspace + 2PC、MessageBus 强一致锁等内容，但当前审计没有确认这些都是当前已实现主线能力。

决策：在未完成验证前，将多 Agent 协作层定位为 experimental，不作为第一面试主线。

拒绝方案：把多 Agent 作为核心卖点；在未验证 Shadow Workspace + 2PC 的情况下写入正式架构；夸大 Teammate 为真实并发执行系统。

原因：多 Agent 容易被质疑过度设计。当前最有面试价值的主线是 runtime、workspace safety、failure intelligence、trace、evaluation。

## ADR-010：重构当前eval_runner.py写死代码转为动态读取sandbox/tasks中的用例

---

## ADR-011：引入 V3 联合熔断防线（Circuit Breaker），替代软 Escalation

状态：Accepted

背景：v2 基准评测（`v2_baseline_fi_soft`）中 trace 数据表明：
1. **LoopGuard 从未触发**（`loop_guard_trigger_count: 0`），因为 LLM 通过更换命令字面量（加 `/d`、`chcp 65001`、`set PYTHONIOENCODING`、`-X utf8`、`2>&1` 等噪音）逃避了字面量去重。
2. **FI 软 Escalation 被 LLM 完全忽略** — 第 16 轮开始注入 escalation 消息，但 LLM 继续死磕到第 35 轮上限（`LOOP_ABORTED`），消耗 19.6 万 Token。
3. 缺少前后台防御协同 — LoopGuard 拦截后 Failure Intelligence 一无所知，反之亦然。

决策：引入 V3 三层联合熔断防线：

1. **CLI 意图标准化（Command Normalization）**：在计算命令哈希前，通过正则剥离 `cd`、`chcp`、`set ENV`、`2>&1`、`> redirect`、`-X` flag 等噪音，提取 `{action, target}` 语义指纹。`python run_test.py`、`cd /d ... && python run_test.py`、`chcp 65001 && python run_test.py` 统一归一化为 `EXECUTE::run_test.py`。

2. **意图感知 LoopGuard + Guard-to-FI 管道**：对归一化后的意图指纹进行跨轮频率检测。拦截时向 FI 内存注入 `LOOP_GUARD_PREVENTED` 虚拟失败记录，消除前后台数据盲区。

3. **物理硬断路器（Hard Stop Circuit Breaker）**：同一行为意图累积 5 次失败（真实 `TOOL_CRASH` + 虚拟 `LOOP_GUARD_PREVENTED` 合计）时，运行时抛出 `RuntimeEscalationException`，物理掐断 Agent 循环，不再向 LLM 返回任何 tool_result。

拒绝方案：
- 仅依赖软 Escalation（v2 已证伪 — LLM 忽略提示）
- 仅升级字面量去重规则（v1 已失效 — LLM 换参数逃避）
- 简单降低 max_iterations（治标不治本）

核心文件：
- `src/core/loop_controller.py` — 新建，三层防线统一实现
- `src/agent/mini_claude_agent.py` — 集成 V3 Controller，捕获 `RuntimeEscalationException`
- `src/core/tracing/models.py` — 新增 `CIRCUIT_BROKEN` 状态

预期效果：任务 001（db_port）在第 5 轮被硬熔断，`final_status = "CIRCUIT_BROKEN"`，Token 消耗从 19.6 万降至约 1-2 万。

已知局限：由于主代理未挂载 2PC 影子回滚机制，导致硬熔断时脏数据发生残留泄漏，引发评测判定非预期通过。该问题已作为高危缺陷分发至缺陷池。

## ADR-012：基于版本化状态大账本与事务性异常硬回滚的评测基础设施重构

状态：Accepted

背景：V3 Circuit Breaker（ADR-011）引入硬熔断后，Agent 在任务执行过程中的文件变更残留到工作区，verify.py 因此误判任务通过。task_001_db_port 在 v3 基线评测中因脏数据导致非预期通过，破坏了评测的客观性。根本原因：Agent 原地直写，没有事务边界，熔断后缺乏回滚能力。

决策：引入版本化状态大账本 + 事务性异常硬回滚。

1. **版本化状态大账本（Versioned State Ledger）**：每轮工具调用前快照工作区受管文件的 hash 清单，作为回滚基准。
2. **事务性异常硬回滚**：熔断触发时根据快照原子擦除所有变更文件，还原到执行前基准状态，确保 `verify.py` 看到的是干净起点。
3. **沙箱隔离**：每个评测任务在独立沙箱目录中运行，任务间不共享状态，起始即隔离。

拒绝方案：
- 2PC Shadow Workspace（过度设计，与当前单进程 Agent 架构不匹配）
- 仅靠 Agent 自行清理（不可靠，LLM 可能忽略清理指令）
- 不做修复（脏数据持续破坏评测可信度）

核心文件：
- `src/core/loop_controller.py` — 集成回滚逻辑
- `sandbox/tasks/` — 任务沙箱隔离
- `verify.py` — 依赖物理快照判断

面试防御价值（DoD）：本决策通过引入异常硬捕获与沙箱擦除机制，彻底根治了 Agent 原地直写导致的脏数据残留漂移，使物理客观判定（verify.py）与控制层生命周期（Circuit Breaker）达成强一致性，为 A/B 对照实验提供了绝对可信的真理源。

验证：task_001_db_port 在 `v3_circuit_breaker` 基线中已正确返回 `FAIL (CIRCUIT_BROKEN)`，脏数据不再导致非预期通过。
