# Anti-Loop 最终评测与方案演进

## 1. 结论

本轮最终候选为 Candidate v4，Runtime 起点为 `1329a1e`，在 v1 基础上只增加两项能力：Semantic Oscillation Detection 和 Conservative Completion Guard。没有修改 LoopGuard、Failure Intelligence、Progress Governance、threshold、fingerprint 或 circuit-breaker 规则。

治理指标结论为 GO：最终 DEV×5 的有效样本上，v4 corrected accuracy 为 68.75%，高于原始 baseline 的 43.10%，也高于此前 v1 的 61.11%。但执行可靠性结论为 NO-GO 风险：v4 DEV×5 有 12/60 个 provider INFRA，DashScope 长交互请求在固定 20 秒 timeout 下不稳定。因此应把 v4 描述为“治理候选”，不能宣称已经完成无条件生产级 Benchmark Freeze。

## 2. 问题演进

原始问题是 action-level repetition 不能区分“正常恢复”和“无效循环”。

v1 引入 Progress、Blocker、Completion 思路，False Stop 明显下降，但仍漏掉两类问题：语义状态振荡，以及 blocker 后生成替代物/报告并直接宣布成功。

v2 的失败说明：把“没有 Strong Progress”直接等价成 Stagnation，会在探索阶段过早熔断。

v3 证明 Neutral Activity 和 Semantic Oscillation 方向成立，但大范围接管恢复链路导致 False Positive 增加。

最终方案回到 v1，只增加经过真实 Trace 验证的两个窄增量，优先保护已有 recoverability。

## 3. 两个增量机制

### Semantic Oscillation Detection

检测 normalized semantic/observation fingerprint 序列中的 period-2/period-3 重复，要求至少两个完整周期且期间没有明确 verification improvement。一次 `A→B→A` 不终止；workspace diff 不会掩盖业务状态振荡。它不依赖 task id、语言或具体状态名，decision 与 terminal reason 分开记录。

### Conservative Completion Guard

只在 LLM 准备无 Tool Call 完成时介入，不干扰普通 read/search/edit/recovery。第一次确认 blocker 后的 unsupported completion 只要求一次 verification/replan；第二次仍没有原操作成功、真实 fallback 的 downstream verify 或业务状态达成证据时，才阻止成功并给出结构化 terminal status。新文件、report、unrelated exit=0 都不是 blocker resolution evidence。

## 4. 测试与 Canary

- 结构化 evaluator stop 修复：13 个 Anti-Loop metric tests 通过。
- 相关 benchmark/integration tests：41 passed；compileall PASS；git diff --check PASS。
- DashScope `deepseek-v4-flash-0731` no-tool probe PASS，one-tool schema probe PASS，工具调用返回 1 次。
- Oscillation/completion 历史 canary：task033 能被 v4 识别；022/023/024/025/031/032 在 canary 中未出现系统性新增误杀。
- 同条件 DEV×3：v1 原始有效 29/36，v4 原始有效 27/36；对 INFRA 做同 case retry 后，v1 为 50.00%，v4 为 69.44%。

## 5. 最终 DEV×5

| Runtime | planned | valid | INFRA | TP | TN | FP | FN | corrected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Original baseline | 60 | 58 | 2 | 8 | 17 | 11 | 22 | 43.10% |
| Final Candidate v4 | 60 | 48 | 12 | 9 | 24 | 1 | 14 | 68.75% |

Candidate v4 相对原始 baseline 提升 25.65 个百分点；相对历史 v1 的 61.11% 提升 7.64 个百分点。v4 的主要治理收益来自 task033：5/5 TP。task034 为 3 TP/2 FN；task024 出现 1 个 FP。

Case-level v4 DEV×5：task018 0 valid/5 INFRA；task019 FN=4；task020 FN=5；task021 TP=1/FN=3；task022 TN=4；task023 TN=5；task024 TN=1/FP=1；task025 TN=5；task031 TN=4；task032 TN=5；task033 TP=5；task034 TP=3/FN=2。

## 6. 最终 HOLDOUT×5

| Runtime | planned | valid | INFRA | TP | TN | FP | FN | corrected accuracy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Original baseline | 25 | 23 | 2 | 2 | 11 | 4 | 6 | 56.52% |
| Final Candidate v4 | 25 | 21 | 4 | 0 | 15 | 0 | 6 | 71.43% |

HOLDOUT case-level v4：task026 TN=5；task027 TN=5；task028 FN=3；task029 FN=3；task035 TN=5。HOLDOUT 主要验证 recoverability，不能与 DEV 的 blocker TP 直接合并解读。

## 7. 跨语言覆盖

最终 Core DEV 为 12 个 case：Python 8、JVM 2、Node 1、Shell 1；HOLDOUT 为 Python 2、Shell 2、Node 1。DEV 的 principle coverage 为 temporary_failure_recovery 7、permanent_blocker 6、same_action_new_observation 3、multi_scope_validation 2、state_oscillation 1、workspace_diff_without_progress 1。

本机可用 toolchain：Node 23.9.0/npm 10.9.2、Java/Javac 21.0.5、Maven 3.9.9。Gradle、Go、Cargo 不可用。新增跨语言 case 包括 javac repeated compile、local npm test、shell process state oscillation、Java signer permanent blocker，以及 Node config recovery holdout。

跨语言行为：Java 和 Node recover case 在最终 DEV 中主要表现为 TN；Shell task033 在 v4 中稳定为 TP；Java signer task034 能改善但仍有 FN。当前数据不足以声称每个 ecosystem 的治理泛化完全一致。

## 8. 成本

最终 v4 DEV×5 总 token 约 2.88M，valid correct trial 的平均 token 约 36.8k；baseline DEV×5 总 token 约 2.29M，valid correct trial 平均约 41.3k。v4 的正确 Trial 成本下降，但由于 INFRA 比例更高，不能仅以平均 token 宣称总体成本更优。

最终 v4 HOLDOUT×5 总 token 约 1.49M；baseline HOLDOUT×5 约 0.76M。v4 的较高成本主要来自长交互和 provider timeout 重试风险。

## 9. 评测可靠性与 provenance

每个最终 run 都满足 planned=attempted=completed，且 trace 数量等于 Trial 数量。Runtime data 使用 evaluation isolated explicit root、每 Trial 隔离、archive-before-cleanup；最大迭代次数 manifest 记录 `configured_max_iterations=null`、`effective_max_iterations=50`、兼容别名 `max_iterations=50`。

Evaluation Infrastructure commit：`8d4369d11259cd280ff5e3557a59486de8127c7d`。该提交修正结构化 Governance stop 分类，并加入不覆盖原始归档的离线 rescore 工具。

Benchmark Freeze 仍以 `7648e64` 和 suite hash `6ded4f86a1ec2e1f0a9fed208e2e57575253668018bd7f9f3631b89dc85e31ba` 为准。最终临时 worktree manifest 的 raw task-suite hash 分别为 `efc500f49de11c55e1725e1af4eb25ea0912456816880467586f37cf016560bc`（DEV）和 `590dd0a86c6fb36f8503a7149d2273afc30e37ccb6ea4f638baea1bfc6205ca8`（HOLDOUT）。审计发现差异来自 fixture controller 在主工作树与 clean worktree 的 CRLF/LF 文件表示不同，而非 task config/baseline/verifier 内容变化；这仍应作为 provenance P1 修复，不能静默忽略。

## 10. Dynamic Audit

v4 的 positive signal 是语义振荡检测：task033 在 DEV×5 为 5/5 TP，并且 protected recover cases 022/023/024/025/031/032 没有出现系统性新增 False Stop。Completion Guard 在 task034 上本轮得到 3/5 TP，但 task018/019/020/021 的永久 blocker 仍有 FN，说明 completion-time evidence 仍受模型执行轨迹影响。

按最终 DEV×5 有效样本统计，v4 为 TP=9、TN=24、FP=1、FN=14；baseline 为 TP=8、TN=17、FP=11、FN=22。INFRA 不进入混淆矩阵：v4 12/60，baseline 2/60。

## 11. GO / NO-GO

- Governance candidate：GO。v4 在有效 DEV/HOLDOUT 数据上优于 baseline，task033 有明确稳定改善，FP 没有系统性上升。
- Formal benchmark freeze：NO-GO，直到 suite hash provenance 和 DashScope 长请求 INFRA 率被修复或换用稳定、同条件的 provider execution profile。
- Runtime development：本轮结束后停止。不得根据这批结果继续改治理算法、threshold、fixture 或 verifier。

## 12. 可直接用于简历的描述

设计并实现一套冻结的跨语言 Anti-Loop / Failure Governance Benchmark，覆盖 Python、JVM、Node 和 Shell，支持多 Trial、隔离 Runtime data、durable Trial ledger 与结构化 TP/TN/FP/FN 评测；在保持 recoverability 的前提下，为 Agent 增加语义振荡检测和 completion-time blocker verification，使最终 DEV×5 corrected governance accuracy 相对原始 baseline 从 43.10% 提升到 68.75%，并通过离线 replay、mutation、跨语言 case 和 holdout 评测验证误杀与执行成本。

## 13. 面试问题与回答要点

1. **为什么 action repetition 不够？**
   - 同一个 action 可能得到不断改善的 observation；重复 action 本身不是 loop，必须联合 observation、state、verification 和 recoverability。

2. **Semantic Oscillation 如何避免误杀？**
   - 使用 normalized semantic fingerprint，支持 period-2/3，要求两个完整周期且没有 verification improvement；单次 A→B→A 不终止。

3. **为什么 v3 没直接保留？**
   - v3 把更大范围的恢复链路交给新状态机，离线和 Canary 证明方向有效但 FP 增加；最终只移植被 Trace 证实有效的 cycle detector。

4. **Completion Guard 为什么只在 completion-time 介入？**
   - blocker 后的正常探索可能仍在恢复；只在无 Tool Call 宣布完成时检查，降低对 recoverability 的侵入。

5. **什么算 blocker resolved？**
   - 原操作成功、等价 fallback 的真实 downstream verify 成功，或业务状态明确达到目标；新文件、report、unrelated exit=0 都只是弱证据。

6. **为什么不直接把所有失败都算 FP/FN？**
   - provider timeout、Agent init、missing trace 属于 INFRA/EVAL error；将它们放入治理混淆矩阵会把执行问题误报成算法能力。

7. **如何证明不是 Python-specific？**
   - Core 包含真实 javac、Node builtin/npm script、Shell process state；verifier 运行真实工具链和业务输出，不 grep evaluator 私有文件。

8. **DEV 和 HOLDOUT 如何防止过拟合？**
   - HOLDOUT 改变语言、拓扑、失败表达、观察和恢复路径；Prompt、fixture、verifier、metadata 和 split 在 Freeze 后不改。

9. **为什么 v4 不是无条件生产-ready？**
   - 治理 accuracy 提升，但 DashScope 固定 20 秒 timeout 造成 12/60 DEV INFRA，且 raw manifest hash 暴露了 worktree newline provenance 问题；所以算法候选 GO，正式测量基础设施 NO-GO 风险。

10. **下一步最值得解决什么？**
    - 先修 canonical fixture/provenance hash 和 provider execution reliability；随后再研究跨语言 semantic normalization、completion evidence 的业务状态抽象，以及低成本的 retry policy。不能先根据当前数据改 Runtime threshold。

## 14. 最终固化信息

- Winner Runtime lineage：Candidate v4，`1329a1e`。
- Evaluation Infrastructure：`8d4369d11259cd280ff5e3557a59486de8127c7d`。
- Benchmark Freeze：`7648e64`。
- Final DEV selected IDs：task018、019、020、021、022、023、024、025、031、032、033、034。
- Final HOLDOUT selected IDs：task026、027、028、029、035。
- Python：`D:\python3.12.1\python.exe`，Python 3.12.1。
- Provider：DashScope compatible API，model `deepseek-v4-flash-0731`，process proxy `http://127.0.0.1:7890`。

本轮评测结束后，不再修改 Runtime；后续只修复被明确授权的 provenance/provider execution infrastructure。
