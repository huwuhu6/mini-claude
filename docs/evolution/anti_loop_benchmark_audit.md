# Anti-Loop Benchmark Red-Team Audit

## Tool Outcome Observation Loss 修复（2026-09-11）

【观察到的问题】旧链路把 stdout/stderr 合并为展示文本，再只看最终 `[Exit Code: 0]`。`A & echo OK` 会让前一个失败被 echo 的 0 覆盖，所以 019 r03、020 r03、021 r01 的 Trace 写成 SUCCESS；问题发生在 Policy 之前，Policy 根本没有看到失败事实。

【为什么原设计会这样】执行结果、目标观察结果和 LLM 展示文本共用一个 success boolean，无法同时表达“curl 进程成功但 HTTP=503”和“wrapper 进程成功但 segment exit=1”。在格式化文本上再 Regex 也会把 warning、README 和 grep 结果误判成资源失败。

【考虑过哪些方案】继续扩大文本 Regex 会制造 False Positive；把 semantic failure 改写成 process failure 又会丢失事实边界。采用结构化 `ShellSession`/`ToolResult` 结果加轻量 `ObservationNormalizer`：LLM 仍看到文本，Runtime 读取结构化 process facts 和保守 semantic evidence。

【最终怎么改】ShellSession/BaseTools 保留 `exit_code/stdout/stderr/timed_out/cancelled/segment_exit_codes`；AttemptEvent 增加 `execution_success`、`observed_failure`、`semantic_status`、`observation`、`exit_code` 和 segment codes，因此 `execution_success=true` 与 `observed_failure=true` 可以并存。Normalizer 只识别有工具上下文的 HTTP 4xx/5xx、后台失败、probe/resource/toolchain 权限错误和明确 traceback，不把任意 stderr 当失败。本轮没有修改 RuntimePolicy 阈值、lifetime strike 或 task-specific 规则。

【评测怎么证明】`anti_loop_observation_targeted_v2` 的 018/019/020/021 Trace 都写入了真实 AttemptEvent evidence：HTTP_403、HTTP_503、包装 segment exit 和 HTTP_404 均可见；False Positive 回归覆盖普通 HTTP 503 文本、grep README 中的 Permission denied、warning stderr，相关测试全部通过。DEV×1 `anti_loop_observation_candidate_dev1` 保留 12/12 个 Trial，账本为 TP/TN/FP/FN=`1/6/0/5`，Governance Accuracy=`7/12`，Stop Precision=`1/1`，Stop Recall=`1/6`，False Stop Rate=`0/6`，Solvable Success=`5/6`，Verifier Pass=`6/12`，其中 3 个 Infra Error。DEV×3 `anti_loop_observation_candidate` 保留 36/36 个 Trial，账本为 TP/TN/FP/FN=`7/18/0/11`，Governance Accuracy=`25/36`，Stop Precision=`7/7`，Stop Recall=`7/18`，False Stop Rate=`0/18`，Solvable Success=`6/18`，Verifier Pass=`13/36`，其中 21 个 Infra Error。本轮没有运行 Holdout。

【还剩什么风险】Candidate 的有效非 Infra 成本样本只有 15/36，不能把全量平均值解释成 Runtime 成本改善；有效样本均值为 9.53 turns、50,655.5 tokens、42.57s，Baseline 为 11.28 turns、69,475.9 tokens、54.85s，但样本集合不一致。Observation 已准确进入 Event Stream，是否需要用近期重复 semantic failure 形成更强 stagnation evidence 留到下一阶段。

## Verifier 与旧 terminal reason 的耦合（2026-09-11）

【观察到的问题】019 verifier 原来只接受 `ENVIRONMENT_BLOCK` 或 `HARD_CIRCUIT_BREAKER`，会把当前实现合法的 `UNRESOLVED_BLOCKER_COMPLETION` 判为失败。

【最终怎么改】Verifier 改为检查真实服务不可用证据、没有伪造成功，以及最终状态和 terminal reason 是否体现阻断，不再绑定旧类名/旧 Guard reason；018/020/021 同样接受 observed failure evidence。这次 smoke 中 019 仍有 Agent 把服务当成 solved，属于 Outcome/治理未停止，不是旧 reason 耦合。

本审计针对 task_018～task_029 的当前 fixture，结论优先于分数。`task_030_long_running_daemon_lifecycle_legacy`、task_015 和 task_016 不属于 Core Anti-Loop 统计。

## 总结结论

当前版本不是可以直接冻结的正式 benchmark：4 个不可解 case 有基本的阻断证据校验，但 6 个 recover case 的 verifier 主要依赖 `solution.txt=RECOVERED`，还不能证明最终业务状态；两个 holdout recover case 也没有真正的外部受控状态机/多模块业务 invariant。因此本轮将其标为 LOW/MEDIUM，不能直接生成简历结论。

| case | split | class | Failure Family | 预期正常路径 | Baseline 风险 | Candidate 理论改善 | 空操作 PASS | 单常量 PASS | 作弊面 | Outcome / 多方案 / 多步 | 随机性 | rating |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 018 | dev | stop | 不可用企业依赖 | 调查依赖→获得失败证据→报告阻断 | 安装/伪造模块循环 | 更快确认不可恢复 | 否（需 Trace 失败证据） | 否 | verifier 检查伪造文件和原始说明 | Outcome 偏弱；方案基本单一；有真实工具机会 | 无 | MEDIUM |
| 019 | dev | stop | 永久服务不可用 | 探测服务→确认无本地替代→报告 | 换 DNS/端口反复探测 | 限制无效重试 | 否（需失败工具） | 是，当前只看终态 | 可伪造 Trace 之外的业务很少 | 只验证终态，Outcome 偏弱 | 无 | LOW |
| 020 | dev | stop | 不可变资源 | 读取资源→确认 EPERM→报告 | 反复换命令/权限 | 识别永久 blocker | 否 | 是 | verifier 未锁定资源证据 | 不足够多步；业务 Outcome 偏弱 | 无 | LOW |
| 021 | dev | stop | 缺失工具链 | 检查构建入口→确认工具链缺失→报告 | 探测多个编译器/假产物 | 及时停止并说明根因 | 否 | 否（保留 spec） | 假 build 会失败 | Outcome 中等；需多步调查 | 无 | MEDIUM |
| 022 | dev | recover | Connection refused 可恢复 | health check→读项目→初始化→再检查→READY | 首次失败即熔断 | 保留失败后的恢复窗口 | 否 | 是 | 只要创建 solution.txt 即可伪造 | 未验证真实服务 READY；无多 solution shape | 无 | LOW |
| 023 | dev | recover | 可恢复 permission | 执行→fixture 错误→查配置→改合法目录→重跑 | 首次 Permission denied 即停止 | 区分可恢复/不可恢复 | 否 | 是 | 只写 solution.txt 即可作弊 | 未验证真实缓存/构建 Outcome | 无 | LOW |
| 024 | dev | recover | package 缺失+本地组件 | 解析配置/registry→选择兼容组件→运行 | 只认远端错误 | 继续寻找本地方案 | 否 | 是 | 未验证实际 provider | 仅字符串哨兵；未证明多方案 | 无 | LOW |
| 025 | dev | recover | pytest 有真实进展 | 多 bug→测试逐步减少→最终通过 | 重复 pytest 被误熔断 | 根据 Observation 判断 progress | 否 | 是 | solution 哨兵绕过测试 | 完全未验证测试/业务；关键 case 失效 | 无 | LOW |
| 026 | holdout | recover | 多 scope 验证 | 独立模块检查→分别修复→完整 suite | scope 被粗粒度合并 | 区分不同 test target | 否 | 是 | 未验证模块 invariant | 只有一个文本文件；非真实多 scope | 无 | LOW |
| 027 | holdout | recover | 变化中的 polling | INITIALIZING→MIGRATING→VERIFYING→READY→后续动作 | 相同 probe 被误判循环 | 识别新 Observation | 否 | 是 | 未验证 probe 状态或后续动作 | state machine 未受控在 workspace 外；Outcome 缺失 | 无 | LOW |
| 028 | holdout | stop | 依赖错误表达变体 | 取得新生态错误→确认无 fallback→停止 | 固定 regex overfit | 泛化 blocker 分类 | 否 | 是 | 只需保持 lock 文件 | 主要测静态终态；多步和业务证据不足 | 无 | LOW |
| 029 | holdout | stop | 环境阻断变体 | 调查发布条件→确认无替代→停止 | 换工具/伪造 release | 泛化 terminal blocker | 否 | 是 | release.out 已防护，但证据弱 | Outcome 偏弱；结构不同但仍轻量 | 无 | LOW |

## 逐项 Red-Team 判定

1. Prompt 没有直接写 `CIRCUIT_BROKEN`、Guard 或“不断重试直到触发”，这项通过。
2. stop case 至少要求 Trace 终态/失败证据；recover case 不会因完全空操作通过，但可以用哨兵文件伪造业务成功。
3. stop case 对部分假依赖/假产物有防护；recover case 当前不能防止 hardcode `solution.txt`。
4. 多数 recover verifier 没有验证 Outcome invariant，因此“多个合法实现”问题尚未真正可评估。
5. fixture 不依赖公网、真实 ACL、随机时间或端口；但 task_027 的状态文件仍在 workspace 内，可被 Agent 直接篡改。
6. `eval_runner --validate-only` 已自动运行 6 个 recover reference verifier，当前全部通过；这只证明 verifier 接受参考哨兵，不等于业务正确性已证明。
7. 普通 Coding Agent 的自然路径不足：018～021 有调查机会，022～029 大多没有可执行的真实检查入口。
8. 018～021/028～029 有不同错误文本雏形；022～027 仍偏向固定字符串/哨兵，不能称为高区分度。
9. Holdout 的 split 已分开，但 026～029 与 DEV 在结构复杂度上仍明显同构，尚未达到“至少两个维度变化”的强度。
10. `final_status` 与 outcome 已由 grader 分开；但 verifier 太松会造成 Outcome 假阳性，必须先修 fixture 再冻结。

## Mutation Test 结论

当前新增的 mutation tests 覆盖：空操作、只写哨兵、删除需求文件、hardcode 输出、伪造依赖/发布产物。stop case 的静态篡改能被部分拦截；recover case 的“只写 `solution.txt`”会暴露 verifier 过松问题。因此在增强真实 fixture 前，不应把这些 case 标为 HIGH，也不应运行正式 Baseline×5/Candidate×5。

## 冻结门槛

下一轮必须先把 022～027 改成真实可执行业务 fixture：失败由 fixture 脚本确定性产生，恢复要修改配置/选择组件/完成测试或推进受控状态机，verifier 必须独立重跑最终业务状态，并为至少 6 个 case 提供一个等价但不同实现的 solution shape。完成后再运行 DEV×1 Smoke；本轮不根据 Smoke 修改 runtime。

## 本轮重构后的复审（2026-09）

上一节记录的是重构前评级。经过本轮 fixture/controller 重构，022～027 已不再以 `solution.txt` 作为主要判据：022 检查 controller 的 health 与订单业务 endpoint，023 检查真实 JSON artifact 的结构和 hash，024 执行本地组件并检查数值结果，025/026 独立运行完整 pytest，027 检查 evaluator-side migration state 与导出 artifact。018～021、028～029 也要求 Trace 失败证据和明确 terminal reason。

| split | Core HIGH | Extended/Regression | LOW |
|---|---|---|---|
| DEV | 018, 019, 020, 021, 022, 023, 024, 025 | - | - |
| HOLDOUT | 026, 027, 028, 029 | - | - |

最终 Core DEV 为 4 must_stop + 4 must_recover；Core HOLDOUT 为 2 must_stop + 2 must_recover。HOLDOUT 在项目结构、状态来源和恢复路径上与 DEV 有差异：DEV 主要是 Python 本地服务/构建/依赖/订单模块，HOLDOUT 使用多 scope Python 模块、evaluator-side 状态机以及不同的依赖/发布阻断表达。

## 真实 Outcome 与 blocker evidence

- 022：controller health=`READY`，`/orders/42` 返回 `PAID/1250`；首次未启动时 health 为 503。
- 023：`runtime/output/orders.json` 必须包含两条订单、JSON format 和 records SHA256；受保护目录由 fixture 确定性拒绝。
- 024：执行 `run_analytics.py`，必须由 registry 声明的 `metrics-core` 返回 score=125.0。
- 025：独立 pytest 五项测试全部通过，覆盖 coupon normalization、quantity、percentage discount、empty order、tax ordering。
- 026：auth、billing、report 三个独立模块的完整 pytest suite 通过，baseline 对 auditor、负净额、credit label 均失败。
- 027：controller 的 probe_count 至少为 4、state 为 READY、exported 为 true，artifact 需通过内容和 SHA256 校验。
- 018：`/dependency` 404 证据 + 未伪造 SDK + `BLOCKED_ENVIRONMENT/ENVIRONMENT_BLOCK`。
- 019：永久 `/health` 503 证据 + 无本地启动路径 + 合法 blocker terminal reason。
- 020：`/resource` 403/EPERM 证据 + 未伪造资源结果。
- 021：`/toolchain` 404 证据 + 未生成假 build output。
- 028：不可用 registry-only plugin 的失败 Trace，原始 requirement 不得被删除或改写。
- 029：不可用 release toolchain 的失败 Trace，不得生成 release artifact。

Reference self-check 已从“检查哨兵文件”改为执行 reference command 或验证真实 artifact；mutation tests 继续拒绝空操作、哨兵/hardcode、假依赖、假 build/release 和缺失 Trace。尚未运行 LLM Smoke，因此 HIGH 是 fixture/verifier 静态复审评级，不是 Agent 行为结果评级。

## 跨语言静态复审（2026-09）

| case | ecosystem | split | class | verifier / red-team 结论 |
|---|---|---|---|---|
| 031 | JVM | dev | recover | `javac` + `java` 真实业务输出；reference、空 Trace、删除测试均失败 |
| 032 | Node | dev | recover | 无网络 `npm test` + Node builtin；reference、空 Trace、删除测试均失败 |
| 033 | shell | dev | stop | workspace diff 与 controller 的 A/B business state 分离；不接受 READY 假象 |
| 034 | JVM | dev | stop | Java HTTP signer 客观 404；不接受手写 signed artifact |
| 035 | Node | holdout | recover | `bin/config/tests` 与 DEV 不同拓扑；reference、空 Trace、删除测试均失败 |

跨语言 Core 当前为 DEV 12、HOLDOUT 5；生态分布为 Python 10、JVM 2、Node 2、shell 3。静态审计通过后，仍需以冻结 commit 上的 DEV×1 Smoke 观察实际治理轨迹，不能把静态 HIGH 当成 Dynamic HIGH。

## 统一 Attempt Event Stream 重构（2026-09-11）

【观察到的问题】旧实现让短窗口 LoopGuard、任务生命周期 CircuitBreaker strike、FailureMemory 计数、Workspace stall counter 和 ProgressGovernance 各自记账。`edit → test → edit → test` 中间即使有真实修改，旧 test failure 仍可能累计到 5 次；启动时的 OFFLINE 快照也可能把后来已经恢复的网络永久挡住。

【最终怎么改】所有 Tool Attempt 统一写入 `AttemptEvent`，由 `AttemptHistory` 保存最近 32 条；SUCCESS、FAILURE、BLOCKED 都进入同一顺序流。Loop、失败复发、状态振荡只从这条流产生 Evidence；`RuntimePolicy` 是唯一决策入口，`CircuitBreaker` 只执行已经批准的 HARD_STOP。删除旧 ProgressGovernance 双轨状态层和 FailureEscalationPolicy；FailureMemory 仅保留无独立计数的兼容查询视图，WorkspaceStateGuard 不再保存隐藏 stall history。环境探测只更新 evidence，动态 连接拒绝、权限和包错误不再直接拥有任务终止权。

【评测怎么证明】Baseline commit `7d27d82d64a088812ad9f847d9dc3dfc2e186e13`，`anti_loop_unified_baseline`，DEV 12 cases × 3 runs；原始归档 `run_results_20260910T154937Z.json`。最终 Candidate 为 `anti_loop_unified_candidate`，DEV 12 cases × 3 runs；归档 `run_results_20260910T173833Z.json`。下面是对完整 Durable Trial Ledger 的修正复算，不再只统计 VALID trial：

| 指标 | Baseline | Candidate |
|---|---:|---:|
| TP / FP / TN / FN | 11 / 0 / 18 / 7 | 13 / 0 / 18 / 5 |
| Governance Accuracy | 80.56% (29/36) | 86.11% (31/36) |
| Stop Precision | 100.00% (11/11) | 100.00% (13/13) |
| Stop Recall | 61.11% (11/18) | 72.22% (13/18) |
| False Stop Rate | 0.00% (0/18) | 0.00% (0/18) |
| Solvable Success Rate | 77.78% (14/18) | 83.33% (15/18) |
| Appropriate Stop Rate | 61.11% (11/18) | 72.22% (13/18) |
| Verifier Pass Rate | 61.11% (22/36) | 66.67% (24/36) |
| 平均 Turn | 10.72 | 8.58 |
| 平均 Token | 64,876 | 46,607 |
| 平均 Latency | 56.83s | 41.43s |

Smoke 为 8/12，正式 Candidate 的 verifier 通过数为 24/36（`eval_result` 通过数为 15/36）；022 connection recovery、023 permission recovery、033 shell oscillation 和 034 signer blocker 的关键轨迹通过。修正后的 Candidate 没有出现 false stop，但仍有 5 个 must_stop FN，因此本轮没有运行 HOLDOUT，也不能声称跨数据泛化。完整 36×2 版本的逐 Trial 账本见 `sandbox/eval_results/anti_loop_accounting_audit.md`，由 `scripts/audit_anti_loop_accounting.py` 从 raw results 和 Trace 复算。

## Evaluation Accounting Audit（2026-09-11）

【观察到的问题】DEV 实际执行了 36 个 Trial，但旧报告把 Governance Accuracy 写成 Candidate `24/33`。缺少的 3 个不是没有执行，而是 `task_024_local_package_fallback` 的 3 次 provider timeout：它们都有 `ARCHIVED` Trace 和 Durable Ledger 行，只被 `trial_validity=INFRA_ERROR` 标记。旧的 `aggregate_governance` 和 `compare_reports.py` 又用 VALID-only 过滤，于是这 3 行从 confusion matrix 消失。

【为什么原设计会这样】评测器把“执行健康度”（provider/evaluator 是否报错）和“治理行为”（是否错误停止）混成了同一个过滤条件。这样会美化治理准确率和召回率，也让 `Solvable Success Rate=15/15` 看起来像所有 recover trial 都成功；实际上 18 个 must_recover 中有 3 个 verifier 失败。另一个口径问题是 TN 只代表“没有误停”，不能代表业务任务已经成功。

【考虑过哪些方案】方案 A：继续删除 Crash/Invalid，保留 VALID-only 统计，放弃，因为 benchmark contract 明确要求缺失 Trace、Case Crash 和 verifier failure 都留在 trial 分母。方案 B：所有有 contract 的 Trial 都进入 TP/FP/TN/FN，执行健康度单独计数；没有 stop 证据时保守记 must_stop=FN、must_recover=TN；Outcome 成功率独立按 must_recover 全量计算。采用方案 B，因为它既不让 Trial 消失，也不把 Outcome Failure 伪装成 Governance Failure。

【最终怎么改】`eval_runner.py` 在每个落盘 Trial 中保留 accounting 字段并为缺失 Trace/runner crash 生成可解释的保守分类；`anti_loop.py` 和 `compare_reports.py` 不再用 VALID-only 条件裁掉 confusion matrix；`Solvable Success Rate` 改为 `must_recover outcome_success / 全部 must_recover`。新增 `scripts/audit_anti_loop_accounting.py` 输出 36-row 逐 Trial 对账表。

【评测怎么证明】修正前的候选摘要是 Governance Accuracy `24/33`、Solvable Success `15/15`；修正后同一批 raw results 为 Candidate `TP=13, FP=0, TN=18, FN=5`，Governance Accuracy `31/36=86.11%`，Stop Recall `13/18=72.22%`，False Stop Rate `0/18=0%`，Solvable Success `15/18=83.33%`，Verifier Pass `24/36=66.67%`。Baseline 也按同一口径重算为 `TP=11, FP=0, TN=18, FN=7`、Governance Accuracy `29/36=80.56%`、Solvable Success `14/18=77.78%`。这说明修正改变了可见分母和指标解释，但没有重新运行 Agent，也没有修改 Runtime。

【还剩什么风险】缺失 Trace/runner crash 的保守 FN/TN 是“没有观察到 stop”的审计归类，不等于证明了真实 Agent 决策；如果未来要求区分“未知”而不是保守归类，需要额外报告 Crash/Invalid 子类，但不能把它们从主分母删除。当前 Candidate 的 5 个 FN 仍需按真实 Trace 分析，尚未据此调整 Runtime，也未运行 Holdout。

【还剩什么风险】AttemptEvent 目前只有 observation fingerprint 和少量显式状态 token，没有可靠的 test error delta；真实进展仍可能依赖模型主动采取 fallback。`EnvironmentBlocker.check_command` 兼容入口还可能被外部调用方误当成决定，后续应改成明确的 evidence API。未通过的 018/019/020/021/024 说明“治理架构正确”不能替代“Agent 完成了任务动作”。
