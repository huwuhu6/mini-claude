# 多版本评测指标对账报告

> 自动由 `compare_reports.py` 生成 — 2026-08-17 21:49:34

> 版本: large_log_baseline_v2, log_to_file_refactor_v2
> 用例: task_013_large_log_debug

## 运行条件

| 版本 | Agent 提交 | 工作区 | Python | 平台 | 任务集 | 用例数 |
|---|---|---|---|---|---:|---:|
| `large_log_baseline_v2` | `6466917a` | clean | 3.12.1 | Windows-11-10.0.26200-SP0 | `e6ab49b5` | 1 |
| `log_to_file_refactor_v2` | `6466917a` | clean | 3.12.1 | Windows-11-10.0.26200-SP0 | `e6ab49b5` | 1 |

## 全局核心指标演进看板

| 版本 | 综合通过率 | 全量 Token | 平均 Token | 全量轮次 | 命中率范围 | 压缩次数 | 平均耗时 | 相比基线成本 | 用例数 |
|------|-----------|---------|---------|---------|-----------|---------|---------|-------------|-------|
| **large_log_baseline_v2** | 5/5 | 44.4k | 44.4k | 9.4 | 91% | 0 | 14.2s | — (基线) | 1 |
| **log_to_file_refactor_v2** | 5/5 | 59.2k | 59.2k | 11.4 | 86% | 0 | 16.7s | 🔺 33.3% | 1 |

## 多版本精简对比

> ✅ Nt · Nktok · N%hit · Ncmp · Ns    |    ❌ STATUS · Nt · Nktok · Ncmp

> 字段缺失显示 `-`；Token 数 ≥ 10000 自动缩写为 k 单位

| 测试用例 | 能力描述 | large_log_baseline_v2 | log_to_file_refactor_v2 |
|---------|---------|-----------|-----------|
| **task_013_large_log_debug** | Agent 能否在超长测试日志中定位唯一失败，并修复导致失败的配置参数。 | ✅ 5/5 · 9.4t · 44.4ktok 91%hit · 0cmp · 14.193999999999999s | ✅ 5/5 · 11.4t · 59.2ktok 86%hit · 0cmp · 16.674s |

## 细粒度明细对比

> 每个用例独立小节，指标各行，版本各列。
> Δ 列：相比基准版本的差值（🔺上升 / 🔻下降 / 持平）


### task_013_large_log_debug

> *Agent 能否在超长测试日志中定位唯一失败，并修复导致失败的配置参数。*

> 运行统计: large_log_baseline_v2: 5/5 | log_to_file_refactor_v2: 5/5

| 指标 | large_log_baseline_v2 | log_to_file_refactor_v2 | Δ |
|--------|--------|--------|--------|
| **验证结果** | SUCCESS | SUCCESS | — |
| **最终状态** | SUCCESS | SUCCESS | — |
| **总轮次** | 9.4 (7~12) | 11.4 (6~21) | +2.0 (🔺21.3%) |
| **总 Token** | 44,423 (28,973~63,254) | 59,210 (23,466~135,605) | +14,787 (🔺33.3%) |
| **Peak Turn Tokens** | 5982 (4772~7219) | 6627.2 (4951~10644) | +645 (🔺10.8%) |
| **总延迟** | 14.193999999999999s (11.33s~18.15s) | 16.674s (9.07s~29.06s) | +2.5 (🔺17.5%) |
| **工具调用次数** | 9.6 (7~12) | 12 (6~22) | +2.4 (🔺25.0%) |
| **工具命中率** | 91% (80%~100%) | 86% (69%~100%) | -5.0pp |
| **工具失败次数** | 1 (0~2) | 2 (0~4) | +1.0 (🔺100.0%) |
| **每轮 Token** | 4,617.5 (4,139.0~5,271.2) | 4,798.6 (3,911.0~6,457.4) | +181 (🔺3.9%) |
| **平均工具延迟** | 93.7ms (79.2ms~108.3ms) | 58.1ms (42.8ms~79.8ms) | -36 (🔻38.1%) |
| **循环守卫触发** | 0.2 (0~1) | 0 | -0.20 (🔻100.0%) |
| **熔断次数** | 0 | 0 | 持平 |
| **自愈收敛速度** | 2.6 (0~8) | 6.8 (0~14) | +4.2 (🔺161.5%) |
| **回滚次数** | 0 | 0 | 持平 |
| **压缩次数** | 0 | 0 | 持平 |
| **工具分布** | bash:5.2 edit_file:1.0 list_files:1.2 read_file:2.0 write_file:0.2 | bash:6.0 edit_file:1.0 list_files:1.8 read_file:2.8 search_code:0.4 | — |
| **Tool Call Sequence** | list_files -> read_file -> read_file -> bash -> edit_file -> bash -> bash | bash -> bash -> list_files -> list_files -> list_files -> read_file -> read_file -> bash -> bash -> search_code -> search_code -> edit_file -> bash | — |
| **Read Saved Log** | no | yes | — |
