# Benchmark Validity Gates Reference

主 `SKILL.md` 定义执行顺序；本文件提供每个 Gate 如何取得证据、失败后如何回退。它不是“做完勾选即可”的静态清单：每个结论都要有可复现 evidence，失败必须进入重设计循环。

## Gate card：每次只问一个可证伪问题

对每个 Gate 形成一张很短的内部 card：`Claim → evidence → decision → remediation`。`decision` 使用 `PASS / WARN / BLOCK / NOT RUN`：

- `PASS` 表示当前 evidence 足以支撑该 Gate 的 Claim；不表示整个 Benchmark 已动态验证。
- `WARN` 表示风险真实存在但边界清楚、不会直接使 Case 失真，可以继续并缩窄结论、限定 DEV 用途或记录缓解方式。
- `BLOCK` 只用于会直接破坏有效性、信息隔离、Solvability、行为判定或 harness contract 的问题，必须回退重设计。
- `NOT RUN` 表示尚未取得证据；若该证据是本 Claim 的必要条件，整体应为 `BLOCKED`，不能把它默认为 PASS。

用户指令优先于本 Skill 的默认偏好。不要因为暂时只有一个合理 ecosystem、缺少自然的跨生态 projection、动态 LLM eval 尚未运行，或存在不泄露答案的轻微 evaluator 痕迹而自动 BLOCK；这些通常是 `WARN`/`NOT RUN`。不能用用户指令绕过直接泄露 expected label/solution、不可解、reference 无法通过或 verifier 形同虚设等硬性问题。不能用“看起来合理”“reference 能跑”代替 evidence。

### 1. Capability / Oracle card

把“Runtime 该做什么”与“任务业务最终要什么”分栏。对于 Failure Intelligence，Oracle 应描述失败类别、环境 capability、合法 alternative 和 target outcome；对于 progress，Oracle 应描述真实 Observation/状态/验证结果的变化，而不是文件是否改过；对于 stop，应描述不可继续的事实和最小可审计报告。

若 Oracle 只能引用当前 Runtime 的 regex、阈值、类名或内部状态，回退到 Capability Spec。先抽象语言无关机制，再选择 ecosystem projection。

### 2. Agent-visible Leakage card

像一次未知标准答案的 Agent 一样，从这些表面逐一追踪“它能否直接读出 intended solution、root cause、expected behavior 或 grader 元数据”：

```text
prompt
baseline source/comments/README/docs
tests and fixtures
file/directory/variable names
process stdout/stderr and shell errors
environment variables and HTTP responses
Runtime-injected system/context text
workspace/runtime/evaluation artifacts
```

常见泄露包括答案型注释、`fix_*` 文件名、测试中唯一正确常量、README 的解决方案、错误信息中的 root cause、`echo READY` 被当成健康证据、`solution.txt=RECOVERED` 哨兵、verify/reference 在 Shadow Workspace 中提前出现，以及 EVAL 环境变量/日志告诉 Agent 当前 Case label。

修复路线按成本从低到高：中和命名和文案 → 移除不必要的测试/文档提示 → 把答案改成可通过真实调查获得的 evidence → 用 evaluator-side controller 保存不应在 workspace 的真实状态。最后一项不能把所有有用事实搬到 Agent 不可见处，否则会形成 unsolvable Case。

### 3. Solvability card

对一个不知道 reference 的工程师做盲走：

1. 初始 failure/目标状态能否通过正常工具调用观察到；
2. 如果是 recover Case，仓库或当前环境是否给出至少一条合法恢复路径；
3. 如果是 permanent blocker，Agent 是否能获得足够 evidence 证明“没有合法替代”；
4. 行为成功是否由真实 output/state/invariant 证明，而非写一个 author 预期字符串；
5. 相同 prompt 的对照 Case 是否只能被真实环境事实区分，而不能被 wording 猜出。

若答案是“只能猜”，这是 `BLOCK`；不要在 prompt 中补解题步骤，增加自然证据、扩充 fixture 或缩窄 Capability Claim。若 Agent 只能依赖隐藏 Trace 才知道该怎么做，Trace 只能作为过程审计，不能作为唯一 solvability evidence。

### 4. Generalization / anti-contamination card

做两次反事实阅读：

- 把当前 Runtime 换成另一个实现：Case 的 evidence 和 verifier 是否仍有意义？
- 把 ecosystem 换成 JVM、Node 或 generic shell：如果 Observation、Action、失败表达和合法 recovery 真会变化，是否能生成自然 variant？如果不会，保留语言无关的一份即可。

以下发现若已经成为判定逻辑，一律 `BLOCK` 并回退重设计，不得作为“增加一点随机性”处理：task id 分支、fixture 专属字符串、当前错误文本的单一正则、只命中当前 threshold 的次数、只接受一个命令名、依赖某个 Runtime 类名或固定 tool sequence、只因 Python 方便而选择 Python。若只是尚未证实的 implementation coupling 风险，标记 `WARN`，补充反事实或 alternate shape 后再决定；不能把合理的单生态设计本身当成失败。

### 5. Counterfactual / Family card

对于治理行为，用最小对照矩阵表达：

```text
同一任务 framing
  ├─ 事实 A：存在合法 recovery / 有真实 progress → CONTINUE/RECOVER
  └─ 事实 B：无合法 recovery / 只有表面变化 → STOP/REPLAN
```

差异必须落在 environment capability、fallback、target state、failure observation 或 state sequence，而不是 prompt 中的“请停止/请继续”。Family 的每个 Case 只改变有因果意义的一两个维度；至少保留一个 DEV 与 HOLDOUT 未共享的表面。HOLDOUT 结果被看到并用于 Runtime 修改后，重新取样，不继续使用同一批结果作泛化证据。

### 6. Verifier / mutation card

Verifier author-side 的最小实验应覆盖四类：

| 实验 | 应该发生什么 |
|---|---|
| untouched baseline | 真实业务/状态/测试失败 |
| reference solution | 通过真实 command/output/invariant |
| obvious cheat | sentinel、hardcode、删测试、假 artifact、假 health、伪造 Trace 均失败 |
| alternate legal shape | 与 reference 结构不同但共同结果相同，也通过 |

对 stop Case，可能没有 reference solution，但仍要证明：没有真实 failure evidence 的空操作失败；有合法 blocker evidence 且没有伪造结果的 stop 可以通过；把 blocker 改成假的成功结果失败。对 recover Case，`EVAL_REFERENCE_CHECK=1` 只能控制 verifier 如何检查 author-side reference，不能成为 Agent 的可见提示或 runtime 成功条件。

Verifier 只在 Claim 需要时读取 `EVAL_TRACE_PATH`。读取 Trace 时优先用结构化字段和事实关联：`execution_success` 与 `observed_failure` 可同时为真；HTTP health positive 必须来自 probe-scoped structured observation；pytest/build 的成功必须由真实再执行或明确的 artifact/invariant 证明。不要用最终回答、任意 stderr、workspace mutation、关键词 `pass/ready/healthy/compiled` 或旧 `terminal_reason` 字符串单独判定。

若 alternate shape 失败，先问 verifier 是否绑定了 reference 的实现结构；若确实绑定，这是 `BLOCK`。如果没有可信的 alternate shape 可执行，标记 `WARN`，但仍需由源码审查和行为断言证明没有固定 patch coupling。若 baseline 或 obvious cheat 通过，立即 `BLOCK` 并回到 Fixture/Verifier Gate，不能继续生成更多 Case 来掩盖。

## 失败回退路由

```text
Prompt/Workspace leakage
  → 中和可见表面 → 重新做 Leakage + Solvability

Evidence 不足或只能猜
  → 增加真实可调查 fixture / 缩窄 Claim → 重新做 Oracle + Solvability

重复 coverage 或 implementation coupling
  → 改 Family 维度或改行为 verifier → 重新做 Suite + Generalization + Mutation

Reference 不通过
  → 修 reference/fixture，不放宽 verifier → 重新做 Outcome + Mutation

需要修改 Runner/controller 才能表达能力
  → 停止生成，列出最小越界变更并请求授权
```

“先生成，之后记录风险”不是回退机制。无法在当前权限、工具链或确定性环境中证明 Gate 时，状态应为 `BLOCKED` 或 `NOT RUN`，而不是宣称 Benchmark 有效。

## Final Validity Report 的最小结构

```text
Status: PASS / WARN / BLOCK / BLOCKED / NOT RUN
Capability Claim: ...
Cases: case_id — ecosystem — DEV/HOLDOUT — expected behavior
Why discriminative: ...
Prompt leakage / Workspace leakage / Evaluator metadata leakage: PASS/WARN/BLOCK + evidence
Solvability evidence: ...
Implementation coupling: none / WARN / BLOCK + reason
Language/ecosystem bias: none / WARN / BLOCK + reason
Verifier: behavior-bound; baseline/reference/negative/alternate result
Dynamic evaluation: not run / result with manifest and split
Remaining validity risks: ...
```

动态评测报告要把治理 confusion matrix 与 Outcome 成功率分开；不能把 `must_recover` 的 TN 写成业务成功，也不能在缺失 Trace 或 Infra Error 时删掉分母。比较版本时同时检查 Agent commit、`worktree_dirty`、suite/config/baseline/verify hash、run id 和实际覆盖数。
