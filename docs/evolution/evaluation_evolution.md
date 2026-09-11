# Evaluation Framework Evolution

这份文档记录评测模块的背景、试错过程、当前实现、指标口径和后续决策。

它只在本地维护，不纳入 Git。评测代码发生重要变化时，按下面的结构追加内容；普通重构和小修不单独记录。

## 1. 背景与目标

这个项目不是只验证 Agent 最终有没有改对文件，还要观察 Agent 是如何完成任务的：

- 是否陷入重复工具调用或无效重试；
- 是否因为读取过多上下文导致 Token 浪费；
- 是否因为编辑定位错误污染工作区；
- 失败后是否改变策略并恢复；
- 不同版本 Agent 的改动是否真的改善了任务结果。

因此，评测模块同时承担两件事：

1. 用独立验证脚本判断任务是否完成；
2. 从 Trace 中提取轮数、Token、工具调用和失败恢复等过程指标。

## 2. 当前任务模型

当前任务采用固定目录结构：

```text
sandbox/tasks/task_xxx/
├── baseline/       Agent 开始前复制到 shadow workspace 的初始文件
├── config.json     prompt、任务标识和验证脚本声明
└── verify.py       Agent 完成后运行的独立验证脚本
```

Agent 执行时只能看到 `baseline/` 的内容。`verify.py` 在 Agent 执行结束后才复制进 shadow workspace，避免 Agent 直接修改验证脚本。

当前任务覆盖：

- 单文件调试和编辑；
- 大文件编辑；
- 非唯一上下文定位；
- 跨文件修改；
- 搜索、读取和验证工具回归；
- 算法和数据处理代码生成；
- HTTP 重试封装；
- 多目录、多文件的仓库级迁移。

## 3. 最初发现的问题

### 3.1 结果目录名不能证明代码版本

最初通过 `--version` 生成：

```text
sandbox/eval_results/v20.1/
sandbox/eval_results/v20.2/
```

但 `--version` 只是输出目录标签，Agent 仍然从当前工作区导入。报告无法回答“这个结果到底对应哪个 Git commit”。

### 3.2 新增任务后历史结果无法自然扩展

任务会持续增加。旧版本的历史报告只包含当时存在的任务，不能把新任务结果直接补写到旧报告中，否则会混淆实验条件。

当前采用的阶段性原则是：

```text
旧报告保持不变
新任务集形成新的实验条件
重新运行旧 Agent + 新任务集，得到新的结果
```

历史 Agent 自动 checkout 和新旧任务集交叉测试暂未实现。

### 3.3 任务输入没有启动前校验

`eval_runner.py` 原先只要求存在 `config.json`，直接读取 `prompt` 和 `verify_script_file`。这导致任务配置错误只能在 Agent 执行后才暴露，甚至可能被当成一次普通失败或跳过验证。

实际发现 `task_007_java_cognitive_noise_rebuild` 存在 `verify.py`，但 config 没有声明 `verify_script_file`，因此原先会跳过验证。

### 3.4 任务输入没有被纳入版本追踪

原先 `sandbox/` 整体被 `.gitignore` 忽略，任务的 `baseline`、`config.json` 和 `verify.py` 并没有被 Git 管理。即使 Agent 代码有 commit，任务输入仍可能在本地悄悄变化。

## 4. 本轮修改

### 4.1 增加任务契约校验

在 Agent 启动之前检查：

- `case_id` 必须和目录名一致；
- `prompt` 必须是非空字符串；
- `baseline/` 必须存在；
- `verify_script_file` 必须存在；
- 验证脚本路径不能越出任务目录；
- 目录中存在 `verify.py` 时，config 必须显式声明它。

任务契约不完整时，评测器会在启动 Agent 前直接失败，并列出具体任务和错误原因。

### 4.2 修复 task_007

为 `task_007_java_cognitive_noise_rebuild/config.json` 增加：

```json
"verify_script_file": "verify.py"
```

这样该任务才会真正执行验证脚本，而不是被标记为 `SKIPPED`。

### 4.3 增加运行 provenance

每次运行会在结果目录写入：

```text
run_manifest_<run_id>.json
```

当前记录：

- Agent Git commit；
- 工作区是否 dirty；
- Python 版本和平台；
- 任务集摘要；
- 每个任务的 config、baseline、verify 内容哈希。

同时，归档的 Trace 会写入相同的 `evaluation_metadata`，避免 Trace 脱离运行上下文后无法解释。

### 4.4 放开任务 fixture 的 Git 管理

`.gitignore` 现在只忽略生成的 `sandbox/eval_results/` 等运行产物，并允许 `sandbox/tasks/` 纳入 Git。任务 fixture 仍需要后续通过 Git add 正式加入版本库。

## 4.5 增加 validate-only 模式

### 动机

任务契约校验原本只在完整评测启动时执行，而完整评测会导入 Agent、初始化上下文压缩组件并依赖模型环境。这样即使只是想检查 `config.json` 和 `verify.py`，也可能因为缺少 API、tiktoken 编码缓存或其他运行依赖而无法开始。

### 方案

新增：

```bash
py eval_runner.py --validate-only
py eval_runner.py --validate-only --task task_004_large_file_edit
```

同时将 `MiniClaudeAgent` 的导入延迟到真正执行 case 时。校验模式只扫描任务目录、验证契约并输出错误，不创建 Agent、不写入评测 manifest、不产生评测结果。

### 结果

当前 12 个任务可以在不加载 Agent 和 tiktoken 的情况下完成契约校验。这个入口适合在提交任务 fixture 前快速检查，也为后续 CI 校验保留了简单入口。

## 4.6 为评测契约增加回归测试

仅有 `--validate-only` 还不够，因为校验逻辑本身也可能在后续修改中回归。于是增加 `tests/integration/test_eval_contract.py`，覆盖：

- 当前 12 个任务全部通过契约校验；
- `task_007` 显式声明 `verify.py`；
- 验证脚本不能通过相对路径越出任务目录；
- `node_modules` 和 `__pycache__` 的变化不会改变 baseline fixture 哈希。

这组测试只导入评测器的纯函数，不启动 Agent、不访问 provider，适合单独快速运行。

## 5. 指标定义

### `eval_result`

验证脚本的最终状态：

- `SUCCESS`：验证脚本返回码为 0；
- `FAILED`：验证脚本返回非 0，或 Agent 没有生成 Trace；
- `CRASHED`：验证脚本异常或超时；
- `SKIPPED`：任务明确没有验证脚本。

它只表示任务结果，不代表每个工具调用都正确。

### `total_turns`

Agent 与 provider 交互的总轮数，来自原始 Trace。它受 prompt、模型随机性、上下文压缩和失败恢复策略影响。

### `total_tokens`

Trace 记录的 Token 总量。跨版本比较时需要保持 provider、模型和 Token 统计方式一致。

### `total_latency_seconds`

从 case 开始准备 sandbox 到清理结束的墙钟时间，包含 Agent、verify 和清理过程，不等同于纯 LLM 延迟。

### `tool_call_precision`

当前实际计算方式是：

```text
成功工具调用数 / 总工具调用数
```

它更接近 `tool_execution_success_rate`，暂时保留旧字段名以兼容历史报告，不能按分类任务中的 precision 解读。

### `loop_guard_blocking_rate`

```text
被 LoopGuard 拦截的工具调用数 / 总工具调用数
```

它表示防循环机制的介入比例，不能单独解释为越高越好或越低越好，需要结合任务成功率和最终轮数判断。

### `self_healing_convergence_speed`

当前定义为：最后一次 bash 类工具失败后，到任务结束经历的剩余轮数。它是过程指标，不是严格意义上的自愈速度；没有 bash 失败时为 0。

## 6. 当前结果与验证

本轮已完成：

- `eval_runner.py` 和 `compare_reports.py` 编译通过；
- `task_007` config 可以正常解析；
- 任务契约校验逻辑已加入运行入口；
- `git diff --check` 通过。
- `py eval_runner.py --validate-only` 可以脱离 Agent 运行并校验任务契约。
- `py -m pytest tests/integration/test_eval_contract.py -q` 覆盖评测契约和 fixture 哈希回归。

完整评测尚未运行，因为当前环境中 `tiktoken` 首次初始化需要联网下载编码资源，测试和评测导入阶段会受网络代理影响。

## 7. 尚未解决的问题

### 历史 Agent 版本交叉测试

当前 manifest 记录 commit，但不会自动 checkout。下一步如果确有需要，可以研究用临时 Git worktree 运行指定 commit；在此之前不把 `--version` 命名成真正的 Agent 版本。

### 多次运行的统计稳定性

当前报告主要计算均值、最大值和最小值，尚未加入中位数、P90、标准差和失败率置信区间。

### verify 标准不完全统一

现有 verify 同时包含行为测试、子进程输出匹配、源码正则检查和 AST 检查。后续需要区分“任务正确性”和“实现过程诊断”，但暂不重写已有任务。

## 8. 后续决策原则

- 先修复会改变结论正确性的缺陷，再增加新指标；
- 先记录 provenance，再讨论历史版本自动复测；
- 任务验证优先于过程指标；
- 单次运行只用于观察趋势，多次运行才用于简历数字；
- 每次新增字段都要说明统计口径和已知偏差；
- 不为尚未出现的规模问题提前引入数据库、分布式队列或复杂任务 DSL。

## 9. Task 017: Shell 环境跨调用持久化

`task_017_stateful_shell_env` 使用两个独立的 `bash` 调用，先设置
`AUTH_STAGE=staging`，再运行初始 fixture 中的 `check_env.py`。验证脚本同时
检查脚本完整性、成功回执和原始 Trace：设置变量与运行验证脚本必须分别发生在
两个工具调用中，且验证命令不得重复设置变量。这样不会把同一条命令内的临时环境
变量误判为 Shell 会话跨调用持久化。

为支持这一类过程契约，评测器仅在执行 `verify.py` 时通过 `EVAL_TRACE_PATH`
传入原始 Trace 的绝对路径。该 Trace 位于 Agent 可写工作区之外；普通任务无需
使用这个变量。该机制只服务评测验证，不改变 Agent Runtime 行为。

## 10. Task 018: 常驻服务生命周期

`task_018_long_running_daemon_lifecycle` 使用固定的本地 HTTP 服务端口 `8765`。
任务要求 Agent 用 `run_background` 启动 `server.py`，再通过 `health_check` 访问
`/health`。验证器先审计原始 Trace，确认异步启动与 HTTP 健康检查都确实发生，再在
`agent.shutdown()` 已返回后检查端口是否已经释放。

该用例的基线预期是：服务能够启动且健康检查成功，但 `shutdown()` 调用无任务 ID 的
`background.stop()`，从而遗留服务进程并使 verify 失败。它主要测量后台常驻服务的
退出回收；前台 `bash` 启动服务的 120 秒阻塞不作为每轮必经路径，避免把固定超时成本
混入生命周期回收指标。

## 4.7 让对比报告展示运行条件

### 背景

前一轮已经让 `eval_runner.py` 为每次评测写入 `run_manifest_*.json`，其中包含 Agent commit、工作区状态、运行环境和任务集哈希。但 `compare_reports.py` 原先只扫描 `trace_*.json`，报告读者仍然只能看到“哪个目录”和“哪些指标”，无法判断两个版本是否在相同任务集和环境下运行。

这会削弱评测结论的可信度：如果新版本新增了任务，或者历史目录没有保留运行清单，轮数和成功率的变化就不能直接归因于 Agent 实现。

### 本轮修改

`compare_reports.py` 现在会为每个选中的版本读取最近的运行清单，并在报告开头增加“运行条件”表，展示：

- Agent commit 的短哈希和工作区是否 dirty；
- Python 版本和平台；
- 任务集哈希和清单中的用例数。

报告还会在以下情况追加警告：

- 版本目录没有 manifest，通常意味着这是旧格式结果，实验条件无法完整追溯；
- 选中版本的 task suite hash 不一致，此时汇总指标不能直接解释为 Agent 代码优化效果。

旧结果仍然可以生成报告，只会显示缺失信息和警告，不会因为没有 manifest 而中断历史对比。

### 结果

已使用 `baseline` 和 `v20.1` 的历史结果实际生成报告，确认旧目录会进入兼容路径并显示缺失清单警告。新评测运行完成后，报告会自动显示对应的提交、环境和任务集信息。

### 当前边界

当前读取的是每个版本目录按修改时间最近的 manifest；如果同一版本目录长期累积多批实验，报告仍然是“目录级别”的汇总。后续只有在确实需要按 run 精确对比时，再考虑增加 run ID 过滤，不提前引入更复杂的实验数据库。

## 4.8 按 run_id 绑定 trace 与运行清单

### 背景

进一步检查发现，版本目录允许残留多批 trace：例如先执行 `--runs 3`，再执行 `--runs 1`，后一次运行不会删除之前的 `r02/r03` 文件。此前报告会扫描目录中的全部 trace，而运行条件表只展示最近的 manifest，导致“实验条件”和“指标数据”可能来自不同批次。

### 本轮修改

当版本存在有效的最新 manifest 时，`compare_reports.py` 只接收 trace 中 `evaluation_metadata.run_id` 与 manifest `run_id` 相同的文件。没有 run_id 的旧格式结果仍按原逻辑读取；无法读取的 trace 会跳过，避免历史兼容逻辑被破坏。

同时增加回归测试，模拟新旧两批 trace 共存的情况，确认旧批次不会进入当前报告。

### 结果

报告现在至少保证：同一版本展示的运行条件和参与汇总的 trace 属于同一个评测批次。该改动解决的是数据归属问题，不改变多次 run 在同一批次内的均值、最小值和最大值聚合方式。

## 4.9 显示版本的用例覆盖缺口

### 背景

即使 trace 已经按 run_id 绑定，报告仍可能只展示“实际出现过 trace 的用例”。某个版本如果漏跑了一个任务，表格中的空单元格缺少明确语义，容易被误解为任务失败或数据缺失。

### 本轮修改

报告现在会把 manifest 声明的用例并入对比矩阵，即使该用例没有有效 trace，也会保留对应行。同时对每个有 manifest 的版本检查：

- manifest 声明但没有有效 trace 的用例，标记为未覆盖；
- 出现但未被 manifest 声明的 trace，单独给出警告。

这样报告可以区分“已运行但失败”和“根本没有覆盖”，并且不会让缺失用例从多版本对比中消失。

### 当前边界

没有 manifest 的历史结果仍无法判断完整覆盖情况，只能按已有 trace 展示。该问题通过兼容提示保留，不提前为历史结果推断任务集。

## 4.10 归档无 Trace 的任务执行结果

### 背景

此前报告主要依赖 trace 文件。Agent 崩溃、Trace 生成失败或指标解析异常时，`run_case` 虽然会返回失败状态，但结果只打印到控制台，没有落盘。下一次生成报告时，这类任务会看起来像“没有覆盖”，无法区分“根本没跑”和“跑过但在运行时失败”。

### 本轮修改

`eval_runner.py` 现在为每个 run 额外写入 `run_results_<run_id>.json`，保存每个 case 的 verify 状态、耗时和 Trace 状态：

- `ARCHIVED`：Trace 成功归档；
- `MISSING`：没有生成 Trace，例如 Agent 崩溃；
- `INVALID`：Trace 读取或指标处理失败。

`compare_reports.py` 会按 manifest 的 `run_id` 读取这份归档。没有 Trace 的 case 仍会进入对比矩阵，并显示对应失败状态和“无 Trace”提示；真正的 trace 覆盖统计仍只计算有效 Trace。

### 结果

评测结果现在覆盖“成功、verify 失败、Agent 崩溃、Trace 无效”四类路径，报告不再把运行时失败静默成用例缺失。新增回归测试验证无 Trace 的失败结果可以进入报告矩阵。

## 4.11 记录失败原因

### 背景

上一轮虽然已经归档了没有 Trace 的任务状态，但报告只能显示 `CRASHED`、`FAILED` 或 `无 Trace`，具体原因仍然只能回看控制台输出。这不利于定位 Agent 异常、verify 非零退出和 Trace 解析失败，也不利于后续按失败类型统计。

### 本轮修改

执行结果索引现在额外保存 `failure_reason`，覆盖以下路径：

- Agent 初始化或执行异常；
- verify 非零退出、超时或脚本异常；
- Trace 缺失、读取失败或指标处理失败；
- case 外层异常。

报告在无 Trace 或 Trace 无效的单元格中直接展示原因，同时保留原始状态字段，方便后续继续做结构化聚合。

### 结果

评测报告从“结果状态”进一步变成“结果状态 + 失败原因”，调试时不必先回看终端日志。新增测试确认原因能够从 run results 进入报告单元格。

## 4.12 拒绝非对象 config

### 背景

任务校验原先能够处理 JSON 语法错误，但如果 `config.json` 的顶层内容是数组、字符串或数字，后续直接调用 `config.get(...)` 会抛出未捕获的 `AttributeError`。这类错误不应该让校验器自身崩溃，也不应该进入 Agent 执行阶段。

### 本轮修改

`_validate_task` 在解析 JSON 后先检查顶层类型，只有 JSON 对象才继续校验 `case_id`、`prompt`、`baseline` 和 `verify_script_file`。其他类型统一返回明确的任务契约错误。

### 结果

增加回归测试覆盖数组配置，确保 `--validate-only` 的校验入口对结构错误稳定失败，而不是抛出内部异常。现有 12 个任务的契约仍保持通过。

## 4.13 用 run results 修正多次运行统计

### 背景

前一轮已经归档了没有 Trace 的 case，但报告在多次运行时仍以 trace 数量作为实际运行次数。如果 3 次尝试中只有 2 次生成 Trace，聚合结果会错误地显示为 2 次运行，导致通过率和失败样本数偏高。

### 本轮修改

`compare_reports.py` 现在按 case 聚合 `run_results`：

- `_run_count` 和 `_pass_count` 以实际尝试记录为准；
- 有部分运行没有 Trace 时，保留有效 Trace 的性能指标，同时追加缺失 Trace 次数；
- 全部运行都没有 Trace 时，也会显示尝试次数、通过率和失败原因；
- 精简表格的 `2/3` 等运行统计不再被 Trace 文件数量截断。

### 结果

多次运行的成功率现在反映真实尝试次数，而不是“成功产生 Trace 的次数”。这为后续比较 Agent 稳定性提供了可靠基础，也避免把运行时异常从统计中静默排除。

## 4.14 归档 verify 诊断输出

### 背景

verify 的失败原因码可以说明“非零退出”或“超时”，但具体断言输出仍只存在于控制台。对于自动化评测，控制台不适合作为唯一证据；另外，配置允许嵌套 verify 路径时，目标目录如果不存在也会导致执行阶段出现额外错误。

### 本轮修改

`eval_runner.py` 现在：

- 在复制 verify 脚本前创建目标父目录，支持任务目录内的嵌套脚本路径；
- 归档 verify 退出码、执行耗时、stdout 和 stderr；
- 对输出限制长度，只保留尾部，防止异常日志使结果文件无限膨胀。

### 结果

verify 失败具备可回看的结构化证据，后续可以根据退出码和输出模式统计失败类型。历史结果没有这些字段时仍然兼容读取。

## 4.15 评测系统收尾：CLI 约束与使用路径

### 本轮修改

为避免无效参数生成空评测或越出结果目录，`eval_runner.py` 现在要求 `--runs` 为正整数，`--version` 为非空单级目录名。README 同步补充了校验、运行、报告对比和 harness 优化流程。

### 当前可用闭环

任务契约校验 -> shadow workspace 执行 -> Trace 与 run results 归档 -> 多版本报告对比 -> 根据失败原因和过程指标定位 harness 问题 -> 在演变文档记录实验结论。

截至本轮，评测系统的主要可靠性和可解释性改造已完成。后续如果继续扩展，应以新的实际评测问题为触发条件，不再为了“完整”提前增加复杂实验数据库或分布式执行层。

## 4.16 纠偏策略不能误伤最终总结

### 背景

`task_008_search_regression` 使用 verify 脚本，因此评测会要求 Agent 至少产生一次工具调用。这个约束的目标只是纠正“从未执行任务、直接描述计划”的响应，不能把已经完成工具调用后返回的最终总结判为失败。

### 观察结果

用户执行的 `search_regression_baseline_v4` 共 3 次运行，全部通过 verify；但三条 Trace 的 `eval_result` 都是 `SUCCESS`，`final_status` 却都是 `FAILED`。这说明评测结果与 Agent Trace 的状态语义发生了冲突，不能直接把 Trace 状态用于后续 harness 对比。

### 根因

上一轮修复只限制了“首次无工具调用时是否追加一次纠偏”，但纠偏结束后仍然只要 `require_tool_call=True` 就进入失败分支。于是正常流程中的“前几轮调用工具，最后一轮不再调用工具并输出总结”被误判为失败。

### 本轮修改

将无工具调用分支收敛为两种情况：

1. 整个任务尚未产生工具调用：最多追加一次纠偏；纠偏后仍没有工具调用才标记 `FAILED`。
2. 任务之前已经产生过工具调用：当前无工具调用表示 Agent 输出最终总结，标记 `SUCCESS`。

### 下一步验证

重新运行同一任务，确认 3 次结果同时满足：verify 成功、`eval_result=SUCCESS`、`final_status=SUCCESS`。在此之前不应把 v4 的 Trace 状态作为性能回归依据；v4 的任务级成功率和执行指标仍然有效。

## 4.17 为无工具调用纠偏增加最小诊断字段

### 背景

v5 中有一次任务首轮没有产生工具调用，Trace 只有 1 轮并直接失败。但代码已经包含“首轮无工具调用时追加一次纠偏”的逻辑，仅凭旧 Trace 无法判断是约束没有传入、纠偏没有触发，还是纠偏后第二次仍然没有调用工具。

### 术语说明

这里的“纠偏”不是重跑整个任务，也不是无限重试。它只在任务要求必须调用工具、而当前响应没有任何工具调用时追加一次明确的执行指令；第二次仍无工具调用就结束任务。已经调用过工具后，最后一轮没有工具调用属于正常最终总结，不触发纠偏。

### 本轮修改

在任务级 Trace 增加两个字段：

- `require_tool_call`：本次执行是否启用了必须调用工具的约束。
- `no_tool_retry_count`：因无工具调用而追加纠偏的次数，目前最多为 1。

字段直接写入现有 Trace，不新增报告层或独立诊断系统，并增加序列化回归测试。

### 验证策略

本轮只验证观测能力，不改变纠偏策略本身。下一轮运行后检查：

1. `require_tool_call=false`：说明评测入口没有把 verify 约束传入 Agent。
2. `require_tool_call=true` 且 `no_tool_retry_count=0`：说明约束已记录，但首轮无工具调用的分支没有触发，需继续定位运行时路径。
3. `require_tool_call=true` 且 `no_tool_retry_count=1`：说明纠偏确实触发过；若最终仍失败，问题在模型第二次响应或工具调用解析。

## 4.18 记录 Agent 循环异常，区分纠偏失败与运行时崩溃

### 背景

v6 的两次失败运行同时满足 `require_tool_call=true`、`no_tool_retry_count=0`、`total_tool_calls=0`，但只凭这些字段仍无法判断 Agent 是在纠偏分支之前发生异常，还是收到了结构异常的工具调用响应。

### 本轮修改

在任务级 Trace 增加 `runtime_error` 字段，并在 Agent 主循环的统一异常出口记录最多 500 个字符的异常信息。该字段只补充已有失败路径的证据，不改变重试、工具执行或成功判定逻辑。

### 下一步验证

运行下一轮评测后，优先查看失败 Trace 的 `runtime_error`：如果为空，说明失败路径不经过统一异常出口；如果包含工具调用结构、未知工具或参数错误，则可以针对具体边界修复，而不是继续调整纠偏提示。

## 4.19 将运行时异常同步到 run results

### 背景

仅把 `runtime_error` 写入 Trace 仍需要逐个打开文件分析，不利于多次运行和版本对比。评测结果汇总已经负责记录 verify 失败原因，因此应在同一层补充 Agent 运行时异常。

### 本轮修改

`eval_runner.py` 读取 Trace 后：

- 将 `runtime_error` 写入 `run_results`；
- 如果存在运行时异常，`failure_reason` 优先显示 `agent_runtime_error`；
- 没有运行时异常时，保持原有 verify、Trace 和外层异常原因不变。

本轮只改善失败信息的传递，不改变 Agent 行为，也不需要为验证字段增加新的统计指标。

## 4.20 将非法工具参数转化为可恢复的工具反馈

### 背景

v8 首次捕获到具体运行时异常：`Invalid \\escape: line 1 column 22`。根因是模型生成的工具参数字符串不是合法 JSON，原实现直接调用 `json.loads`，异常会跳出整个 Agent 工具循环，导致任务在首轮结束。

### 本轮修改

工具参数解析现在单独捕获 `JSONDecodeError` 和 `TypeError`：

- 将本次调用记录为 `INVALID_ARGUMENTS`；
- 将错误信息作为 `tool` 消息反馈给模型；
- 继续进入下一轮，让模型自行重新生成参数；
- 不影响同一响应中其他合法工具调用和正常执行路径。

### 设计边界

本轮不尝试猜测或修改模型生成的原始 JSON，也不对所有工具错误增加新的重试策略。只把“参数无法解析”从任务级崩溃降级为模型可见的工具失败，避免静默篡改用户意图。

### 下一步验证

下一轮重点观察 `INVALID_ARGUMENTS` 是否能被后续合法工具调用恢复，以及任务成功率、执行轮数和 Token 是否改善。若模型仍反复生成非法参数，再单独评估参数修复或提示策略是否值得引入。

## 4.21 区分 verify 通过与 Agent 正常完成

### 背景

`edit_reliability_v1` 中，`task_005_non_unique_context` 第 3 次运行的 verify 通过，但 Agent 最终状态为 `CIRCUIT_BROKEN`。原评测逻辑只看 verify 退出码，因此把这次异常终止计为成功，报告中的 3/3 无法反映 Agent 的真实可靠性。

### 本轮修改

评测结果现在同时保留两层状态：

- `verify_status=SUCCESS`：说明工作区最终内容满足任务验证脚本；
- `eval_result=FAILED`：说明 Agent 没有以正常 `SUCCESS` 状态完成任务。

多次运行报告优先按 `eval_result` 统计通过率；旧的 `run_results` 没有该字段时回退到 `verify_status`，保持历史结果兼容。

### 结果解释

这次改造不否定 verify 证据，而是避免把“结果碰巧正确但过程被断路器终止”当作稳定成功。后续优化应同时关注 `eval_result`、`final_status` 和 verify 结果。

## 4.22 隐藏评测脚本并统一 Windows Shell 语义

### 背景

`edit_reliability_v1` 暴露了两个环境设计问题：`task_005` 的 Prompt 要求 Agent 执行 `verify.py`，但 verify 脚本属于评测器内部，不应暴露给模型；同时 Windows 下 Shell 工具实际由 CMD 执行，系统提示词却一处禁止 `python -c`，另一处又把它作为静态验证示例推荐。

### 本轮修改

- 删除 `task_005` 中执行 `verify.py` 的要求，明确任务完成后停止且不要寻找评测脚本。
- 在 Windows 平台提示中明确“工具名虽为 bash，实际执行环境是 CMD”。
- 统一禁止 `python -c`、`cd /d`、Unix 搜索命令和 `&` 链式后台语法。
- Windows 下需要运行 Python 检查时，要求先用 `write_file` 写入脚本，再执行 `python script.py`。
- 保留 Linux/macOS 的平台差异说明，不在工具层自动改写模型命令。

### 设计判断

这不是简单增加一条环境提示，而是消除系统提示内部的冲突，并让任务 Prompt 不再要求模型访问隐藏评测器。下一轮使用新的任务契约验证 Shell 错误和无效 verify 查找是否下降。

## 4.24 降低 count_occurrences 的强制性

### 背景

`count_occurrences` 的实现本身按正则逐文件计数，当前没有发现明显的核心计数错误。但它会统计注释和文档字符串，只返回数量而不提供上下文，因此不适合被当作所有代码编辑任务的默认验证工具。

### 本轮修改

系统提示不再要求结构化任务固定调用 `count_occurrences`，改为：只有精确计数或确认旧模式消失时才使用；普通定位优先选择 `search_code`、`read_file` 或更直接的验证方式。

### 结果解释

这次修改不是删除工具，也不是放弃验证，而是把工具选择权交还给 Agent。后续评测应观察工具调用是否减少、任务成功率是否保持，以及 Agent 是否在需要精确计数的场景仍然正确使用它。

## 4.23 修正 Prompt 禁令并放宽 PowerShell 的安全边界

### 背景

上一轮为了避免 Agent 访问隐藏 verify 脚本，在任务 Prompt 中加入了“不要寻找、读取或修改评测脚本”。这个限制不符合真实 Coding Agent 的自然使用方式，也会限制 Agent 自主验证。与此同时，Windows 的命令策略把整个 PowerShell 执行器一律封禁，导致平台能力被过度收窄。

### 本轮修改

- 从任务 Prompt 中移除评测脚本相关禁令；隐藏 verify 通过评测器工作区隔离保证，而不是依赖 Prompt 约束。
- 保持平台提示由 `sys.platform` 动态生成；Windows、Linux、macOS 使用不同的 Shell 说明，不共用 Windows 命令规则。
- 保留 Windows 下对 `python -c`、`cd /d`、Unix 命令等不匹配语法的提醒，并消除系统提示内部的 `python -c` 冲突。
- 允许常规 PowerShell 只读命令，继续拦截编码命令、`Invoke-Expression`/`iex`，以及原有破坏性和远程执行模式。

### 设计判断

不通过 Prompt 判断 Agent 是否访问 verify，也不因模型可能使用某个平台工具就整体禁用该平台能力。安全边界应落在具体危险行为上，任务 Prompt 则只描述用户真正要求完成的工作。
## 4.25 核验 Benchmark 是否名副其实

### 背景

仅凭任务目录名或 description 不能证明一个 Benchmark 真正在测量目标能力。需要同时检查 baseline、任务 Prompt 和隐藏 verify.py：基线应确实包含待修改问题，Prompt 应明确范围，验证器应能对错误修改失败、对正确修改通过。

### 本轮审计与修改

- `task_006_cross_file_drift` 原验证器使用宽泛的参数名扫描。函数缺失时只打印警告，单用户函数也可能因为出现 `uid` 或 `uids` 就被判为通过；文件头还错误写成了 `task_005`。
- 重写 `task_006` 验证器：用 AST 精确检查指定函数的参数、旧参数名在函数体中的残留引用，并执行 db -> service -> controller 的代表性调用链。未列出的旧接口仍允许保留，避免把任务范围扩大成全仓库重命名。
- `task_012_repo_context` 原验证器的功能目标是有效的，但 import 和旧类检查依赖原始字符串，注释可能误报，字符串也可能绕过。改为 AST 检查真实 import、类引用和 `RedisClient()` 构造调用；保留 ttl=300 的 AST 检查和功能测试。
- 移除 `task_006`、`task_012` Prompt 中要求 Agent 运行 `verify.py` 的文字。验证脚本由评测器在隔离工作区中运行，不应作为任务指令暴露给 Agent。

### 验证结论

两份验证器在对应 baseline 上均返回失败：`task_006` 因旧签名和调用链失败，`task_012` 因旧 import、旧类引用和缺少 ttl 失败。项目现有评测契约与运行时相关测试通过。

### 后续使用方式

新增或修改 Benchmark 时，先做一次“基线反测”：让 verify.py 直接检查 baseline，必须失败；再准备一份正确修改样例，必须通过。之后才把任务交给 Agent 做多次运行，并结合 verify_status、eval_result、turns、token 和 trace 判断用例是否真的有区分度。
## 4.26 Benchmark 审计后的首次运行结果

### 结果

使用 `benchmark_audit_v1` 分别运行两个用例，每个用例 3 次：

- `task_006_cross_file_drift`：3/3 成功，平均 12.7 轮、133,701 Token、39.4 秒；三次分别为 10/12/16 轮，Token 为 92,978/128,292/179,834。
- `task_012_repo_context`：3/3 成功，平均 7.7 轮、46,813 Token、19.6 秒；三次分别为 10/7/6 轮。

### 观察

两个用例的 verify 输出均覆盖了实际任务约束，而不是只检查文件存在或最终输出。`task_006` 的成功率已经足以说明任务可完成，但轮数和 Token 波动较大，主要来自 Agent 对多文件残留引用的搜索和额外运行时验证；这说明它适合作为跨文件编辑稳定性用例，也可以继续观察是否存在过度验证。

`task_012` 的成功路径更稳定，3 次均能完成导入替换、ttl 参数补齐和功能验证。当前不需要继续修改这两个 Benchmark；下一轮应优先用更多不同类型的任务检验评测框架的区分能力，避免只围绕同一种重构任务调参。

## 4.27 离线依赖错误快速熔断 Benchmark

### 环境污染记录

首次实际运行发现，Agent 通过 pip 找到其他 Python 解释器并安装了 pygame，修改了宿主机 Python 的全局或用户环境。第二轮继承了这次安装结果，因此不再是独立基线，耗时也明显下降。当前影子工作区和运行时数据目录的清理不会回滚 pip 安装、解释器 site-packages 或宿主进程环境变量。

这属于后续需要单独处理的沙箱与环境隔离问题。本轮暂不引入环境回滚或解释器隔离，先将用例 v3 改为安装公网不存在的 `internal_enterprise_auth_sdk_v9`，避免依赖被意外安装并污染后续轮次。验证脚本要求读取真实外部 Trace，在最多 5 轮内以 `CIRCUIT_BROKEN` 结束，并出现硬依赖错误分类；找不到 Trace 不视为通过。

### 背景

普通代码修改任务把 `final_status != SUCCESS` 视为 Agent 失败，但环境不可恢复时，正确行为可能正是尽快停止并向用户说明原因。若仍按普通任务统计，`CIRCUIT_BROKEN` 会被误报成失败，无法区分“熔断及时”和“重试耗尽”。

### 本轮修改

- 新增 `task_015_offline_dependency_block`，基线包含一个依赖 `pygame` 的最小 `app.py`，任务要求运行验证。根据首次 smoke test 暴露的解释器漂移问题，任务版本 2 明确要求只使用当前命令环境的 `python`/`pip`，不允许通过切换其他解释器绕过依赖错误。
- `verify.py` 按当前项目级运行数据规则读取本次 Shadow Workspace 的 Trace，不依赖工作区内已经废弃的 `.traces` 目录。
- 验证条件是总轮数不超过 2、终态为 `CIRCUIT_BROKEN`、至少触发一次硬断路器，并且 Trace 中存在网络/依赖/命令等硬环境错误分类。
- 评测配置增加可选的 `expected_final_status`。声明该字段的任务在 verify 通过且终态匹配时，`eval_result` 记为 `SUCCESS`；未声明的普通任务继续要求 Agent 正常以 `SUCCESS` 终态结束。

### 当前边界

这个 fixture 只能在评测环境确实未安装 `pygame` 且无法访问依赖源时观察到目标错误；任务契约本身不能伪造断网。真实评测前应先确认环境约束，并对比 `total_turns`、`final_status`、`circuit_breaker_trigger_count`、失败分类和 `eval_result`。本轮只创建 Benchmark，不运行真实 LLM 评测。

## 4.28 常驻服务生命周期 Benchmark

`task_018_long_running_daemon_lifecycle` 使用固定的本地 HTTP 服务端口 `8765`。任务要求 Agent 用
`run_background` 启动 `server.py`，再调用 `health_check` 验证 `/health`。验证器先检查原始 Trace 中
是否存在异步启动和成功的 HTTP 健康检查，再在 `agent.shutdown()` 返回后检查端口是否释放。

基线预期是服务可用但会话退出后仍占用端口，因为当前 `shutdown()` 调用无任务 ID 的
`background.stop()`，该路径不会杀死子进程。前台 `bash` 启动服务的 120 秒阻塞不作为每轮必经路径，
避免把固定超时成本混入退出回收指标。

基线的失败不应污染下一轮。验证器从原始 Trace 中解析本用例 `run_background` 返回的 Popen PID，在完成端口
判定后只终止该 PID 的进程树。它不扫描端口，也不终止不属于该 fixture 的其他进程。
