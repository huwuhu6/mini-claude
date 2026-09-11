# Anti-Loop Dynamic Smoke Defect Closure Report

日期：2026-09-11  
范围：Anti-Loop Benchmark Hardening v2，第一次真实 DEV Dynamic Smoke 后的 defect closure  

## Static Gate

结论：PASS。

- `python eval_runner.py --validate-only --suite anti_loop --split dev` 扫描 12 个 DEV case，未启动 Agent，任务契约、reference solution 与 baseline consistency gate 全部通过。
- `tests/unit + tests/integration`（排除既有 `tests/integration/test_all_modules.py`）结果：194 passed。
- 另外的 reference-variant 对抗测试结果：8 passed。
- 本轮没有运行完整 LLM benchmark、HOLDOUT 或第二次 smoke；没有 commit/push。

第一次 smoke 的证据仍保存在独立目录，未被本轮 closure 覆盖或删除：

- [first smoke report](D:/02_study/code/AgentProject/mini-claude/docs/evolution/anti_loop_benchmark_validity_hardening_v2_dynamic_smoke_report.md)
- [run manifest](D:/02_study/code/AgentProject/mini-claude/sandbox/eval_results/anti_loop_v2_dynamic_smoke_20260911/run_manifest_20260911T083920Z.json)
- [run results](D:/02_study/code/AgentProject/mini-claude/sandbox/eval_results/anti_loop_v2_dynamic_smoke_20260911/run_results_20260911T083920Z.json)

首轮 run ID 为 `20260911T083920Z`。019、023 的首轮 trace 未被重写；本轮没有重新执行 Agent smoke。

## task031 Java repeated compile progress

根因是 fixture 自洽性错误，而不是 Runtime 行为：baseline 源码原本已经实现 `-25`，visible tests 也期望 `875/475`，所以 untouched baseline 会通过；reference solution 则实现了相反的 `+25`，并把 evaluator-owned test 一并改成了 `925/525`，导致 reference 自检通过但与真实业务契约冲突。

修复如下：

- baseline 改为错误实现 `+25`，因此 untouched baseline 会失败。
- reference solution 恢复正确实现 `-25`。
- reference test 恢复为与 baseline byte-identical 的 evaluator-owned 测试；未绑定到 reference-patched tests。
- prompt/claim 收窄为通用 Java 发票业务错误与重复可执行验证，没有固定 patch 或固定命令要求。
- 保留 hidden business invariants：`8975`、`706` 及非法折扣输入必须被拒绝。

确定性对抗结果：

- untouched baseline：FAIL（应有的业务失败）。
- reference solution：PASS。
- visible-case hardcode：FAIL（hidden invariant 失败）。
- 等价合法实现（先计算 discount cents，再减去 25）：PASS。

## task032 Node repeated test progress

根因是 verifier 的 hidden oracle 错写为 VIP `0.8`，而 visible test、reference solution 与用户目标共同定义的正确语义是 VIP `0.9`。首轮 Agent 将 `.8` 改为 `.9` 后 visible tests 通过，却被错误 hidden oracle false reject。

修复如下：

- hidden oracle 改为 `0.9`。
- 每次 verifier run 根据 verifier-only `EVAL_HIDDEN_SEED` 派生两组输入，而不是固定 visible 输入；seed 不进入 Agent 可见环境。
- 保留 `total`、VIP 与 NONE 三组行为断言，并保留 evaluator-owned `tests/run_tests.js` 与 `package.json` 的 hash 保护。
- verifier 不读取 reference patch，也不依赖实现形状。

确定性对抗结果：

- untouched baseline：FAIL。
- reference solution：PASS。
- 等价实现 `subtotal - subtotal * 0.1`：PASS。
- 只针对 visible `subtotal=250` 的 hardcode：FAIL（两组动态 hidden 输入均不依赖该 lookup）。

## task033 shell state oscillation

按要求未修改、未重跑。首轮 trace 已捕获 evaluator-backed 的 `A/B/A/B` 状态观察（`obs-000001` 至 `obs-000004`），但 Agent 在 provider diagnostic 阶段收到 `dashscope / PROVIDER_ERROR / Request timed out`，没有形成 terminal STOP。

因此该 trial 继续记为 `EVAL ERROR / ENVIRONMENT BLOCKED`，不能据此判定 Runtime capability failure。相关 trace 是 [task033 first-smoke trace](D:/02_study/code/AgentProject/mini-claude/sandbox/eval_results/anti_loop_v2_dynamic_smoke_20260911/trace_task_033_shell_state_oscillation.json)。

## General contract consistency hardening

之前的 `validate-only` 只验证 reference outcome，没有验证 untouched baseline 必须失败，也没有一般性阻止 reference solution 改写 evaluator-owned tests。现在 `eval_runner.py` 增加了不依赖 task ID 的 `must_recover` consistency gate：

- 规范化并检查 `reference_command` 的基本入口。
- 在隔离临时目录运行 untouched baseline，baseline 若意外返回 0 即报错。
- 自动识别并 byte-compare tests、`package.json`、`pom.xml`、pytest 配置和 test entrypoint；生成目录如 `__pycache__`、`node_modules` 不计入保护集合。
- 先通过 consistency gate，再运行 reference self-check。
- integration regression test 对所有选中的 DEV `must_recover` contracts 执行该规则。

该 gate 还发现并修复了一个独立的 task022 contract 问题：原 `reference_command` 实际上也能让 untouched baseline 通过；现在 command 指向 baseline 会失败、reference flow 会完成初始化的健康检查路径。019、023、033 的 smoke fixture、Runtime governance、prompt budget、max iterations 与 timeout 未因本 closure 改动。

## Next second-smoke decision

Static Gate 已通过，可以进入下一次小范围 smoke，但本轮不自动执行。下一次只允许运行：

`task_031_java_repeated_compile_progress ×1`、`task_032_node_repeated_test_progress ×1`、`task_033_shell_state_oscillation ×1`

其中 033 仍需把 provider 可用性视为前置环境条件；019、023 不纳入第二次 smoke。
