---
name: miniclaude-benchmark-author
description: 为 mini-claude 设计、生成并审计高质量 Benchmark Family 与 Validity Gate；仅在用户要求 Benchmark 设计、生成或修改时使用，单纯讨论时不创建任务文件。
---

# MiniClaude Benchmark Author + Validity Gate

这是仓库 `.agents/skills/miniclaude-benchmark-author/` 下供 Codex 使用的项目级 Skill；它不是 MiniClaude Runtime 的模块，也不要求 Runtime 实现 Skill loading、discovery 或 execution。它的首要产物不是一组看起来合理的文件，而是一个能回答下面问题的、可审计的 Benchmark Family：

> 如果这个 Benchmark 通过，它究竟证明了哪个 Runtime Capability？如果失败，失败说明的是 Runtime 能力、任务 Outcome、verifier，还是评测基础设施？

## 使用边界

- 用户只要求讨论或评审设计时：完成调研、Capability Spec 和 Gate 审计，但不要创建或修改 `sandbox/tasks/`。
- 用户明确要求生成 Benchmark 时：只要没有未解决的 `BLOCK`，即可创建新 Case；有明确边界和处置方式的 `WARN` 不应自动阻止有价值的 Case。可以运行低成本的 deterministic validation，不擅自启动真实 LLM Benchmark 或消耗 provider 配额。
- 本 Skill 默认只新增任务 fixture、verifier、reference solution 及确有必要的极小 fixture 支持；不修改 Runtime、现有 Case、`eval_runner.py` 或 Evaluation Harness，不顺手重构无关代码。
- `BLOCK` 必须回到对应设计阶段修改并重新检查，不能降级成 warning；已经证实的 verifier 作弊路径、答案泄露、不可解 fixture、reference 失败或唯一 patch 绑定，都是 validity `BLOCK`，即使动态 LLM eval 尚未运行也不能写成 `BLOCKED`。`WARN` 可以在用户目标仍有价值时继续，但必须缩窄 Claim、调整 DEV/HOLDOUT 用途或在最终报告中明确风险；`NOT RUN` 不能冒充 `PASS`。只有因为缺少必要外部证据、环境或依赖而无法形成该项结论时，才标记 `BLOCKED` 并说明下一步；动态评测尚未启动本身记为 `NOT RUN`。

## 决策等级与指令优先级

用户明确指令优先于本 Skill 的默认偏好，例如“优先 JVM”“先做一个 DEV Case”或“暂不做跨生态 Family”。本 Skill 仍负责识别会使结果失真的硬性问题，并遵守系统指令、仓库实际 contract 和安全边界；不能用用户指令绕过直接泄露答案、不可解、verifier 形同虚设或 harness contract 破坏。

每个 Gate 都输出一个内部结论：

- `PASS`：当前 evidence 足以支撑该 Gate 的 Claim。
- `WARN`：存在已知但可界定的质量风险，不足以证明整个 Case 无效；可以继续，但要记录影响、缓解方式和结论边界。例如目前只有一个合理的 JVM projection、暂未运行动态 LLM eval、或存在不泄露 label/solution 的轻微 evaluator 痕迹。
- `BLOCK`：会直接破坏测量有效性、信息隔离、可解性、行为判定或 harness contract，必须重设计后才能生成或宣称有效。例如 prompt 直接泄露 expected label、Agent 能看到 intended solution、untouched baseline 通过、reference 无法通过、verifier 绑定唯一 patch、或 Case 只能靠猜测完成。
- `NOT RUN`：检查尚未执行；在完成前不能写成 `PASS`。如果该检查是本次 Claim 的必要证据，状态应升级为 `BLOCKED`，而不是勉强生成。

只有 `BLOCK` 阻止生成。`WARN` 不等于“忽略风险”：对照覆盖不足时可把 Case 限定为 DEV，对尚未证明的泛化只写局部结论；如果用户要求的是严格的 HOLDOUT 结论，则必须先处理相应 warning。

开始 authoring 前阅读：

- [references/harness-contract.md](references/harness-contract.md)：当前 task contract、Shadow Workspace、fixture、Trace、DEV/HOLDOUT 和结果记账。
- [references/validity-gates.md](references/validity-gates.md)：Leakage、Solvability、Generalization、Verifier 与 mutation audit 的判定和回退路径。

## 强制执行流程

### Gate 0：确认任务模式与仓库状态

先判断用户是在讨论、审查，还是明确要求生成。然后读取当前 `git status --short`，保留用户已有改动；不要把工作区 dirty 当作可以忽略的背景。每次调用都重新扫描当前仓库和 `sandbox/tasks/`，不能依赖本 Skill 创建时的 task 数量、最大编号、生态分布或 Runtime 类名。

至少调查 `CLAUDE.md`、`README.md`、`docs/HANDOFF.md`、`eval_runner.py`、`compare_reports.py`、`src/core/evaluation/`、相关 Runtime Context/Trace/Failure Intelligence/LoopController 代码、`sandbox/eval_runtime/` 和全部现有任务的 `config.json`。用 `rg` 先定位相关内容，再按需深入读取。若当前仓库状态或评测契约无法确定，先补调查，不生成 Case。

### Gate 1：写出 Capability Spec，而不是从 Runtime 反推题目

在脑中或工作过程记录一份短 Spec：

1. Capability Claim：要测哪一个 Runtime 能力。
2. Positive proof：什么 Agent-visible evidence 能证明它。
3. Negative meaning：失败究竟说明什么，不说明什么。
4. 合法约束与合法恢复路径：哪些是业务要求，哪些是可以采用的不同实现。
5. 不在本题声称的内容：例如“没有误停”不等于“业务已成功”。

Claim 必须抽象到机制层，例如“根据失败、环境能力和替代路径判断是否还有合法恢复路径”，而不是“识别某个正则表达式”或“命中某个 Runtime 分支”。如果题目只能通过检查某个类名、错误字符串、阈值或内部函数证明，这是 `BLOCK`：重写 Claim 或放弃该 Case。

### Gate 2：Existing Suite Audit 与 Coverage Gap

解析当前所有 `config.json`，建立临时 matrix：Capability/Principle、ecosystem、failure family、topology、Observation sequence、workspace mutation、recoverable/permanent、DEV/HOLDOUT 和 verifier 类型。将新设计映射到这个 matrix：

- 已有同一 Claim 且表面变化没有改变 Observation、Action 或合法推理时，默认不新增复制 Case；如果用户明确需要一个教学/回归 DEV Case，可以继续但标记 `WARN`，不能把它计作新的 coverage。
- 有真实 coverage gap 时，优先补 gap；如果用户想验证的是 Family，先决定哪些维度有因果价值，再决定 Case 数量。
- 不机械要求 Python、JVM、Node、Shell 全覆盖；只有 ecosystem 改变工具、错误表达、可执行 Action 或证据形态时才跨生态。

如果只是“换文件名/换错误词/换数字”，不能宣称增加 coverage；这是 `WARN` 或“不新增”的理由。只有当这种变化被拿来声称测试了新的机制、或让 suite 分数产生误导时才是 `BLOCK`。优先改成有意义的 topology、failure family、recovery path、observation progression 或 task framing 变化。

### Gate 3：Internal Oracle 与 Counterfactual

先在 author/evaluator 侧确定 Oracle，再写 prompt：预期行为、允许的 Outcome、应被拒绝的伪造、最小充分 evidence，以及 STOP/CONTINUE、RECOVER/PERMANENT、PROGRESS/NO-PROGRESS 的判定。

对 stop/continue、recoverable/permanent、progress/no-progress 等判断，优先设计 Counterfactual pair：用户任务描述保持相同或高度相似，只改变决定行为的真实环境事实；一例应继续，另一例应停止，或一例有真实 progress，另一例只有表面变化。不要在 prompt 中写入 label。若某个能力确实不适合配对，说明原因并标记 `WARN`，不为了数量硬凑；只有在没有对照导致 Claim 无法区分时才 `BLOCK`。

### Gate 4：Family 设计与 DEV/HOLDOUT 分配

同一 Capability Claim 需要抵抗语言、工具链、错误表达和项目拓扑变化时，生成 Family，而不是一条孤立 Case。可从 ecosystem、toolchain、topology、failure family、合法 fallback、Observation progression、workspace mutation 和 task framing 中选择具有因果意义的变化；每个 Case 只承担一个主要区分点。

DEV 用来反复开发 Runtime；HOLDOUT 应引入 DEV 没有看过的表面，至少认真改变一个会影响观察或 Action 的维度，最好改变两个相互独立的维度。若当前能力只能合理构造 JVM Case，或暂时没有自然的跨生态 projection，不因此 `BLOCK`；在不声称跨生态泛化的前提下标记 `WARN`，并可先作为 DEV。看过 HOLDOUT 结果后若修改了 Runtime，该批 HOLDOUT 已被消费，后续结论必须使用新 Holdout。不要把当前 task id 写进 Runtime 或 Benchmark 的判定逻辑；发现 `task_XXX`、fixture 专属字符串、单题错误文本或 magic threshold，应先判断是明确实现耦合（`BLOCK`）还是尚未证实的风险（`WARN`），不能一律当作能力提升。

### Gate 5：Prompt Draft

Prompt 只包含正常用户会提供的目标、范围和真实业务约束。合法约束可以保留，例如“不要修改 tests”；但“先打开某文件、运行某命令、重试几次、看到某错误后停止、修改第 N 行”通常泄露了解题路径，应移除或改写为结果约束。

Prompt 不得暴露 intended solution、root cause、expected label、Verifier、Reference Solution、`must_stop`/`must_recover`、LoopGuard、`CIRCUIT_BROKEN`、评测编号或内部阈值。直接泄露其中任一项是 `BLOCK`。不要用“禁止步骤”机械审查；逐条判断它是用户约束还是 Solution Hint。若 prompt 需要讲出答案才能 solvable，回到 Fixture/Oracle 收集足够的真实证据。

### Gate 6：Workspace Fixture 与 Agent-visible Observation

从 baseline 设计真实、最小但非玩具化的因果链：问题在初始行为中客观存在，Agent 可以通过正常调查、工具调用、编译/测试、服务探测或文件检查发现证据，并至少存在一条合法解法（对 permanent blocker 则存在合理的停止与报告路径）。不要让“最终写一个哨兵文件”成为成功条件。

逐项审查 Agent-visible surface：prompt、baseline 源码和注释、README/docs、tests、fixture、文件名/目录名、变量名、错误消息、shell 输出、环境变量、HTTP response、Runtime 注入信息和可能的 Benchmark/EVAL 痕迹。特别检查：

- 注释、README、测试断言是否直接写出 root cause 或正确值；
- 文件名、变量名、错误文案是否像 `fix_timeout_here`；
- `EVAL_FIXTURE_URL`/token 等当前 Runner 注入信息是否被误用成答案；
- Agent 运行期间不能看到 verifier、reference solution 或 `EVAL_TRACE_PATH`；
- workspace 改动、普通 stderr、`echo READY` 或成功 exit code 是否会被错误当成业务恢复。

如果直接泄露答案、label 或 grader 逻辑，`BLOCK` 并优先中和注释、名称、错误和输出；如果只是不可避免但不提供答案的 evaluator 痕迹，标记 `WARN`，并确认它不会改变 Agent 的策略。不要为了“保密”删除所有线索。Leakage 和 Solvability 必须成对重跑。

### Gate 7：Solvability 与 Generalization

以一个不知道标准答案的正常工程师视角重走任务：只通过 Agent 可见信息，能否合理推出目标行为、失败原因和合法恢复路径？若只能猜数字、猜标签或猜 author 意图，这是 `BLOCK`；增加证据或缩窄 Claim。

随后做 Generalization audit：先写 Capability → Benchmark → Runtime evaluation 的推理，再反查当前 Runtime 只用于确认可观测接口和现有 harness 约束。不得按当前实现的 regex、类名、错误字符串、动作阈值或 Python 偏好定制题目。换成 Java/Node/Shell 后，若 Claim 仍成立，应能自然地产生合理变体；若当前只能合理构造一个生态，标记 `WARN` 并缩窄泛化结论；若语言无关（如纯文件 IO），不要为了覆盖率制造低价值复制品。

### Gate 8：隐藏 Verifier、Reference Solution 与 Negative Controls

`verify.py` 必须在 Agent 结束后由 Runner 复制到 Shadow Workspace 执行；它验证行为、输出、状态、业务 invariant、合法 artifact、真实停止/恢复和未伪造结果，而不是固定 patch、固定行号、固定函数调用或唯一代码结构。只有当“过程本身”就是 Capability Claim 时，才对工具顺序或 Trace 事实做约束，并写明为何不能用等价行为替代。

Verifier 应尽可能支持多个合法 solution shape。以下是分级要求：

1. untouched baseline 通过是 `BLOCK`；它必须失败；
2. reference solution 无法通过是真正的 `BLOCK`；`anti_loop` 的 `must_recover` 会被当前 Runner 自检；
3. 只写 sentinel、hardcode 输出、删除/放宽 tests、伪造依赖/READY/artifact、空 Trace 或绕过真实业务的方案通过是 `BLOCK`；
4. 有明显不同但都合法的实现时，如果 alternate shape 失败且原因是 verifier 绑定 reference 结构，是 `BLOCK`；如果不存在可信的 alternate shape，标记 `WARN`，但仍需证明 verifier 检查的是行为而非 patch。

Reference Solution 只证明“存在一个合法解”，不是唯一实现规范。Verifier 不得绑定旧的 terminal reason 字符串或某个 Guard 类名。`execution_success`、`observed_failure`、`semantic_status`、结构化 HTTP/health/test evidence 要与展示文本区分；不要用 `echo READY`、普通 stderr、workspace diff 或单一关键词证明恢复。

### Gate 9：生成、确定性验证与回退

只有不存在未解决的 `BLOCK` 且用户明确要求生成时，才扫描当前最大任务序号并按仓库约定创建新的 `task_<三位序号>_<简短名称>`。允许带已记录的 `WARN` 生成，但要在 `config.json`/报告能表达的范围内缩窄用途；不能把 warning Case 当作已证明的 HOLDOUT 泛化结论。保持 `case_id`、目录名和归档 trace 名一致；写入 `config.json`、干净 `baseline/`、隐藏 `verify.py`、必要的 `reference_solution/` 和当前 Runner 支持的 `evaluation` metadata。

生成后按顺序执行：

1. 检查 `baseline/` 没有 `__pycache__`、node_modules、临时日志、运行产物、verifier 或 reference 痕迹；
2. 先对 baseline、reference 和 negative controls 做静态/本地 deterministic 验证；
3. 运行 `py eval_runner.py --validate-only`（可用 `--task`、`--suite`、`--split` 缩小范围），确认没有破坏现有 task contract；
4. 检查新 Case 的 prompt、fixture、verifier、reference 和 hashes；
5. 不运行真实 LLM 评测，除非用户另行明确要求并接受 provider 成本。

若 verifier 过松，属于 `BLOCK`：修改 verifier/fixture 后回到 Gate 6 和 Gate 8；若 reference 不通过，修 reference 或任务设计，不能让 verifier 放宽；若需要修改 Runner/controller 才能表达新能力，停止在生成前说明所需的最小越界变更并请求方向。动态评测尚未运行、生态覆盖暂时有限或存在已隔离的非答案 evaluator 痕迹，通常是 `WARN`/`NOT RUN`，不能误写成 `BLOCK`，也不能用“先生成，之后再看”替代必要的确定性 Gate。

### Gate 10：Final Validity Report

完成 Family 后只输出能支持决策的简洁报告，至少说明：Capability Claim；新增 Case 与 ecosystem；DEV/HOLDOUT 分配；`must_stop`/`must_recover` 或其他行为预期；为何能测试目标；Prompt/Workspace/evaluator metadata 是否泄露；evidence 是否足够；implementation coupling；language/ecosystem bias；Verifier 是否绑定固定实现；reference 是否通过；untouched baseline 和 negative controls 是否失败；剩余 validity risk。

每一项写 `PASS`、`WARN`、`BLOCK` 或 `NOT RUN` 及一句证据；无法执行必要检查时用 `BLOCKED`。只有没有未解决的 `BLOCK` 且 Claim 边界与 warnings 已明确时，才能说“在限定范围内可用于指导 Runtime 优化”；动态 LLM 结果未运行时，明确写“静态有效性已审计，动态泛化尚未证明”。

## 声明前的元审查

在输出“可用于指导 Runtime 优化”前，做一次短的反事实复核：假设 Runtime 换一种实现、把 Case 投影到 JVM/Node/generic shell、把当前 HOLDOUT 结果从历史中删除，以上 Capability、evidence、Verifier 和 DEV/HOLDOUT 结论是否仍然成立。若任一答案显示 Claim 本身失真，回到 Gate 1、Gate 4、Gate 6 或 Gate 8 并产生 `BLOCK`；若只是暂时缺少自然的生态变体或尚未执行动态评测，保留 `WARN`/`NOT RUN`，缩窄结论，不能把静态通过升级成泛化结论。
