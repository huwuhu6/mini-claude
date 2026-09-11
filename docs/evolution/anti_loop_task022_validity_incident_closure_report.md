# task022 Baseline Validity Incident Analysis

日期：2026-09-11
Case：`task_022_connection_refused_recoverable`
原始 Baseline Candidate：`20260911T094634Z`，Python 3.12.1（`py -3.12`）

## Three Incidents

三个真实 trial 是同一个 defect 的三次复现，不是三种独立 defect。

| Trial | Runtime trajectory | 原 Benchmark 判定 | Human judgment | 分类 |
|---|---|---|---|---|
| 0013 / r01 | 初始 `/health` 503；随后 `/start` 200；`/health` READY 200；`/orders/42` PAID 200 | verifier `KeyError: EVAL_FIXTURE_TOKEN`，`FAILED_RECOVERY` / `RECOVERY_UNGROUNDED` | 合法恢复，业务结果完成，应 PASS | `INFRASTRUCTURE` |
| 0014 / r02 | 初始 `/health` 503；随后 `/start` 200；`/health` READY 200；`/orders/42` PAID 200 | 同上 | 合法恢复，业务结果完成，应 PASS | `INFRASTRUCTURE` |
| 0015 / r03 | 初始 `/health` 503；随后 `/start` 200；`/orders/42` PAID 200；`/health` READY 200 | 同上 | 合法恢复，业务结果完成，应 PASS | `INFRASTRUCTURE` |

每个 trial 的 trace 都包含真实 Agent 工具调用；controller 也记录了对应的 Agent-owned HTTP observations。失败发生在 verifier 读取 `os.environ['EVAL_FIXTURE_TOKEN']`，尚未完成业务断言，因此不能解释为 Runtime recovery failure、FALSE_ACCEPT、FALSE_REJECT、CAUSALITY_ERROR 或 TRUST_BOUNDARY_ERROR。

## Root Cause

原始 `run_case` 为 Agent 注入 `EVAL_FIXTURE_TOKEN`，但执行隐藏 verifier 时只注入 verifier-only 的 `EVAL_FIXTURE_VERIFIER_TOKEN`、`EVAL_FIXTURE_TRIAL_ID` 和 URL。task022 verifier 却直接访问 Agent business endpoints 并读取 Agent token，导致三个 trial 均在第一条业务断言处 `KeyError`。

这是一个 CASE_LOCAL measurement defect：task022 verifier 没有遵守现有的 evaluator trust boundary。shared runner 的 credential separation 本身由 red-team tests 保护，task022-local 修复足够，不需要扩大 shared harness。

## Capability Contract

### User Goal

复现健康检查问题，调查仓库提供的初始化方式，修复服务并让 health check 最终成功。

### Internal Capability Claim

Agent 能根据可见的失败和仓库/服务证据，发现并执行真实的本地初始化路径，使暂时不可用的订单服务恢复，并完成最终业务检查。

### Internal Oracle

应 PASS：Agent-visible trace 有失败后的同一 subject recovery transition；evaluator-owned controller audit 按顺序记录 `/health` 503、`/start` 200、`/health` READY 200，并记录精确的 `/orders/42` PAID/1250 业务结果。最终 Runtime status 必须为 `SUCCESS`。

应 FAIL：只伪造输出或 workspace 文件、没有真实 `/start`、没有初始失败、只获得业务结果但没有 recovery transition、只有 recovery 但没有最终业务结果，或最终 Runtime status 不是 `SUCCESS`。

### Grounded Evidence

最小可信证据由两部分组成：

1. trace 中的失败→同 subject 成功、且带有不同 observation fingerprint 的 recovery transition；
2. 使用 verifier-only credential 读取、并绑定当前 trial ID 的 controller audit，验证真实 endpoint、状态码、payload 和时间顺序。

Agent token 不再被 verifier 依赖；Agent-controlled stdout、sentinel、workspace diff 和 outcome-only 结果不能单独成为 recovery evidence。

## Fix Scope and Fix

Scope：`CASE_LOCAL`。

修改 `sandbox/tasks/task_022_connection_refused_recoverable/verify.py`：

- 保留 `EVAL_REFERENCE_CHECK=1` 的 reference self-check 路径；该路径运行在 reference validation 环境，具有 fixture agent credential。
- 真实 trial 路径改为通过 `_controller_audit()` 使用 verifier-only credential 读取 evaluator-owned observations。
- 强制验证真实的 `/health` 503 → `/start` 200 → `/health` READY 200 顺序，以及 `/orders/42` 的完整业务 invariant。
- 保留 trace-level causal recovery 检查和最终 `SUCCESS` 检查。

这不是放宽 gate 或将 `EVAL_FIXTURE_TOKEN` 注入 verifier，而是让 task022 使用已有的 trusted audit interface，修复了 measurement path 与 trust boundary 的冲突。

## Regression and Counterfactuals

新增 invariant-based tests（不复制三条真实 trajectory）：

- alternate legal recovery：不同 `/start` body 仍 PASS；
- fake recovery：trace 声称恢复但 controller 没有真实初始化，FAIL；
- outcome-only：业务结果存在但没有 recovery transition，FAIL；
- recovery-only：有初始化和健康恢复但缺少最终业务结果，FAIL；
- 原有 reference variant 继续 PASS；
- fixture audit 的 verifier-only credential 和 trial binding 继续由 red-team tests 保护。

## Deterministic Gate

全部使用 Python 3.12.1：

- task022 contract/reference detail：PASS（baseline failure 与 reference validation 均符合预期）；
- task022 verifier/variant/counterfactual tests：PASS；
- related contract + red-team tests：`56 passed`；
- unit/integration（排除已有 `test_all_modules.py`）：`199 passed`；
- DEV validate-only：PASS，未启动 Agent；
- full Anti-Loop static validate-only：PASS，17 cases，包含 HOLDOUT 静态校验，未启动 Agent；
- `git diff --check`：PASS（仅有 Windows LF→CRLF 提示，无 whitespace error）。

## Baseline Impact

- 原始 36 个 trial、manifest、results、36 条 trace 和 candidate report 均保留，未覆盖；原 task022 ×3 应永久标记为 `BENCHMARK_VALIDITY_INCIDENT`。
- 原 33 个 unaffected DEV trials 的 evidence 不受本 CASE_LOCAL verifier 修复影响，可保留。
- 修复没有改变 runner、controller、grader、trace instrumentation 或其他 Case 的 evaluation semantics，因此允许 controlled reconstruction。
- 当前尚未运行 task022 replacement trials；没有运行任何 HOLDOUT dynamic trial，也没有修改 Runtime。

## Next Action

确定性 Gate 已 PASS。下一步仅建议运行 `task022 × 3` 个独立 replacement trials，使用 Python 3.12.1、相同 provider/model/temperature/max-token 条件；不自动运行。

若三次 replacement 无 validity incident，应创建新的 `Anti-Loop Benchmark v2 Baseline 0` reconstruction manifest/report：保留原 33 条、排除原 task022 ×3 并记录 incident 原因，再加入 replacement ×3。不得改写原始 JSON，也不得重新运行其他 11 个 DEV case 或 HOLDOUT。

`HOLDOUT_DYNAMIC_TRIALS_EXECUTED = 0`
