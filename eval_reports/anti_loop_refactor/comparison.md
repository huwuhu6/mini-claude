# 多版本评测指标对账报告

> 自动由 `compare_reports.py` 生成 — 2026-08-18 23:42:14

> 版本: baseline_pre_guard_9e175f4, refactor_polyglot_guard_b115d5b
> 用例: task_015_offline_dependency_block, task_016_stalled_code_edit

## 运行条件

| 版本 | Agent 提交 | 工作区 | Python | 平台 | 任务集 | 用例数 |
|---|---|---|---|---|---:|---:|
| `baseline_pre_guard_9e175f4` | `9e175f45` | clean | 3.8.6 | Windows-10-10.0.26100-SP0 | `d561dd34` | 2 |
| `refactor_polyglot_guard_b115d5b` | `b115d5b7` | clean | 3.8.6 | Windows-10-10.0.26100-SP0 | `a2dcc5ab` | 2 |
> ⚠ 选中版本的 task suite hash 不一致，汇总差异不能直接归因于 Agent 代码变化。
> 建议：先确认任务集和运行环境，再解释轮数、Token 或成功率的变化。

## 全局核心指标演进看板

| 版本 | 综合通过率 | 全量 Token | 平均 Token | 全量轮次 | 命中率范围 | 压缩次数 | 平均耗时 | 相比基线成本 | 用例数 |
|------|-----------|---------|---------|---------|-----------|---------|---------|-------------|-------|
| **baseline_pre_guard_9e175f4** | 3/10 | 139.4k | 69.7k | 22.4 | 59%-76%-92% | 0 | 48.1s | — (基线) | 2 |
| **refactor_polyglot_guard_b115d5b** | 5/10 | 40.6k | 20.3k | 9.0 | 15%-44%-72% | 0 | 8.5s | 🔻 70.9% | 2 |

## 多版本精简对比

> ✅ Nt · Nktok · N%hit · Ncmp · Ns    |    ❌ STATUS · Nt · Nktok · Ncmp

> 字段缺失显示 `-`；Token 数 ≥ 10000 自动缩写为 k 单位

| 测试用例 | 能力描述 | baseline_pre_guard_9e175f4 | refactor_polyglot_guard_b115d5b |
|---------|---------|-----------|-----------|
| **task_015_offline_dependency_block** | 测试 Agent 在遇到公网完全不存在的硬依赖时，能否在少轮次内识别并主动向用户上报阻断，而不是无限尝试重试。 | ⚠ 3/5 · 15.2t · 107.8ktok 59%hit · 0cmp · 84.62s | ✅ 5/5 · 1.4t · 4947tok 15%hit · 0cmp · 3.3660000000000005s |
| **task_016_stalled_code_edit** | 测试 Agent 在局部代码匹配持续歧义且工作区没有状态变更时，系统能否按目标文件快速熔断重复编辑。 | ❌ 0/5 · ❌ SUCCESS · 7.2t · 31.6ktok · 0cmp | ❌ 0/5 · ❌ SUCCESS · 7.6t · 35.6ktok · 0cmp 4%blk |

## 细粒度明细对比

> 每个用例独立小节，指标各行，版本各列。
> Δ 列：相比基准版本的差值（🔺上升 / 🔻下降 / 持平）


### task_015_offline_dependency_block

> *测试 Agent 在遇到公网完全不存在的硬依赖时，能否在少轮次内识别并主动向用户上报阻断，而不是无限尝试重试。*

> 运行统计: baseline_pre_guard_9e175f4: 3/5 | refactor_polyglot_guard_b115d5b: 5/5

| 指标 | baseline_pre_guard_9e175f4 | refactor_polyglot_guard_b115d5b | Δ |
|--------|--------|--------|--------|
| **验证结果** | SUCCESS | SUCCESS | — |
| **最终状态** | SUCCESS | SUCCESS | — |
| **总轮次** | 15.2 (5~23) | 1.4 (1~3) | -14 (🔻90.8%) |
| **总 Token** | 107,760 (19,988~232,840) | 4,947 (3,440~10,893) | -102,813 (🔻95.4%) |
| **Peak Turn Tokens** | 11376.2 (4781~26357) | 3525 (3440~3783) | -7851 (🔻69.0%) |
| **总延迟** | 84.62s (17.61s~167.85s) | 3.3660000000000005s (2.33s~6.16s) | -81 (🔻96.0%) |
| **工具调用次数** | 23.2 (8~31) | 1.6 (1~4) | -22 (🔻93.1%) |
| **工具命中率** | 59% (45%~65%) | 15% (0%~75%) | -44.1pp |
| **工具失败次数** | 9.4 (3~13) | 1 | -8.4 (🔻89.4%) |
| **每轮 Token** | 6,418.9 (3,997.6~11,087.6) | 3,494.6 (3,440.0~3,631.0) | -2924 (🔻45.6%) |
| **平均工具延迟** | 2141.7ms (448.3ms~5333.0ms) | 1196.1ms (567.0ms~1599.3ms) | -946 (🔻44.2%) |
| **循环守卫触发** | 0 | 0 | 持平 |
| **熔断次数** | 0.2 (0~1) | 1 | +0.80 (🔺400.0%) |
| **自愈收敛速度** | 4 (0~11) | 0 | -4.0 (🔻100.0%) |
| **回滚次数** | 0 | 0 | 持平 |
| **压缩次数** | 0 | 0 | 持平 |
| **工具分布** | bash:16.6 list_files:1.6 read_file:3.8 search_code:0.4 write_file:0.8 | bash:1.2 list_files:0.2 read_file:0.2 | — |
| **Tool Call Sequence** | bash -> list_files -> bash -> bash -> list_files -> read_file -> bash -> bash -> bash -> bash -> bash -> bash -> bash -> bash -> bash -> bash -> bash -> bash -> bash -> read_file -> read_file -> bash -> bash -> bash -> read_file -> bash | bash | — |
| **Read Saved Log** | no | no | — |


### task_016_stalled_code_edit

> *测试 Agent 在局部代码匹配持续歧义且工作区没有状态变更时，系统能否按目标文件快速熔断重复编辑。*

> 运行统计: baseline_pre_guard_9e175f4: 0/5 | refactor_polyglot_guard_b115d5b: 0/5

| 指标 | baseline_pre_guard_9e175f4 | refactor_polyglot_guard_b115d5b | Δ |
|--------|--------|--------|--------|
| **验证结果** | FAILED | FAILED | — |
| **最终状态** | SUCCESS | SUCCESS | — |
| **总轮次** | 7.2 (6~8) | 7.6 (5~11) | +0.40 (🔺5.6%) |
| **总 Token** | 31,595 (24,847~37,324) | 35,649 (20,288~57,468) | +4054 (🔺12.8%) |
| **Peak Turn Tokens** | 5213.8 (4823~5656) | 5652 (4679~7005) | +438 (🔺8.4%) |
| **总延迟** | 11.66s (9.45s~13.31s) | 13.663999999999998s (7.21s~23.59s) | +2.0 (🔺17.2%) |
| **工具调用次数** | 7 (5~9) | 8 (5~10) | +1.0 (🔺14.3%) |
| **工具命中率** | 92% (83%~100%) | 72% (40%~88%) | -19.9pp |
| **工具失败次数** | 0.6 (0~1) | 2 (1~3) | +1.4 (🔺233.3%) |
| **每轮 Token** | 4,366.2 (4,141.2~4,665.5) | 4,593.0 (4,057.6~5,224.4) | +227 (🔺5.2%) |
| **平均工具延迟** | 72.0ms (34.9ms~131.8ms) | 52.8ms (3.0ms~74.3ms) | -19 (🔻26.7%) |
| **循环守卫触发** | 0 | 0.2 (0~1) | +0.20 |
| **熔断次数** | 0 | 0.2 (0~1) | +0.20 |
| **自愈收敛速度** | 2.2 (0~4) | 3.2 (0~5) | +1.0 (🔺45.5%) |
| **回滚次数** | 0 | 0 | 持平 |
| **压缩次数** | 0 | 0 | 持平 |
| **工具分布** | bash:1.6 edit_file:2.4 read_file:3.0 | bash:1.8 edit_file:2.8 read_file:3.4 | — |
| **Tool Call Sequence** | read_file -> read_file -> edit_file -> read_file -> edit_file -> read_file -> bash | read_file -> read_file -> edit_file -> read_file -> bash -> edit_file -> read_file -> bash | — |
| **Read Saved Log** | no | no | — |
