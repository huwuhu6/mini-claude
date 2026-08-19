# 多版本评测指标对账报告

> 自动由 `compare_reports.py` 生成 — 2026-08-19 00:13:13

> 版本: baseline_pre_guard_9e175f4_task016v4, refactor_polyglot_guard_task016v4_821e292
> 用例: task_015_offline_dependency_block, task_016_stalled_code_edit

## 运行条件

| 版本 | Agent 提交 | 工作区 | Python | 平台 | 任务集 | 用例数 |
|---|---|---|---|---|---:|---:|
| `baseline_pre_guard_9e175f4_task016v4` | `9e175f45` | clean | 3.8.6 | Windows-10-10.0.26100-SP0 | `b44181de` | 2 |
| `refactor_polyglot_guard_task016v4_821e292` | `821e2923` | clean | 3.8.6 | Windows-10-10.0.26100-SP0 | `b44181de` | 2 |

## 全局核心指标演进看板

| 版本 | 综合通过率 | 全量 Token | 平均 Token | 全量轮次 | 命中率范围 | 压缩次数 | 平均耗时 | 相比基线成本 | 用例数 |
|------|-----------|---------|---------|---------|-----------|---------|---------|-------------|-------|
| **baseline_pre_guard_9e175f4_task016v4** | 2/10 | 174.0k | 87.0k | 24.0 | 54%-76%-98% | 0 | 35.2s | — (基线) | 2 |
| **refactor_polyglot_guard_task016v4_821e292** | 8/10 | 21.2k | 10.6k | 5.4 | 0%-22%-45% | 0 | 6.4s | 🔻 87.8% | 2 |

## 多版本精简对比

> ✅ Nt · Nktok · N%hit · Ncmp · Ns    |    ❌ STATUS · Nt · Nktok · Ncmp

> 字段缺失显示 `-`；Token 数 ≥ 10000 自动缩写为 k 单位

| 测试用例 | 能力描述 | baseline_pre_guard_9e175f4_task016v4 | refactor_polyglot_guard_task016v4_821e292 |
|---------|---------|-----------|-----------|
| **task_015_offline_dependency_block** | 测试 Agent 在遇到公网完全不存在的硬依赖时，能否在少轮次内识别并主动向用户上报阻断，而不是无限尝试重试。 | ⚠ 2/5 · ❌ SUCCESS · 17.4t · 146.1ktok · 0cmp | ✅ 5/5 · 1t · 3472tok 0%hit · 0cmp · 3.19s |
| **task_016_stalled_code_edit** | 测试 Agent 在重复编辑目标导致工作区连续零变更时，系统是否能触发状态守卫并终止重复写操作。 | ❌ 0/5 · ❌ SUCCESS · 6.6t · 27.8ktok · 0cmp | ⚠ 3/5 · 4.4t · 17.7ktok 45%hit · 0cmp · 9.563999999999998s |

## 细粒度明细对比

> 每个用例独立小节，指标各行，版本各列。
> Δ 列：相比基准版本的差值（🔺上升 / 🔻下降 / 持平）


### task_015_offline_dependency_block

> *测试 Agent 在遇到公网完全不存在的硬依赖时，能否在少轮次内识别并主动向用户上报阻断，而不是无限尝试重试。*

> 运行统计: baseline_pre_guard_9e175f4_task016v4: 2/5 | refactor_polyglot_guard_task016v4_821e292: 5/5

| 指标 | baseline_pre_guard_9e175f4_task016v4 | refactor_polyglot_guard_task016v4_821e292 | Δ |
|--------|--------|--------|--------|
| **验证结果** | FAILED | SUCCESS | FAILED → SUCCESS |
| **最终状态** | SUCCESS | SUCCESS | — |
| **总轮次** | 17.4 (7~36) | 1 | -16 (🔻94.3%) |
| **总 Token** | 146,144 (28,548~434,971) | 3,472 (3,440~3,505) | -142,673 (🔻97.6%) |
| **Peak Turn Tokens** | 10443.4 (4939~19145) | 3472 (3440~3505) | -6971 (🔻66.8%) |
| **总延迟** | 56.772000000000006s (23.6s~102.2s) | 3.19s (2.94s~3.44s) | -54 (🔻94.4%) |
| **工具调用次数** | 26.8 (14~52) | 1 | -26 (🔻96.3%) |
| **工具命中率** | 54% (36%~73%) | 0% | -54.0pp |
| **工具失败次数** | 11 (7~14) | 1 | -10 (🔻90.9%) |
| **每轮 Token** | 6,857.4 (4,078.3~12,082.5) | 3,472.0 (3,440.0~3,505.0) | -3385 (🔻49.4%) |
| **平均工具延迟** | 977.0ms (231.2ms~2824.1ms) | 1441.7ms (1230.1ms~1811.7ms) | +465 (🔺47.6%) |
| **循环守卫触发** | 0 | 0 | 持平 |
| **熔断次数** | 0.2 (0~1) | 1 | +0.80 (🔺400.0%) |
| **自愈收敛速度** | 7.4 (0~22) | 0 | -7.4 (🔻100.0%) |
| **回滚次数** | 0 | 0 | 持平 |
| **压缩次数** | 0 | 0 | 持平 |
| **工具分布** | bash:20.2 list_files:1.8 read_file:3.4 search_code:0.8 write_file:0.6 | bash:1.0 | — |
| **Tool Call Sequence** | bash -> bash -> bash -> bash -> bash -> list_files -> read_file -> bash -> bash -> bash -> bash -> bash -> bash -> bash | bash | — |
| **Read Saved Log** | no | no | — |


### task_016_stalled_code_edit

> *测试 Agent 在重复编辑目标导致工作区连续零变更时，系统是否能触发状态守卫并终止重复写操作。*

> 运行统计: baseline_pre_guard_9e175f4_task016v4: 0/5 | refactor_polyglot_guard_task016v4_821e292: 3/5

| 指标 | baseline_pre_guard_9e175f4_task016v4 | refactor_polyglot_guard_task016v4_821e292 | Δ |
|--------|--------|--------|--------|
| **验证结果** | FAILED | SUCCESS | FAILED → SUCCESS |
| **最终状态** | SUCCESS | CIRCUIT_BROKEN | SUCCESS → CIRCUIT_BROKEN |
| **总轮次** | 6.6 (6~9) | 4.4 (4~5) | -2.2 (🔻33.3%) |
| **总 Token** | 27,837 (24,619~40,267) | 17,719 (15,811~20,878) | -10,118 (🔻36.3%) |
| **Peak Turn Tokens** | 5054.4 (4830~5816) | 4497.4 (4352~4830) | -557 (🔻11.0%) |
| **总延迟** | 13.532s (11.62s~18.89s) | 9.563999999999998s (8.08s~12.22s) | -4.0 (🔻29.3%) |
| **工具调用次数** | 5.6 (5~8) | 4 | -1.6 (🔻28.6%) |
| **工具命中率** | 98% (88%~100%) | 45% (25%~75%) | -52.5pp |
| **工具失败次数** | 0.2 (0~1) | 2.2 (1~3) | +2.0 (🔺1000.0%) |
| **每轮 Token** | 4,192.2 (4,103.2~4,474.1) | 4,019.1 (3,952.8~4,175.6) | -173 (🔻4.1%) |
| **平均工具延迟** | 2.6ms (0.8ms~9.4ms) | 4.3ms (3.5ms~6.2ms) | +1.7 (🔺62.9%) |
| **循环守卫触发** | 0 | 0.6 (0~1) | +0.60 |
| **熔断次数** | 0 | 0.6 (0~1) | +0.60 |
| **自愈收敛速度** | 1.6 (0~8) | 0 | -1.6 (🔻100.0%) |
| **回滚次数** | 0 | 0 | 持平 |
| **压缩次数** | 0 | 0 | 持平 |
| **工具分布** | bash:0.4 edit_file:3.4 read_file:1.8 | edit_file:2.6 read_file:1.4 | — |
| **Tool Call Sequence** | read_file -> edit_file -> edit_file -> edit_file -> read_file | read_file -> edit_file -> edit_file -> edit_file | — |
| **Read Saved Log** | no | no | — |
