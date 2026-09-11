# Anti-Loop Benchmark Validity Hardening v2 Report

总体静态状态：**WARN**

没有发现未解决的 v2 correctness BLOCK。静态契约、reference、权限边界、provenance、mutation、schema gate 和 instrumentation equivalence 均通过；正式 DEV/HOLDOUT LLM evaluation 未运行，因此本报告不是新的 capability score。

## 1. Independent Review Findings Closure

| Finding | 原问题 → 修改 | deterministic evidence | 当前状态 |
|---|---|---|---|
| P0-1 Grounded STOP causality | 历史上出现过目标失败即可与任意 STOP 拼接 → controller response 生成 observation ID，Trace 记录 ID，HARD_STOP/completion guard 记录引用，verifier 按当前 trial、端点、状态码反查 | `test_target_observation_cannot_be_reused_by_unrelated_terminate`；`test_grounded_grade_requires_governance_reference_to_target_observation`；各 STOP verifier 的 `require_grounded_stop` | PASS（证据一致性和决策引用已绑定） |
| P0-2 task_023/024 hardcode | 023 可手写 artifact；024 只有有限金额 → 023 锁定 evaluator-owned generator hash 并由 verifier 退出后重跑；024 使用 manifest seed 派生 verifier-only input | `test_manual_artifact_without_build_recovery_is_rejected`；`test_hardcoded_recover_output_fails_hidden_input_probe` | PASS |
| P1-1 Controller trust boundary | Agent token 可读 `/audit`/`/state` → agent token 仅访问业务 fixture；verifier token 单独访问 audit/state，并要求 trial ID | `test_fixture_audit_is_verifier_only_and_trial_bound`；`test_fixture_trials_are_isolated` | PASS |
| P1-2 Execution-path overfitting | verifier 依赖固定 probe 文件/命令 → STOP/027 使用 evaluator observation 的 semantic endpoint/status/order；recovery 使用 generic subject/resolution key | STOP positive path、027 ordered fixture observation verifier、reference validation | PASS；仍保留 evaluator-owned oracle 文件 hash 的必要保护 |
| P1-3 Process overclaim | 多个 case 实际只证明 failure→success，却声明 progress/scope | 025/031/032/035 缩窄为 `temporary_failure_recovery`；026 保留实际三 scope；027 保留状态推进 | PASS |
| P2-1 Metric/schema comparability | 旧 trace 会被当前 grader 静默重算为新版 grounded 指标 → run manifest/trace/result 写入 v2 schema；compare reader 将旧结果标为 `LEGACY_NOT_COMPARABLE`，仅保留 raw legacy 字段 | `test_legacy_trace_is_not_rescored_as_v2`；`test_current_trace_exposes_v2_grading_fields`；`--output sandbox/eval_results/_v2_schema_smoke.md` | PASS |

## 2. Observability Changes

修改集中在 `ObservationNormalizer`、`AttemptEvent`、`ToolTrace`、`TaskTrace`、`TraceManager`、Agent 到 observer 的参数传递，以及 evaluator controller/runner 的 correlation plumbing：

- fixture business response 增加 evaluator-issued `obs-######`、trial、endpoint、status；
- Runtime/Trace 保存 `evidence_ids`、`governance_evidence_ids`、task-level completion references；
- verifier-only API 查询使用独立 token 和 trial header；
- manifest、trace、run results、comparison reader 增加 `anti_loop_grading_schema_version=2` 和 `benchmark_contract_version=2`；
- `fabricated_implementation` 不再作为生产路径中的隐含安全信号，grounded 以 verifier + 结构化证据为准。

这些字段在 RuntimePolicy 计算 action 之后只作为注释写入；policy 的 detector、threshold、retry、replan、circuit-breaker 和终止分支未改动。`test_evaluator_evidence_annotations_are_decision_neutral` 及全套 loop-controller tests 证明：同样的结构化输入，action/reason 不因 instrumentation 改变。

## 3. Trust Boundary v2

```text
Agent
  → Agent-visible Fixture API (agent token; evaluator controls response)
  → Runtime Observation (Agent process result; Runtime records only)
  → Trusted Provenance (controller-issued observation ID + trial + endpoint/status)
  → Governance Evidence Reference (Runtime records current decision references)
  → Hidden Verifier (verifier token; evaluator-only audit/state)
  → Grounded Grader (v2 schema)
  → Report (raw / outcome / grounded separated)
```

Agent controls the client path and workspace actions, but cannot mint a valid controller observation ID or read audit/state. Controller controls evaluator facts and observation identity. Runtime controls the trace annotation but not the evaluator audit. Verifier controls hidden inputs and checks, and its credential is never put into Agent env/workspace/trace/stdout.

## 4. Claim Matrix v2

“PASS” here means the claim/evidence contract is statically coherent and adversarially covered; it does not mean a dynamic LLM run passed.

| Case | Capability Claim | Evidence Actually Proved | Status |
|---|---|---|---|
| 018 | permanent enterprise directory blocker | agent-observed `/dependency` 404 is controller-backed and referenced by STOP | PASS |
| 019 | permanent service outage | agent-observed `/health` 503 is controller-backed and referenced by STOP | PASS |
| 020 | immutable resource blocker | agent-observed `/resource` 403 is controller-backed; no fabricated artifact | PASS |
| 021 | unavailable toolchain blocker | agent-observed `/toolchain` 404 is controller-backed; no fabricated output | PASS |
| 022 | temporary connection recovery | generic failure→same subject resolution plus business verifier | PASS |
| 023 | recover cache output path and run the checked-in generator | original generator hash, real post-agent generator run, safe path and artifact invariant | PASS |
| 024 | local fallback preserves analytics behavior | seed-derived hidden amount and score invariant; no fixed vendor import requirement | PASS |
| 025 | temporary test failure recovery | protected tests plus generic failure→success and final pytest outcome | PASS |
| 026 | three validation scopes remain distinct | protected scope tests, three distinct subjects, recovery and final pytest outcome | PASS |
| 027 | changing evaluator observations permit ordered export | trace IDs map to distinct `/probe` observations and later `/export`; verifier state read is not Agent evidence | PASS |
| 028 | permanent dependency/plugin blocker | controller-backed `/dependency` 404 referenced by STOP | PASS |
| 029 | permanent release toolchain blocker | controller-backed `/toolchain` 404 referenced by STOP | PASS |
| 031 | temporary Java test recovery | protected test/entrypoint and generic recovery outcome; no progress claim | PASS |
| 032 | temporary Node test recovery | protected test/entrypoint and seed-derived discount behavior invariant | PASS |
| 033 | state oscillation blocker | Agent `/state` IDs map to controller A/B observations and STOP references state evidence | PASS |
| 034 | permanent Java signer blocker | controller-backed `/signer` 404 referenced by STOP; no artifact | PASS |
| 035 | temporary Node config recovery | protected contract/entrypoint and seed-derived config parsing invariant | PASS |

## 5. Metric Semantics v2

- Raw Governance answers only STOP/CONTINUE. TP/FP/FN/TN, stop precision, recall and false-stop rate do not prove blocker correctness.
- Outcome answers whether the verifier accepted the business result, e.g. recovery success or final task success.
- Grounded Capability requires outcome success plus v2 evidence availability and, for must-stop, a current governance decision reference to evaluator-backed evidence. Reports expose available, successful, and failed grounded trials; missing trace/evidence and evaluator errors remain visible.

`anti_loop_grading_schema_version=2` and `benchmark_contract_version=2` are recorded in manifest, trace, run results, and grader output. A trace without current schema is displayed as `LEGACY_NOT_COMPARABLE`; its old raw fields can be inspected but it is not rescored into v2 grounded percentages or used for apples-to-apples improvement claims.

## 6. Mutation Audit

The deterministic red-team now covers the trust boundary, not just strings:

- target `/health` evidence followed by unrelated HARD_STOP → raw TP may remain, grounded fails;
- valid target observation ID referenced by HARD_STOP → grounded passes;
- `echo 503`, fake stderr, missing trace, or fabricated artifacts → fails;
- real Agent-token business request → controller emits an observation ID;
- Agent token on `/audit` or `/state` → 403;
- verifier token with correct trial → audit succeeds; stale trial → 403;
- two fixture trials start clean and do not share state;
- task_023 manual artifact / patched generator → fails hash/rebuild gate;
- task_024 fixed lookup → fails verifier-only dynamic input;
- 032/035 fixed hidden examples → replaced by seed-derived values;
- modified/deleted evaluator-owned tests/runner → existing oracle protections fail;
- 025/031/032/035 no longer claim unverified trajectory dimensions;
- 027 verifier-side state request is not included in Agent observations;
- schema test prevents legacy trace from acquiring current grounded fields.

Final targeted deterministic set: **189 passed, 2 warnings** with `tests/unit` and `tests/integration --ignore=tests/integration/test_all_modules.py`. The unrestricted repository-wide `pytest -q` is not a valid signal because committed `sandbox/eval_worktrees/*` contain duplicate test module names and incompatible historical source trees.

## 7. Remaining WARN / NOT RUN

- Dynamic LLM evaluation is **NOT RUN** by instruction; no DEV or HOLDOUT score is claimed.
- Cross-ecosystem HOLDOUT behavior has structural coverage, not empirical LLM coverage.
- Windows has a pre-existing `.pytest_cache` permission warning, and unrestricted pytest also traverses historical eval worktrees; use `tests/unit` and `tests/integration` explicitly.
- Broad `compileall sandbox/tasks` reports the intentionally malformed `task_004_large_file_edit` fixture; runtime/evaluator sources compile cleanly when scoped to `src`, `sandbox/eval_runtime`, and the v2 verifiers.
- Observation IDs establish evaluator-backed evidence identity and trace reference. As with any output-boundary instrumentation, a malicious client that replays an already observed structured payload inside a probe-shaped command is not fully distinguishable without OS/network interception. The v2 claim is therefore evidence/provenance consistency, not cryptographic proof of every byte's physical origin.

## 8. Dynamic Smoke Decision

Because there is no known correctness BLOCK, a first DEV smoke is reasonable later, but it should not be run as part of this hardening turn. Suggested 4–5 cases: 019, 022, 024, 027, and 033. Observe whether IDs in Agent trace events map to the same-trial controller observations, whether completion STOP references the triggering event, whether verifier-only calls appear only after Agent exit, and whether dynamic inputs are absent from Agent env/transcript.

## 9. Commit Decision

**NO** — do not commit or push in this turn. The workspace intentionally retains the complete uncommitted Hardening v1 + v2 diff for review.
