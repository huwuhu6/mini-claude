# mini-claude 项目交接文档

这份文档给下一位协作者使用。目标是让协作者不需要重新翻完整段对话，就能理解项目、当前进度和负责人的工作要求。

## 一、项目是什么

这是一个用 Python 编写的本地 Coding Agent runtime，早期原型在 `s_full.py`，当前主实现位于 `src/`。Agent 可以在经过确认的 workspace 中：

- 读取、搜索、写入和编辑代码；
- 执行受策略约束的 shell 命令；
- 通过多轮 LLM 调用完成任务；
- 使用 LoopGuard、Failure Intelligence 等机制处理重复操作和失败恢复；
- 通过 Trace 和 Benchmark 观察执行质量；
- 保存会话 JSONL，回放 Agent 的实际执行流程。

项目重点是 Agent runtime 的可靠性、工具调用、上下文管理、评测和可观测性。它不是生产级 Claude Code 替代品。

## 二、当前主线

当前更应该关注以下模块：

1. `src/agent/mini_claude_agent.py`：主 Agent 循环、工具调用、失败处理和会话事件记录。
2. `src/core/runtime_context/`：workspace、路径解析、Shell Session 和环境状态。
3. `src/core/tools/`：文件工具和 shell 工具。
4. `src/core/tracing/`：任务级 Trace。
5. `src/core/failure_intelligence/`：失败分类、策略指纹和恢复判断。
6. `src/core/evaluation/`、`eval_runner.py`：评测指标、任务运行和结果归档。
7. `src/core/session_recorder.py`、`src/core/debug_viewer.py`：会话 JSONL 和调试入口。
8. `src/providers/`：DeepSeek/OpenAI 风格和 Anthropic 风格的消息转换。

`s_full.py`、`minimal_agent.py` 和其他历史实现可以用来了解演化过程，但修改前要先确认它们是否属于当前主链路。

## 三、当前已经完成的能力

- workspace 确认和权限边界；
- 持久化 Shell Session，减少重复 `cd` 和工作目录漂移；
- 工具调用循环和工具结果回传；
- 同轮工具去重、LoopGuard、Failure Intelligence 和熔断；
- 原子化文件编辑、模糊定位和批量编辑事务；
- Token 阈值压缩、Stub 和工具链清洗；
- Benchmark 的 baseline/config/verify 任务结构、Shadow Workspace、Trace 和报告比较；
- JSONL 会话记录，支持 `round_id`、`step`、`turn` 和 `call_id`；
- `mini-claude --debug latest|errors|flow` 调试入口；
- 运行时数据移出 workspace，默认放在 `D:\02_study\code\mini-claude-project-data`。

## 四、会话记录的当前规则

一份 JSONL 文件对应一次 CLI 会话。事件层级是：

```text
session_id
  -> round_id：一次用户输入到 Agent 回复结束
      -> step：该来回中的事件顺序
          -> thinking.turn：该来回中的模型调用轮次
```

`tool_call` 和 `tool_result` 使用同一个 `call_id`。发送给 Provider 时，OpenAI/DeepSeek 使用 `tool_call_id`，Anthropic 使用 `tool_use_id`。

完整背景和字段解释见 `docs/evolution/session_trace_evolution.md`。

## 五、负责人的偏好和明确要求

以下要求来自长期协作约定，后续协作者应默认遵守：

- 使用中文沟通；提交信息使用中文，并注明 `feat`、`fix`、`refactor` 等类型。
- 负责人自己提交和推送。协作者不要擅自 `git commit` 或 `git push`，只提供建议提交信息。
- 负责人希望持续记录“观察问题 -> 尝试方案 -> 测试结果 -> 最终取舍”的演进过程。
- 演进文档放在 `docs/evolution/`，该目录不纳入 Git；每个方向尽量维护一份完整文档，不要因为每个小改动新建文件。
- 演进文档要用大白话解释背景、改了什么、为什么这么改、结果如何、还有什么问题，不能只写代码摘要。
- 不要过度设计。优先使用现有结构和最小改动，只有真实需求出现时再抽象。
- 用户特别关注 Agent 的真实行为、Trace、工具调用、失败原因、Token 浪费和评测指标，不要只做表面 UI 改动。
- 评测命令不要由协作者自动运行。应给负责人清楚的 PowerShell 命令，由负责人手动运行并反馈结果。
- 负责人反馈测试结果后，先分析 Trace 和 JSONL，再决定是否修改 harness；不要看到一次偶然结果就立刻重构。
- 修改前先检查现有代码和工作区状态，保留负责人已有的未提交改动。

## 六、常用操作

确认 CLI 使用的是当前源码：

```powershell
Get-Command mini-claude
D:\python3.12.1\python.exe -c "import agent.mini_claude_agent; print(agent.mini_claude_agent.__file__)"
```

启动：

```powershell
# 日常交互：当前目录作为 workspace，并进行确认
mini-claude

# 已确认目录安全或用于自动化时：跳过确认
mini-claude . --yes
```

安装依赖时，命令中的 `requirements.txt` 是相对当前终端目录解析的。如果终端不在项目根目录，先执行：

```powershell
Set-Location D:\02_study\code\AgentProject\mini-claude
D:\python3.12.1\python.exe -m pip install -r requirements.txt
```

也可以从任意目录使用绝对路径：

```powershell
D:\python3.12.1\python.exe -m pip install -r D:\02_study\code\AgentProject\mini-claude\requirements.txt
```

查看会话：

```powershell
mini-claude . --debug latest
mini-claude . --debug errors
mini-claude . --debug flow --session <session_id>
```

运行核心测试：

```powershell
D:\python3.12.1\python.exe -m pytest tests/integration/test_eval_contract.py tests/integration/test_runtime_context.py tests/integration/test_failure_intelligence.py -q
```

运行评测时，负责人手动执行 `eval_runner.py`，并把命令输出、`run_results`、相关 Trace 和会话 JSONL 反馈回来。

## 七、当前未完成和风险

- Provider 目前还不是完整的 Token 流式输出，控制台显示的是可验证的思考轮次和工具进度。
- 多工具调用已经有 `call_id`，但工具执行本身仍是串行的，尚未实现并发调度。
- 旧 workspace 中的 `.tasks`、`.team`、`.traces`、`.transcripts` 和 `logs/` 尚未自动删除，避免误伤历史数据；新运行不会继续写入它们。
- 完整对话状态主要存在内存中，进程异常退出后不能自动恢复上下文。
- Windows 测试环境可能出现 `.pytest_cache` 无权限警告；如果核心测试通过，不要把这个警告误判成业务失败。
- `tiktoken` 首次加载可能需要编码资源，受限网络环境下测试收集可能失败。
- 前台服务命令被 `Ctrl+C` 中断后会记录为 `CANCELLED`，而不是普通工具失败。`run_background` 用于 Redis、Spring Boot 等持续运行的服务，Agent 退出不会自动停止这些外部进程。
- 后台任务提供 `get_background_status`、`get_background_logs`、`stop_background` 和 `health_check`；任务状态描述进程本身，端口/HTTP 检查描述服务是否可用。
- 如果模型仍然把 Windows `start ...` 当作普通 `bash` 命令调用，Harness 会在入口处自动把它转交给后台执行器，避免新窗口已经打开但原工具调用仍然阻塞。

## 八、推荐的协作流程

1. 先读本文件、相关 `docs/evolution/` 文档和目标模块。
2. 检查 `git status`，区分负责人已有改动和本轮修改。
3. 明确本轮只解决一个可验证的问题，避免顺手重构无关代码。
4. 修改后运行编译和与改动直接相关的测试。
5. 如果需要 Benchmark，给负责人 PowerShell 命令，不代替负责人运行。
6. 根据反馈补充 Trace、JSONL 和演进文档。
7. 最终说明改了什么、怎么验证、还有什么风险，并给出中文提交信息。

## 九、常用工作流教学

### 9.1 评测工具在哪里

评测相关代码主要在以下位置：

```text
eval_runner.py                  评测入口，负责准备环境、运行 Agent、验证结果、归档 Trace
compare_reports.py              比较不同版本或不同实验结果
src/core/evaluation/            Trace 指标计算和结果分析
sandbox/tasks/                  评测用例
sandbox/eval_results/           评测结果归档
docs/evolution/evaluation_evolution.md
                                评测框架的演进记录和指标解释
```

一个评测任务通常是：

```text
sandbox/tasks/task_xxx/
├── baseline/       Agent 开始前的初始项目文件
├── config.json     prompt、任务 ID、验证脚本配置
└── verify.py       Agent 结束后由评测器独立执行的验证脚本
```

`verify.py` 不应该暴露给 Agent，也不应该让 Agent 修改。评测器会先把 `baseline/` 复制到临时 Shadow Workspace，Agent 只操作 Shadow Workspace；Agent 结束后，评测器再复制并执行 `verify.py`。

### 9.2 评测运行原理

一次 case 的执行顺序是：

```text
读取任务配置
  -> 校验任务契约
  -> 清空并创建 Shadow Workspace
  -> 复制 baseline
  -> 启动当前代码版本的 Agent
  -> Agent 修改 Shadow Workspace 并生成 Trace
  -> 复制 verify.py
  -> 在 Shadow Workspace 执行 verify.py
  -> 计算轮数、Token、工具调用、耗时等指标
  -> 归档 Trace 和结果
  -> 清理 Shadow Workspace 及临时运行时数据
```

这里要区分两类结果：

- `verify_status`：任务最后是否通过独立验证，是结果正确性的主要依据。
- Trace 指标：Agent 是怎么完成任务的，包括轮数、Token、工具调用次数、失败次数和循环防护等，用于解释效率和行为。

Agent 最后输出“看起来正确”不能替代 `verify.py`；工具调用少也不一定代表更好，必须先确认成功率没有下降。

### 9.3 运行评测前先做什么

先检查任务契约，不启动 LLM：

```powershell
py eval_runner.py --validate-only
```

如果 PowerShell 使用多行命令，续行符是反引号，不是 Linux 的反斜杠：

```powershell
py eval_runner.py `
  --version local_experiment `
  --task task_001 `
  --runs 3
```

也可以写成单行，最不容易出错：

```powershell
py eval_runner.py --version local_experiment --task task_001 --runs 3
```

其中：

- `--version` 是本次实验的标签，不会自动切换 Git 版本；
- `--task` 指定一个任务，不写时运行任务集；
- `--runs 3` 表示同一条件重复运行三次，用于观察随机性；
- 如果要比较代码版本，应该先切换或准备对应代码，再分别运行不同标签。

评测命令由负责人手动执行。协作者不要擅自替负责人调用真实 LLM 和消耗 API 配额。

### 9.4 评测结果在哪里

一次评测通常会在下面生成文件：

```text
sandbox/eval_results/<version>/
├── run_manifest_<run_id>.json
├── run_results_<run_id>.json
├── trace_<task>.json
└── trace_<task>_r02.json
```

先看 `run_results`，它适合回答：

- case 是否成功；
- verify 是否执行；
- 失败原因是什么；
- Agent 耗时多久；
- Trace 是否存在。

再看对应 Trace，才适合回答：

- Agent 执行了多少轮；
- 哪些工具被调用；
- 失败后是否改变策略；
- 是否触发 LoopGuard、Failure Intelligence 或熔断；
- Token 和上下文压缩是否异常。

### 9.5 如何分析一次失败

建议按下面顺序，不要一上来就修改代码：

1. 先确认 `verify_status` 是失败、跳过还是验证脚本异常。
2. 看 `failure_reason`，区分 Agent 没完成、验证脚本问题、Trace 缺失和环境错误。
3. 看 Trace 的任务状态、轮数、工具调用次数和最终错误。
4. 找出第一次真正失败的工具调用，而不是只看最后一轮。
5. 判断 Agent 后续行为属于哪类问题：重复调用、错误归因错误、验证过度、工具参数错误、上下文丢失、平台命令不兼容或编辑定位失败。
6. 只有确认问题稳定存在后，才修改对应 harness 模块。

交互式运行产生的问题还可以查看会话 JSONL：

```powershell
mini-claude path/to/project --debug latest
mini-claude path/to/project --debug errors
mini-claude path/to/project --debug flow --session <session_id>
```

Trace 适合做 Benchmark 的统计，JSONL 适合还原一次真实对话的先后流程。两者不能互相替代。

### 9.6 如何用评测驱动 harness 优化

推荐采用“一次只改变一个主要因素”的闭环：

```text
建立基线
  -> 选定一个真实失败样本
  -> 读取 Trace 和 JSONL，提出具体假设
  -> 修改一个 harness 方向
  -> 运行同一任务、同一 runs 次数
  -> 比较成功率、轮数、Token、耗时和失败类型
  -> 检查是否引入其他任务回归
  -> 记录结论，再决定下一轮
```

例如发现 Agent 在 PowerShell 环境中反复使用 Linux 命令，应该先验证：

- 是系统没有告诉 Agent 当前 shell；
- 还是工具实际执行环境和提示词描述不一致；
- 还是命令失败后的错误信息不足；
- 还是 Agent 没有根据失败切换策略。

确认原因后再决定修改系统提示词、Shell 工具反馈、Failure Intelligence 或平台命令适配。不要仅仅因为某一次多用了几轮就直接修改 LoopGuard。

### 9.7 如何写一次演进记录

每个重要方向维护一份文档，按下面结构写：

```text
背景：实际观察到了什么问题
原始行为：修改前 Agent 怎么做、为什么不好
假设：认为问题根因是什么
尝试：改了哪个模块，采用什么策略
结果：成功率、轮数、Token、耗时和行为变化
副作用：是否影响其他任务或降低灵活性
最终取舍：为什么保留或放弃这个方案
下一步：还需要验证什么
```

小的拼写、类型或测试修复不需要单独写演进文档；只有改变 Agent 行为、评测口径或运行时结构时才记录。

### 9.8 如何输入多行问题

REPL 使用 `prompt-toolkit` 提供可编辑的多行输入框：

```text
你 > 第一行
... > 第二行
... > 第三行
```

按 Enter 换行，按 `Alt+Enter` 提交整段内容；如果终端不识别 `Alt+Enter`，可以按 `Esc` 后再按 Enter。粘贴多行文本后，内容会留在同一个输入框中，可以继续移动光标、修改和使用历史记录，不会因为第一处换行而提前调用 Agent。

安装依赖：

```powershell
py -m pip install -r requirements.txt
```

如果因为环境限制没有安装 `prompt-toolkit`，CLI 会退回兼容模式：输入 `/paste`，粘贴内容，最后单独输入 `/end` 提交。

### 9.9 Ctrl+C 的两种行为

现在要区分“正在输入”和“Agent 正在工作”：

- 正在输入时按 `Ctrl+C`：CLI 会询问是否退出，只有输入 `y` 或 `yes` 才退出；直接回车、输入其他内容或再次按 `Ctrl+C` 都会回到输入提示。
- Agent 工作时按 `Ctrl+C`：只取消当前任务，不退出会话。Shell 命令、模型请求或其他工具的取消会记录 `runtime_cancelled`，Trace 状态为 `CANCELLED`。

明确输入 `exit` 或 `quit` 仍然直接退出，因为这不是误触式的中断。
