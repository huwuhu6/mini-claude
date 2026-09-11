# Polyglot Environment Guard

## 背景

此前 Runtime 主要依赖模型执行失败后再通过 Failure Intelligence 和重复意图计数收敛。这个路径无法在第一次确认网络、包仓库或操作系统权限不可用时及时停止，也无法识别不同语言生态使用不同错误文本的情况。

## 本次改造

- 在 Session 子系统启动前增加有预算的环境探针：根据工作区特征文件发现 Node、Python、Rust、Go、Java 相关工具链，并使用短 TCP 探测记录 ONLINE/OFFLINE。
- 将探针事实注入 system prompt、session JSONL 和 Task Trace，提示 Agent 在离线时停止外部依赖下载。
- 增加跨生态包不存在、网络/DNS 阻断和权限错误识别。离线的 `bash`/`run_background` 包管理下载命令在执行前拦截；执行后命中硬错误时直接写入 Trace 并以 `CIRCUIT_BROKEN` 结束任务。
- 增加 SHA256 工作区快照。`edit_file`、`write_file` 和明显的文件型 shell 操作连续两次没有实际文件增量，或查询工具连续三次重复同一目标时，触发 State Mutation Guard 并终止当前循环。

## 边界

快照忽略 `.git`、Agent 运行时目录、缓存和依赖目录，避免日志或解释器缓存被误判为业务代码变更。未知的脚本内部写入只有在其命令能够被识别为文件型操作时纳入守卫，后续如 Benchmark 证明覆盖不足，再扩展命令分类。

## 验证

本地测试覆盖离线探针、工作区特征驱动的工具链发现、跨生态硬错误分类和连续零增量写入。真实 Benchmark 由负责人在改造后的独立版本目录中分别运行 `task_015_offline_dependency_block` 与 `task_016_stalled_code_edit` 各 5 次，再使用 `compare_reports.py` 生成对账报告。
