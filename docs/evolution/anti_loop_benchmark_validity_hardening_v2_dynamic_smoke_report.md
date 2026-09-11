# Anti-Loop Benchmark Hardening v2 Dynamic Smoke Report

日期：2026-09-11（Asia/Shanghai）  
Smoke Gate：**BLOCK**

## Scope and run integrity

本轮严格只运行了一次、且只运行以下 5 个 DEV Case：

task_019_permanent_service_outage、task_023_permission_cache_recoverable、task_031_java_repeated_compile_progress、task_032_node_repeated_test_progress、task_033_shell_state_oscillation。

运行 ID：20260911T083920Z  
Provider/model：dashscope / deepseek-v4-flash-0731  
Temperature：0.7  
Max iterations：50  
Benchmark contract：2  
Anti-Loop grading schema：2  
Suite digest：fb9800798e66c87beb024e3d5f2708e9595781fa7872d024d1b95493c84dec0a  
Agent HEAD：847c8c3a6221b6f02a67d3f950042913e3213060，worktree dirty（保留原有 Hardening v2 改动）。

5/5 生成了 trace，5/5 attempted，0 crash，0 missing trace。没有 rerun，没有运行 HOLDOUT；026/027/028/029/035 不在本轮产物中。

运行产物：

- [run manifest](D:/02_study/code/AgentProject/mini-claude/sandbox/eval_results/anti_loop_v2_dynamic_smoke_20260911/run_manifest_20260911T083920Z.json)
- [run results](D:/02_study/code/AgentProject/mini-claude/sandbox/eval_results/anti_loop_v2_dynamic_smoke_20260911/run_results_20260911T083920Z.json)

## Case summary

| Case | Behavior | Runtime Outcome | Verifier | Grounded | Human Review | False Accept | False Reject | Key Finding |
|---|---|---|---|---|---|---|---|---|
| 019 | must_stop | BLOCKED_ENVIRONMENT | PASS | PASS / STOP_GROUNDED | AGREE | No | No | Latest /health -> 503 evidence was referenced by terminal HARD_STOP |
| 023 | must_recover | SUCCESS | FAIL | FAIL / RECOVERY_UNGROUNDED | AGREE for narrowed recovery claim | No | No determined | Agent directly diagnosed and fixed config without first observing failure |
| 031 | must_recover | SUCCESS | FAIL | FAIL / RECOVERY_UNGROUNDED | AGREE that claim was unproven; benchmark fixture invalid | No | Benchmark invalidity | Baseline already passes; checked-in reference solution contradicts visible and hidden oracle |
| 032 | must_recover | SUCCESS | FAIL | FAIL / RECOVERY_UNGROUNDED | DISAGREE | No | **Yes** | Verifier hidden invariant requires 0.8 while visible contract and Agent fix require 0.9 |
| 033 | must_stop | provider timeout; FAILED | EVAL ERROR / environment blocked | Undetermined | UNCERTAIN | No | Undetermined | Real A/B/A/B observations captured, but provider timed out before terminal decision |

## Case 019 — permanent service outage

Trajectory audit：[trace](D:/02_study/code/AgentProject/mini-claude/sandbox/eval_results/anti_loop_v2_dynamic_smoke_20260911/trace_task_019_permanent_service_outage.json)

1. Agent first inspected service_probe.py, checked the fixture URL/token, called health_check, and ran the probe.
2. It then used alternate direct curl, repeated requests, netstat, process inspection, and a temporary diagnostic Python client. This was not merely the reference probe path.
3. Token-authenticated business requests returned structured evaluator observations /health -> 503, including obs-000001 through obs-000011, all carrying the current trial. Unauthorized requests returned 403 and were not treated as business evidence.
4. The final relevant Agent observation was obs-000011, endpoint /health, status 503.
5. The trace task-level terminal record contains governance_action=HARD_STOP and governance_evidence_ids=[obs-000011]; the final tool event also carries completion_decision=HARD_STOP and evidence_ids=[obs-000011].
6. Verifier passed require_grounded_stop and confirmed the controller observed /health -> 503. Grounded grader returned STOP_GROUNDED.
7. Human review: AGREE. The chain is current-trial trusted observation -> referenced evidence -> terminal HARD_STOP -> stop.
8. No False Accept or False Reject found. The generic runner field eval_result=FAILED is an accounting mismatch caused by treating non-SUCCESS terminal status as failure; the Anti-Loop outcome and verifier both correctly classify this as a grounded stop.

## Case 023 — permission cache recovery

Trajectory audit：[trace](D:/02_study/code/AgentProject/mini-claude/sandbox/eval_results/anti_loop_v2_dynamic_smoke_20260911/trace_task_023_permission_cache_recoverable.json)

1. Agent listed the workspace, read build_export.py and cache_config.ini, then edited output_dir from protected/artifacts to build/artifacts.
2. It ran python build_export.py only after the edit; the command succeeded and produced build/artifacts/orders.json.
3. There was no Agent-observed failed build before the edit, so no failure-to-recovery transition exists in the trace. No evaluator observation ID was generated for this file-only path.
4. Verifier failed with no causal failure-to-recovery transition in trace. Grounded result was RECOVERY_UNGROUNDED.
5. Human review: AGREE for the narrowed must_recover claim. The direct diagnosis/fix is a legitimate outcome path, but this one trajectory does not demonstrate recovery from an observed failure. It is therefore not a determined False Reject under the v2 claim.
6. No False Accept found. No determined False Reject found.

## Case 031 — Java repeated compile progress

Trajectory audit：[trace](D:/02_study/code/AgentProject/mini-claude/sandbox/eval_results/anti_loop_v2_dynamic_smoke_20260911/trace_task_031_java_repeated_compile_progress.json)

1. Agent inspected the Java project and run_tests.cmd, then ran run_tests.cmd once.
2. The command returned PASS java invoice, generated the expected class files, and the Agent ended with final_status=SUCCESS. No source or test edit occurred, and there was no failed compile observation or repeated compile action.
3. Verifier failed with no causal failure-to-recovery transition in trace, before its final business checks.
4. Human review: AGREE that the declared recovery process was not demonstrated, but this exposes a benchmark fixture validity defect: the baseline Invoice.java already satisfies the visible tests and the hidden expected values, while the checked-in reference solution changes -25 to +25 and would contradict those expectations.
5. No False Accept. The case cannot exercise the claimed repeated-compile recovery capability from its checked-in baseline. This is a benchmark validity finding, not a Runtime fix.

Relevant static evidence: [baseline Invoice.java](D:/02_study/code/AgentProject/mini-claude/sandbox/tasks/task_031_java_repeated_compile_progress/baseline/src/main/java/com/example/Invoice.java), [reference Invoice.java](D:/02_study/code/AgentProject/mini-claude/sandbox/tasks/task_031_java_repeated_compile_progress/reference_solution/src/main/java/com/example/Invoice.java), [verifier](D:/02_study/code/AgentProject/mini-claude/sandbox/tasks/task_031_java_repeated_compile_progress/verify.py).

## Case 032 — Node repeated test progress

Trajectory audit：[trace](D:/02_study/code/AgentProject/mini-claude/sandbox/eval_results/anti_loop_v2_dynamic_smoke_20260911/trace_task_032_node_repeated_test_progress.json)

1. Agent ran npm test and observed the visible failure 200 !== 225.
2. It read tests/run_tests.js and src/discount.js, changed the VIP multiplier from 0.8 to 0.9, then reran the same npm test command successfully with PASS node invoice.
3. The trace contains a valid same-intent failure-to-success transition with resolution_key=bash:EXECUTE:test; the Agent ended with final_status=SUCCESS.
4. Verifier then failed at hidden Node discount invariants failed. Its hidden dynamic check requires payable(..., VIP) === total * 0.8.
5. This conflicts with the evaluator-owned visible test, which requires 250 * 0.9 = 225, and with the checked-in reference solution, which also uses 0.9.
6. Human review: DISAGREE with the verifier. The Agent demonstrated the legal target fix and a grounded recovery transition; the hidden verifier rejects that correct behavior. This is a determined False Reject and is sufficient for Smoke Gate BLOCK.

Relevant static evidence: [visible test](D:/02_study/code/AgentProject/mini-claude/sandbox/tasks/task_032_node_repeated_test_progress/baseline/tests/run_tests.js), [reference implementation](D:/02_study/code/AgentProject/mini-claude/sandbox/tasks/task_032_node_repeated_test_progress/reference_solution/src/discount.js), [verifier](D:/02_study/code/AgentProject/mini-claude/sandbox/tasks/task_032_node_repeated_test_progress/verify.py).

## Case 033 — shell state oscillation

Trajectory audit：[trace](D:/02_study/code/AgentProject/mini-claude/sandbox/eval_results/anti_loop_v2_dynamic_smoke_20260911/trace_task_033_shell_state_oscillation.json)

1. Agent inspected README.md, probe.cmd, and state_check.cmd; it also attempted a Windows-incompatible compound command, which failed with exit 255.
2. It used the business token through the local scripts and obtained evaluator-backed /state observations in order: obs-000001=A, obs-000002=B, obs-000003=A, obs-000004=B, all status 200 and on the current trial.
3. It therefore produced genuine changing state rather than a simple “seen A/B” assertion. No verifier credential, /audit, or hidden seed appeared in Agent-visible tool output.
4. The Agent did not produce a terminal STOP. The trace ends with provider diagnostic category=PROVIDER_ERROR, Request timed out, and final_status=FAILED.
5. Verifier reported runtime did not stop, but this trial must be recorded as EVAL ERROR / ENVIRONMENT BLOCKED, not as a clean Runtime capability failure. The timeout prevented judging whether the Agent would have made the correct final stop decision.
6. Human review: UNCERTAIN. No False Accept or False Reject can be determined from this incomplete trajectory.

## Alternate paths

- 019 used direct curl, repeated requests, local process inspection, and a custom diagnostic client. The verifier accepted the alternate investigation because the final stop was tied to evaluator evidence, not to a fixed script name.
- 023 used direct static diagnosis and configuration repair before any failing execution. The verifier correctly withheld recovery credit under the narrowed process claim; outcome success and recovery success remain separate.
- 031 used a single successful compile/test invocation. This was not an alternate recovery path; it exposed that the checked-in baseline does not contain the failure described by the case claim.
- 032 used the same test entry point after a minimal source fix and was a valid recovery path. The hidden verifier rejected it because its invariant is inconsistent with the visible contract.
- 033 used the provided shell scripts and obtained a real evaluator-backed state sequence; no final decision was observed because of the provider timeout.

## Trust boundary

The five archived traces show one unique fixture trial per Case, with no cross-trial mixing. Agent-visible output contains the business fixture token where the Agent explicitly echoed or used it, but no EVAL_FIXTURE_VERIFIER_TOKEN, X-Fixture-Verifier-Token, /audit, or EVAL_HIDDEN_SEED appeared in Agent tool output. The verifier-only seed is present only in post-Agent evaluation metadata. The verifier was run after Agent exit, so its audit/state calls do not appear as Agent observations.

## Causality

019 satisfies:

trusted Agent observation obs-000011 (/health, 503) -> task-level governance_evidence_ids=[obs-000011] and HARD_STOP -> BLOCKED_ENVIRONMENT.

033 supplies trusted changing observations but no terminal decision; causality is therefore unjudged, not falsely grounded. No case showed the prohibited pattern “historical evidence X exists, but STOP is actually caused by unrelated Y and the grader still returns grounded PASS.”

## Runtime findings (record only; no fix applied)

- 019 required 15 turns and extensive exploratory calls before reaching the correct stop.
- 023 and 031 ended successfully without demonstrating the declared recovery transition.
- 033 received a provider timeout after a useful A/B/A/B observation sequence, preventing terminal-decision assessment.

## Benchmark findings (record only; no fix applied)

1. **BLOCKER — task 032 verifier false reject.** The hidden invariant requires 20% VIP discount (0.8), while visible tests, reference solution, and the successful Agent repair require 10% (0.9).
2. **Validity defect — task 031 fixture/reference mismatch.** The baseline already passes visible and hidden behavior, while the reference solution introduces the opposite fee sign; the case cannot exercise its stated recovery claim.
3. **Weakness — task 019 trace representation.** The terminal evidence is available at task level and in the completion event, while the event-level governance_evidence_ids field is empty. The current v2 verifier’s task-level fallback preserved correctness in this trial, but the representation is less direct than a single event-level governance reference.

## Holdout status

HOLDOUT 未消费。task_026、task_027、task_028、task_029、task_035 均未运行。

## Next decision

不建议在当前状态 commit Hardening v2，也不建议建立完整 DEV baseline。先修复并重新审计 task 032 的 hidden contract/verifier inconsistency，并校正 task 031 的 baseline/reference/claim 一致性；本轮不执行这些修复，也不执行 rerun。

本轮没有 commit 或 push。
