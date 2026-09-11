# mini-claude Evaluation Harness Contract

这是 authoring 时的项目参考，不是永久快照。每次使用 Skill 仍要重新读取代码和 `config.json`；本文件只保存那些容易被误读、会改变 Benchmark 设计的约定。

## 目录与生命周期

当前 Runner 的任务根目录是 `sandbox/tasks/`，每个任务通常为：

```text
sandbox/tasks/task_<三位序号>_<简短名称>/
├── baseline/                 Agent 开始时复制的初始项目
├── config.json               prompt、case_id、验证和 evaluation metadata
├── verify.py                 Agent 结束后才复制并执行
└── reference_solution/       anti_loop/must_recover 的可执行参考结果
```

`eval_runner.py` 会清空并重建 `sandbox/shadow_workspace/`，复制 `baseline/`（复制时跳过 `node_modules` 和 `__pycache__`），启动 Agent，结束后才复制 `verify_script_file`，执行 verifier，再归档 Trace 和 `run_results`，最后清理 Shadow Workspace 与本次显式 Runtime data root。Verifier 不应假设 Agent 能看到自己的源码。

Runner 只把 `EVAL_TRACE_PATH` 注入 verifier；它指向 Agent 可写 workspace 之外、在 verifier 启动前仍可读的原始 Trace。`EVAL_FIXTURE_URL`、`EVAL_FIXTURE_TOKEN`、`EVAL_FIXTURE_RUNTIME` 会随 fixture controller 注入 Agent 进程，因此不是秘密答案。若使用它们，必须把它们当作正常可调查环境证据，并审查变量名是否泄露 evaluator 语义。

## 当前 `config.json` 契约

`eval_runner._validate_task()` 当前机械检查：

- `case_id` 等于任务目录名；
- `prompt` 是非空字符串；
- `baseline/` 存在；
- `verify_script_file` 若存在则是任务目录内真实文件；若目录中存在 `verify.py`，config 不能省略声明；
- `evaluation.suite == "anti_loop"` 时，`split` 必须是 `dev`/`holdout`，`behavior_class` 必须是 `must_stop`/`must_recover`。

常用但不应盲目添加的字段包括 `task_version`、`description`、`reference_command`、`expected_final_status` 和：

```json
"evaluation": {
  "suite": "anti_loop",
  "split": "dev",
  "behavior_class": "must_recover",
  "ecosystem": "node",
  "principles": ["temporary_failure_recovery"]
}
```

这些字段提供 metadata，不会代替 semantic QA。`expected_final_status` 只适合 Claim 确实要求某一终态的 Case；不要为了让 verifier 通过而机械指定 `CIRCUIT_BROKEN`。`verify_script_file` 可以显式为 `null`，但新的可判定 Benchmark 通常应有隐藏 verifier。

对于 `anti_loop` 的 `must_recover`，当前 Runner 的 reference self-check 要求 `reference_solution/` 和 `verify_script_file`，并可先执行 `reference_command`；然后以 `EVAL_REFERENCE_CHECK=1` 执行 verifier。Reference 分支应真的验证 reference outcome，不要无条件 `exit 0`。命令解析使用简单 token split，Windows/JVM/Node 命令要按当前代码和平台实际验证。

## Anti-Loop 的两层记账

`src/core/evaluation/anti_loop.py` 将两个问题分开：

1. Outcome：任务是否通过 verifier、是否以 `SUCCESS` 结束、是否伪造结果。
2. Governance：运行时是否应该 STOP。

在当前 contract 中，`must_stop` 的正确治理是 TP；没有正确 stop 是 FN。`must_recover` 没有误停是 TN，但 TN 不代表业务成功；业务成功要另看 `outcome_success`/verifier。`FP` 表示 recover Case 被提前停止。缺失 Trace、case crash、verifier/evaluator 错误会保留在 Trial Ledger，不能用 VALID-only 过滤美化分母；缺失 stop 证据时 Runner 的保守归类也不等于证明了真实决策。

当前 Runtime 的 stop 证据可能出现在 Task Trace 或嵌套 Tool/Attempt Event 中。`AttemptEvent` 同时保留 `execution_success`、`observed_failure`、`semantic_status`、`observation`、`failure_category`、`recoverability`、`strategy_fingerprint`、`observation_fingerprint`、`semantic_state`、`verification_improved`、`workspace_changed`、`subject_key` 和 governance decision。进程成功与目标不健康可以同时成立；verifier 不应只看一个 `success` 布尔值。

`RuntimePolicy` 是当前统一决策入口，`LoopController` 只是 facade，`CircuitBreaker` 只执行已批准的 hard stop。Loop、Failure recurrence、状态 oscillation 和 resolution evidence 都来自有限的 `AttemptHistory`，不能把旧的私有计数器、固定类名或 task id 当成 Benchmark 合约。当前支持的决策文本包含 `ALLOW`、`REPLAN`、`HARD_STOP` 等，但 Benchmark 应验证可观察行为，除非 Claim 本身就是某个 runtime 状态。

## Fixture controller 与隔离边界

当前 `sandbox/eval_runtime/controller.py` 是 localhost-only、token 保护的 deterministic HTTP controller，状态在 Agent Shadow Workspace 外。它支持不同 Case 所需的服务、dependency/resource/toolchain、polling、export、signer 等响应。当前实现按 case id 路由，这是现有 harness 的历史实现细节，不是新 Benchmark 应复制的设计原则。

如果使用 controller：

- 让 response、状态转移和合法 recovery path 成为正常可调查证据；
- 不把 `token`、URL、endpoint 名称或 controller 的 case 分支当成 label；
- verifier 使用真实状态/结果，不直接 import evaluator 私有实现；
- 需要增加 controller 分支时，先确认现有接口不能表达该能力，并把它作为明确的最小 harness 变更提出；
- 不依赖公网、随机真实服务、宿主机 ACL 或会污染解释器/全局 package 的操作。

## Trace、Manifest 与比较

Trace 是 `TaskTrace -> TurnTrace -> ToolTrace` 三层结构，保存 tool call、stdout/stderr 的 bounded preview、failure evidence、workspace digest、progress/governance 字段和最终状态。`eval_runner.py` 归档增强 Trace，并把 `evaluation_metadata` 放入 Trace。

每批评测在 `sandbox/eval_results/<version>/` 写 `run_manifest_<run_id>.json` 和 `run_results_<run_id>.json`。Manifest 记录 Agent commit/dirty 状态、Python/platform、task suite/config/baseline/verify hash、fixture controller hash、provider/model 等；报告按 run id 绑定 Trace，并显示未覆盖 Case。正式比较必须保持同一 fixture/hash、明确的 Agent commit 和干净工作区。版本目录名本身不能证明代码版本。

`--validate-only` 只做 task contract/reference 的确定性检查，不启动 Agent；默认未指定 suite 的非校验运行保留历史兼容语义并选 `dev`，正式 Anti-Loop 选择应显式使用 `--suite anti_loop --split dev|holdout`。Skill 可以运行 validate-only 和 author-side mutation，但不代替用户做高成本 LLM 评测。

## 当前矩阵的背景快照（必须重扫）

本次创建 Skill 时仓库有 35 个 task，其中 17 个带 `evaluation.suite=anti_loop`：当前 Anti-Loop DEV 12、HOLDOUT 5；DEV 主要包含 Python permanent/recovery、JVM compile/signer、Node test 和 shell oscillation，HOLDOUT 包含多 scope/polling、错误表达变体、环境阻断变体和 Node topology 变体。历史普通 Case 与 legacy daemon 不应自动混入 Anti-Loop 结论。

现有本地树在部分任务下仍可见由运行产生的 `__pycache__`，尽管 Runner/hash 会跳过它们；新增或修改任务时必须清理，而不是把这个偶然状态当作合法 baseline 内容。当前矩阵、task id、ecosystem 数量和 controller 分支都会变化，未来 Skill 调用必须以仓库实际扫描结果为准。
