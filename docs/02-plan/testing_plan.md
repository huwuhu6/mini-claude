# Testing Plan

> 本文记录 mini-claude 的测试与评测策略。项目是面试导向，因此测试不只是保证代码正确，也是展示 AI 应用开发能力的重要材料。

## 1. 测试目标

mini-claude 的核心风险不是普通函数错误，而是 Agent runtime 的副作用、状态漂移、失败恢复和评测可信度。因此测试目标分为五层：单模块正确性、工具安全性、Agent loop 可靠性、Trace 与 Eval 可信度、面试 demo 稳定性。

## 2. 当前测试现状

根据当前 repo 审计，已有测试包括：

| 测试文件 | 数量 | 覆盖内容 |
| --- | --- | --- |
| `tests/integration/test_all_modules.py` | 13 | config、tools、features、tasks、message bus、teammate、background、subagent、console、compression、providers、agent |
| `tests/integration/test_failure_intelligence.py` | 28 | 失败分类、策略指纹、升级策略、记忆、ToolTrace 字段、消息生成、pygame 回归 |
| `tests/integration/test_runtime_context.py` | 27 | PathResolver、CommandPolicy、ShellSession、RuntimeContext、Snake Game 回归、Trace 字段 |

总计约 68 个集成测试。当前测试基础较好，但高价值入口与安全边界仍有明显缺口。测试当前使用 `sys.path.insert(0, .../src)`，未完全验证安装包路径。

## 3. P0 测试任务

### 3.1 `ShellSession.reset()` 回归测试

目的：验证 shell session 的状态不会在任务之间泄漏。

测试用例：初始化 session，cwd 为 workspace root；`cd subdir`；调用 `reset()`；断言 cwd 回到 workspace root；reset 后执行 `pwd` 或等价命令，确认执行目录正确；多次 reset，确认幂等。

预期结果：当前 bug 修复前测试应失败，修复后测试通过。

面试价值：这是一个典型 runtime state bug，可以讲测试如何防止状态泄漏。

### 3.2 WorkspaceAuthority 单元/集成测试

目的：验证 workspace ownership boundary。

| 场景 | 预期 |
| --- | --- |
| workspace 内文件 | allow |
| workspace 子目录 | allow |
| `..` escape 到外部 | deny |
| 绝对路径指向 workspace 内 | allow |
| 绝对路径指向 workspace 外 | deny |
| additional root 内文件 | allow |
| additional root 外文件 | deny |
| symlink 指向 workspace 外 | 必须有明确策略，建议 deny 或 Needs verification |

预期结果：权限边界可被自动化测试证明，deny 时返回结构化错误。

### 3.3 BaseTools 与 Authority 委托链测试

目的：确认权限模型不是孤立类，而是真正接入文件工具。

覆盖工具：`read_file`、`write_file`、`edit_file`、`list_files`、bash cwd/path 行为。

测试用例：读取 workspace 内文件成功；读取 workspace 外文件失败；写入 workspace 内文件成功；写入 workspace 外文件失败；edit workspace 外文件失败；list workspace 外目录失败；bash 中 `cd ..` 后尝试操作外部路径，按策略处理。

预期结果：所有文件副作用都经过 authority，失败信息能被 Failure Intelligence 或 trace 使用。

### 3.4 CLI smoke tests

目的：验证项目推荐入口可用。

| 场景 | 预期 |
| --- | --- |
| `mini-claude . --yes` | 解析当前目录并跳过确认 |
| `mini-claude /abs/path --yes` | 绑定绝对路径 |
| `mini-claude relative/path --yes` | resolve 到绝对路径 |
| 无参数 | 行为明确：默认 cwd 或显示帮助 |
| 非 TTY 无 `--yes` | 安全退出或报错 |
| 用户输入 n | 退出，不启动 Agent |
| 用户输入 y | 启动 Agent |

建议：参数解析可以用 monkeypatch；console script 可用性用 subprocess 或 packaging smoke test；避免真实调用 LLM，使用 fake provider 或 mock。

## 4. P1 测试任务

### 4.1 Failure Intelligence 真实失败测试

目的：确认 Failure Intelligence 在真实工具失败中生效，而不是只在独立模块中通过。

| 场景 | 预期分类 |
| --- | --- |
| 不存在的命令 | COMMAND_NOT_FOUND 或等价分类 |
| 缺少 Python 包 | PACKAGE_NOT_FOUND 或 DEPENDENCY_ERROR |
| 网络不可达安装包 | NETWORK_UNREACHABLE |
| 权限拒绝 | PERMISSION_DENIED |
| 测试失败 | TEST_FAILURE 或等价分类 |
| command policy 拦截 | POLICY_BLOCKED 或 PERMISSION_DENIED |

需要验证字段：failure_category、recoverability、strategy_fingerprint、escalated、task_id 隔离 memory、escalation message。

如果当前分类枚举中没有这些精确名称，应以当前代码为准；本文名称是测试意图，不是强制 API。

### 4.2 Escalation 行为测试

目的：确认重复失败后系统不会无限 retry。

测试用例：同一 task 内连续 3 次同类失败且策略多样性低；触发 escalation；trace 中记录 escalation；LLM 如果继续重复，系统有明确行为。若当前未实现硬终止，标记 `Needs verification`。

### 4.3 Trace schema 测试

目的：确保 trace 可作为评测和调试事实来源。

测试内容：TaskTrace 包含 task_id、user_prompt、workspace_root、workspace_confirmed、total_turns 等字段；TurnTrace 包含轮次、模型交互摘要、token 信息或可用替代字段；ToolTrace 包含工具名、参数摘要、cwd、workspace_root、session_id、status、latency、failure 信息；JSON 可反序列化；历史 trace 兼容策略明确；敏感信息脱敏策略明确。

建议固化一份 `tests/fixtures/traces/current_schema_trace.json`，TraceAnalyzer 必须能读取该 fixture。

### 4.4 Eval runner smoke test

目的：确保评测系统可运行。

测试内容：删除或临时移动 `logs/`；运行 eval runner 的最小模式；断言自动创建 `logs/`；断言生成 CSV；断言 CSV 有 header 和至少一行结果。如果完整 eval 依赖 LLM，则增加 dry-run 或 fake provider 模式。

原则：不要让 CI 依赖真实 API key。使用 fake provider 或记录回放。

## 5. P2 测试任务

### 5.1 长文件工具测试

目的：验证 `read_file` offset/limit 和 `edit_file` 可恢复错误。

测试内容：创建超过 1000 行文件；读取中间 50 行；修改中间某段；old_text mismatch 时返回附近上下文；Agent 可据此恢复修改。

### 5.2 Provider abstraction 测试

目的：避免真实 LLM 依赖影响测试。

测试内容：FakeProvider 返回固定 tool call；ProviderManager 可切换 DeepSeek / Anthropic / FakeProvider；provider error 被转换为统一错误；API key 缺失时错误明确。

### 5.3 BackgroundProcessor 并发测试

目的：验证后台任务不会污染共享状态。

测试内容：多个后台任务同时执行；失败任务异常可被收集；通知队列顺序明确；与 trace/failure memory 交互无明显竞态。当前可先标记为 `Needs verification`，不必立即投入。

### 5.4 MessageBus 测试

目的：验证消息总线不会死锁或丢消息。

测试内容：send 后磁盘文件存在；subscribe callback 不应在锁内导致重入死锁；同时发送多个消息不产生重复 ID；进程重启后能否恢复消息，如果不支持，文档说明。

## 6. Evaluation 策略

普通测试验证代码逻辑；Eval 验证 Agent 行为质量。Agent 可能最终完成任务，但过程很差，例如重复调用同一个工具、在错误目录执行命令、失败后不换策略、不运行测试、过度压缩后忘记状态、执行大量无意义命令。因此需要过程指标。

建议最终固化指标：total_turns、duplicate_tool_ratio、failure_recovery_rate、escalation_count、command_policy_blocks、compression_count、test_run_count、final_task_status。

历史总结中有一个强案例：网络不可达导致 pip install retry storm；引入 Failure Intelligence 后，3 次同策略失败触发 escalation；Agent 改用 tkinter 方案；任务轮数从约 35 降到约 16。注意：这个案例来自历史总结，需要用当前 repo trace 重新验证后才能作为正式展示数据。未验证前标记 `Needs verification`。

## 7. CI 建议

短期不需要复杂 CI，但建议至少有：

```bash
py -m pytest tests/integration/test_runtime_context.py -v
py -m pytest tests/integration/test_failure_intelligence.py -v
py -m pytest tests/integration/test_all_modules.py -v
```

后续增加：

```bash
py -m pytest tests/integration/test_workspace_authority.py -v
py -m pytest tests/integration/test_cli_entrypoint.py -v
py -m pytest tests/integration/test_eval_runner.py -v
```

原则：CI 不依赖真实 LLM API key；所有 LLM 行为用 fake provider、mock 或 fixture；eval 完整运行可以手动触发，不一定放入每次 CI。

## 8. 面试展示建议

测试材料应准备三类：单元/集成测试结果、端到端 demo trace、eval summary。

面试中可以这样讲：我没有只做一个能聊天的 Agent，而是把它当成一个 runtime 来测。路径权限、shell 状态、失败分类、trace schema、eval runner 都有不同层级的测试。Agent 的难点不是单个函数是否正确，而是副作用、状态和失败恢复是否可控。
