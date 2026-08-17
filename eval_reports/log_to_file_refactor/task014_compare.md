# 多版本评测指标对账报告

> 自动由 `compare_reports.py` 生成 — 2026-08-17 22:45:18

> 版本: large_log_middle_baseline_v1, large_log_middle_refactor_v1
> 用例: task_014_large_log_middle_debug

## 运行条件

| 版本 | Agent 提交 | 工作区 | Python | 平台 | 任务集 | 用例数 |
|---|---|---|---|---|---:|---:|
| `large_log_middle_baseline_v1` | `5111599f` | clean | 3.12.1 | Windows-11-10.0.26200-SP0 | `f440451f` | 1 |
| `large_log_middle_refactor_v1` | `2dd60c22` | clean | 3.12.1 | Windows-11-10.0.26200-SP0 | `f440451f` | 1 |

## 全局核心指标演进看板

| 版本 | 综合通过率 | 全量 Token | 平均 Token | 全量轮次 | 命中率范围 | 压缩次数 | 平均耗时 | 相比基线成本 | 用例数 |
|------|-----------|---------|---------|---------|-----------|---------|---------|-------------|-------|
| **large_log_middle_baseline_v1** | 5/5 | 254.0k | 254.0k | 14.8 | 87% | 0 | 22.6s | — (基线) | 1 |
| **large_log_middle_refactor_v1** | 5/5 | 79.3k | 79.3k | 13.8 | 90% | 0 | 21.6s | 🔻 68.8% | 1 |

## 多版本精简对比

> ✅ Nt · Nktok · N%hit · Ncmp · Ns    |    ❌ STATUS · Nt · Nktok · Ncmp

> 字段缺失显示 `-`；Token 数 ≥ 10000 自动缩写为 k 单位

| 测试用例 | 能力描述 | large_log_middle_baseline_v1 | large_log_middle_refactor_v1 |
|---------|---------|-----------|-----------|
| **task_014_large_log_middle_debug** | Agent 能否在只有中间日志包含有效诊断信息的超长测试输出中定位并修复配置问题。 | ✅ 5/5 · 14.8t · 254.0ktok 87%hit · 0cmp · 22.588s | ✅ 5/5 · 13.8t · 79.3ktok 90%hit · 0cmp · 21.59s |

## 细粒度明细对比

> 每个用例独立小节，指标各行，版本各列。
> Δ 列：相比基准版本的差值（🔺上升 / 🔻下降 / 持平）


### task_014_large_log_middle_debug

> *Agent 能否在只有中间日志包含有效诊断信息的超长测试输出中定位并修复配置问题。*

> 运行统计: large_log_middle_baseline_v1: 5/5 | large_log_middle_refactor_v1: 5/5

| 指标 | large_log_middle_baseline_v1 | large_log_middle_refactor_v1 | Δ |
|--------|--------|--------|--------|
| **验证结果** | SUCCESS | SUCCESS | — |
| **最终状态** | SUCCESS | SUCCESS | — |
| **总轮次** | 14.8 (9~19) | 13.8 (10~21) | -1.0 (🔻6.8%) |
| **总 Token** | 253,998 (128,003~481,086) | 79,288 (48,164~123,976) | -174,710 (🔻68.8%) |
| **Peak Turn Tokens** | 24295.4 (20024~38114) | 7804.8 (5863~10547) | -16,491 (🔻67.9%) |
| **总延迟** | 22.588s (17.41s~29.27s) | 21.59s (15.42s~32.5s) | -1.00 (🔻4.4%) |
| **工具调用次数** | 15 (9~20) | 14 (10~21) | -1.0 (🔻6.7%) |
| **工具命中率** | 87% (80%~100%) | 90% (82%~100%) | +3.2pp |
| **工具失败次数** | 2.2 (0~4) | 1.4 (0~3) | -0.80 (🔻36.4%) |
| **每轮 Token** | 16,835.1 (11,796.8~28,299.2) | 5,664.6 (4,816.4~6,982.5) | -11,170 (🔻66.4%) |
| **平均工具延迟** | 79.7ms (58.7ms~103.5ms) | 84.6ms (47.0ms~160.3ms) | +4.9 (🔺6.1%) |
| **循环守卫触发** | 0 | 0 | 持平 |
| **熔断次数** | 0 | 0 | 持平 |
| **自愈收敛速度** | 6 (0~8) | 6.6 (0~13) | +0.60 (🔺10.0%) |
| **回滚次数** | 0 | 0 | 持平 |
| **压缩次数** | 0 | 0 | 持平 |
| **工具分布** | bash:9.2 edit_file:1.0 list_files:1.0 read_file:3.2 write_file:0.6 | bash:7.4 edit_file:1.0 list_files:1.4 read_file:3.2 search_code:1.0 | — |
| **Tool Call Sequence** | list_files -> read_file -> read_file -> bash -> bash -> edit_file -> bash -> bash -> bash | list_files -> list_files -> list_files -> read_file -> read_file -> bash -> search_code -> bash -> read_file -> read_file -> edit_file -> bash -> bash -> bash | — |
| **Read Saved Log** | no | yes | — |
