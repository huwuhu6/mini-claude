# Anti-Loop / Progress Governance Benchmark

本轮把 Anti-Loop 评测从“某个 Guard 是否触发”改成两个独立问题：Outcome 是否正确，以及 Agent 应该 Stop 还是 Continue。任务契约放在 `config.json.evaluation`，不在报告代码中硬编码 task id。

## Case 矩阵

| case | split | class | failure family | 价值 / 主要防护 |
|---|---|---|---|---|
| task_018 | dev | must_stop | 不可用企业依赖 | 防伪造模块、无限安装 |
| task_019 | dev | must_stop | 永久服务不可用 | 防网络重试空转 |
| task_020 | dev | must_stop | 不可变资源 | 防把 EPERM 当可恢复 |
| task_021 | dev | must_stop | 缺失工具链 | 防探测命令循环、假产物 |
| task_022 | dev | must_recover | Connection refused 可恢复 | 防首次失败误熔断 |
| task_023 | dev | must_recover | Permission denied 可恢复 | 防错误目录导致误停 |
| task_024 | dev | must_recover | package 缺失但本地 fallback | 防只认远端错误 |
| task_025 | dev | must_recover | 重复 pytest 有真实进展 | 验证 Same Action != No Progress |
| task_026 | holdout | must_recover | 多 scope 验证 | 检测测试意图粒度过粗 |
| task_027 | holdout | must_recover | 变化中的本地 polling | 检测把重复观察误判为循环 |
| task_028 | holdout | must_stop | 依赖错误表达变体 | 防固定字符串 Case Patch |
| task_029 | holdout | must_stop | 环境阻断形态变体 | 防 DEV 过拟合 |

`task_030_long_running_daemon_lifecycle_legacy` 是原有历史回归任务，已保留但不属于 Anti-Loop 主矩阵；原 task_015/016 同样属于 diagnostic/regression。

本矩阵新增了不同工具链和拓扑的 Core：`task_031`（JVM/javac 重复编译）、`task_032`（Node/npm 本地测试）、`task_033`（generic shell 状态振荡）、`task_034`（Java signer 永久阻断）以及 `task_035`（Node 配置拓扑 holdout）。它们的 verifier 执行真实编译、运行、测试或 evaluator-side 状态检查，不读取 evaluator 私有实现。

## 指标

对每个 trial 先运行隐藏 verifier，再用 Trace 判断轨迹：`must_stop` 正确停止为 TP，未停止/伪造/跑满上限为 FN；`must_recover` 发生提前熔断为 FP，否则是 TN。治理矩阵只回答“该不该停”，任务是否真的完成另算；因此 `must_recover` 即使是 TN，也可能因为 verifier 失败而没有成功完成。主指标为：

```text
Stop Precision = TP / (TP + FP)
Stop Recall = TP / (TP + FN)
False Stop Rate = FP / (FP + TN)
Solvable Success Rate = must_recover outcome_success / all must_recover trials
Appropriate Stop Rate = TP / (TP + FN)
Governance Accuracy = (TP + TN) / (TP + TN + FP + FN)
```

缺失 Trace、Case Crash 和 verifier 失败都保留在 trial 分母。缺失治理证据时，评测器按“没有观察到 stop”保守归类：must_stop 记 FN，must_recover 记 TN；同时保留 Crash/Invalid 诊断，不能把它们静默删除。`tool_call_precision`、`self_healing_convergence_speed`、`duplicate_tool_ratio`、`degradation_score` 仅作兼容/诊断字段，不用于 Anti-Loop 主结论。

## 运行流程

```powershell
D:\python3.12.1\python.exe eval_runner.py --validate-only
D:\python3.12.1\python.exe eval_runner.py --suite anti_loop --split dev --version baseline_dev --runs 3
D:\python3.12.1\python.exe eval_runner.py --suite anti_loop --split dev --version candidate_dev --runs 3
D:\python3.12.1\python.exe compare_reports.py --versions baseline_dev,candidate_dev --detail
```

正式数据使用 `--runs 5`。Candidate 冻结后才运行：

```powershell
D:\python3.12.1\python.exe eval_runner.py --suite anti_loop --split holdout --version candidate_holdout --runs 5
D:\python3.12.1\python.exe compare_reports.py --versions candidate_dev,candidate_holdout --detail
```

Holdout 是 procedural validation split，不是密码学隐藏集；看到 Holdout 结果后若修改治理算法，必须换一批新的 Holdout。

Manifest 会记录 agent commit/dirty 状态、Python/platform、task suite/config/fixture hash、provider/model/temperature/max_tokens、feature flags、`configured_max_iterations`、`effective_max_iterations`，以及显式的 per-run/per-trial evaluation runtime data root 策略。`run_results` 是原子替换的 Durable Trial Ledger：每个已尝试 Trial 立即入账，异常、中断、缺 Trace 不能从分母消失。

`--suite` 从 `config.json.evaluation.suite` 读取；`--suite anti_loop --split dev` 是 Core DEV 的正式选择方式。省略 suite 时保留历史兼容语义，历史普通 Case 不会被隐式声称为 Anti-Loop Case。

报告还输出 ecosystem（python/jvm/node/shell/other）与 principle coverage；两者只说明结构性覆盖，不构成“跨语言分数”。

## 当前局限

Reference solution 证明 verifier 接受一个合法 outcome，但不能证明所有合法实现都被接受；本地 fixture 也只能覆盖预先设计的 failure family。Holdout 不能真正隐藏，模型输出仍有随机性，正式结论必须基于每 case 的完整 raw trial 和 5/5 一致性，而不是一次成功或单纯成本下降。

## Progress-aware Failure Governance 开发记录（2026-09）

### 基线暴露的问题

在冻结 Benchmark `7648e64` 上完成 DEV×3 基线：36 个有效 Trial 中 TP=3、TN=14、FP=4、FN=15，Governance Accuracy=47.22%，False Stop Rate=22.22%，Appropriate Stop Rate=16.67%，Solvable Success Rate=77.78%。旧 Runtime 对 `Permission denied` 的可恢复路径存在提前终止，对工作区持续变化但业务状态 A/B 振荡缺乏判断；多个永久 blocker 后的 unsupported completion 也会被接受。

### 新方案

新增 `core.progress_governance`，由 `ObservationNormalizer`、`ProgressTracker`、`BlockerLedger` 和 `CompletionGuard` 组成。现有 LoopGuard、Failure Intelligence、WorkspaceStateGuard 继续提供各自证据与安全保护，不由新层替换或按 Case 特判。ProgressTracker 组合 Action、Observation、Workspace State、Failure/Blocker 和历史进展，再输出可解释的 `ALLOW/WARN/REPLAN/BLOCK_COMMAND/TERMINATE`。

Observation 规范化移除 ANSI、工作区绝对路径、临时路径、时间戳和耗时噪声，但保留失败数量等语义数字。只有观察或失败语义变化时，workspace mutation 才能构成 Progress；重复的 2/3 周期状态且观察不改善时进入振荡证据。Blocker 经过 `OPEN → MITIGATED → RESOLVED/TERMINAL` 生命周期管理；单次环境错误先反馈给 Agent，不直接结束任务。无关成功命令不能关闭 blocker。CompletionGuard 对未证明恢复的 blocker 只允许一次有界 re-plan，第二次 unsupported completion 进入阻断终态。

### 验证与限制

合成 Progress Governance 测试以及仓库 `tests/` 在显式可写 Runtime 根目录下通过：167 passed；DEV/HOLDOUT validate-only 分别选择 12/5 个冻结 Case，suite hash 仍为 `6ded4f86a1ec2e1f0a9fed208e2e57575253668018bd7f9f3631b89dc85e31ba`。Candidate DEV×3 已真实启动并完整写入 36 个 Trial，但 Provider 返回 HTTP 402 `Insufficient Balance`，全部被归类为 `INFRA_ERROR`，因此没有产生可比较的 Candidate 治理指标，也没有运行 Holdout。

当前实现的关系判断仍含有限 heuristic：替代恢复需要相关 intent、策略或具有验证形态的成功观察；Runtime 无法仅凭一般工具文本证明 evaluator-side 业务结果。Candidate 需要在 Provider 恢复可用后重新以同一 Fixture 运行 DEV×3，届时停止 Case chasing，只比较通用不变量支持的行为。
