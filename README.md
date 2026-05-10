# mini-claude — Local Multi-Agent Runtime

> **具备工业级沙箱隔离、并发控制与故障注入评测基座的本地多智能体底座。**
>
> 不是一个 API Wrapper，不是 LangChain 的配置胶水，也不是堆砌 UI 的前端玩具。
> 这是一个从**架构正确性**出发设计的 Agent Engine —— 关注隔离性、容错性、可观测性与可测试性。

---

## Architecture Highlights

### 1. Shadow Workspace + Two-Phase Commit（沙箱隔离与两阶段提交）

子代理（SubAgent）的所有文件操作均在隔离的 **影子工作区**（`.claude/shadow/<task_id>/`）中执行，而非直接触碰主工作目录。

```
主工作区                    影子工作区                  结果
=======                    ==========                  ====
                           subagent writes
                           files in isolation
                              │
                    ┌───────┴───────┐
                    │               │
                 SUCCESS          FAILURE
                    │               │
                    ▼               ▼
               COMMIT           ROLLBACK
           copy2() merge     rmtree() + context
           to main workdir   rollback to checkpoint
```

- **COMMIT 阶段**：子代理成功后，`shutil.copy2()` 将产物合并回主工作区，保证原子性
- **ROLLBACK 阶段**：子代理失败时，影子目录被完整销毁（`shutil.rmtree`），LLM 上下文回退到子代理调用前的 checkpoint，杜绝部分脏数据泄露
- **防泄露验证**：评测任务 I 专门验证——子代理中途失败后，其写入的文件在主工作区不可见

### 2. MessageBus with RLock Concurrency Control（线程安全消息总线）

消息总线是所有代理间通信的唯一通道，**所有关键路径**（`send`、`broadcast`、`read_inbox`、`subscribe`）均由 `threading.Lock()` 保护。

```
Teammate A ──→ MessageBus (RLock) ──→ Teammate B
                    │
                    ├── 持久化到磁盘（JSON）
                    ├── 通知订阅者回调
                    └── 读取后自动清理（防止磁盘泄漏）
```

- 支持 **4 种消息类型**：DIRECT / BROADCAST / SYSTEM / TASK_UPDATE
- 支持 **4 级优先级**：LOW / NORMAL / HIGH / CRITICAL
- 消息读取后自动删除持久化文件，防止磁盘泄漏
- 重启后可恢复未读消息

### 3. Fault-Injection Evaluation Harness（故障注入评测基座）

内置 `eval_runner.py` 提供 9 个基准评测任务（A-I），覆盖 6 大容错维度：

| 任务 | 名称 | 测试维度 |
|------|------|---------|
| A | File I/O | 基础工具调用正确性 |
| B | 环境探针 | 系统环境交互能力 |
| C | 容错测试 | 异常文件路径的优雅处理 |
| D | 长上下文记忆与压缩容错 | Token 压缩后关键信息保持 |
| E | 子代理委托测试 | SubAgent 隔离执行正确性 |
| F | 底层异常与重试容错 | **故障注入**——工具调用中途崩溃后的自愈 |
| G | 多轮对话退化测试 | 多轮交互中记忆持久性 |
| H | 参数幻觉自愈测试 | LLM 幻觉参数后的自我纠正 |
| I | 回滚状态防泄露测试 | Shadow Workspace 2PC 回滚完整性 |

评测框架支持：
- **故障注入**（`fault_inject`）：通过 `unittest.mock` 在工具调用中途注入崩溃，验证 Agent 的重试与自愈逻辑
- **多轮对话模式**（`prompts` 列表）：在同一 Agent 实例上连续发送多条消息
- **后置初始化钩子**（`post_agent_init`）：在评测前动态修改 Agent 行为（如强制子代理快速失败）
- 所有结果自动追加到 `logs/eval_results.csv`

### 4. Pluggable Feature System with Dependency Resolution

功能管理器支持运行时动态启用/禁用模块，并提供**依赖解析**：

```python
FeatureDefinition(
    name='subagent',
    enabled=True,
    dependencies=[
        FeatureDependency(feature='tasks', required=True),   # 依赖任务系统启用
        FeatureDependency(feature='background', required=False),  # 可选依赖
    ]
)
```

### 5. Dict-Based Tool Dispatch（字典路由模式）

遵循 s_full.py 的核心设计原则——**永不使用硬编码 if/elif 链**：

```python
self.tool_dispatcher = {
    "bash":       self._handle_bash,
    "read_file":  self._handle_read_file,
    "write_file": self._handle_write_file,
    "edit_file":  self._handle_edit_file,
    "task":       self._handle_task,
    ...
}
```

符合开闭原则（Open/Closed Principle）：新增工具只需注册键值对，无需修改路由逻辑。

### 6. Multi-Provider Failover

支持 Deepseek（OpenAI 兼容 API）和 Anthropic（原生 SDK）双提供者，提供健康检查、自动故障转移与 Provider Manager 统一路由。

---

## What it IS

- **一个 Agent Runtime Engine**：定义了 Agent 的工具调度、子代理沙箱、消息通信、任务编排的完整生命周期
- **一个架构参考实现**：展示了如何用 ~3000 行 Python 构建一个正确、隔离、可测试的多智能体系统
- **一个可评测的 Agent 基座**：内置故障注入评测框架，可以量化和验证 Agent 的容错能力
- **高信噪比**：每个模块都有明确的单一职责，没有为"看起来功能多"而堆砌代码

## What it is NOT

- ❌ 不是 LangChain / AutoGPT 的包装器或配置胶水
- ❌ 不是带 UI 的 Chatbot 前端应用
- ❌ 不是生产级分布式系统（当前为单机本地运行时）
- ❌ 不是万能工具箱——它是一个**引擎**，不是应用商店

---

## Project Structure

```
mini-claude/
├── src/
│   ├── agent/
│   │   ├── mini_claude_agent.py   # 主 Agent：集成所有系统（~1300 行）
│   │   └── minimal_agent.py       # 最小化 Agent（用于测试）
│   ├── core/
│   │   ├── tools/base_tools.py    # 安全工具：bash / read / write / edit
│   │   ├── subagent.py            # 子代理系统 + 故障注入
│   │   ├── messaging/bus.py       # 消息总线（RLock 并发控制）
│   │   ├── background.py          # 异步命令执行器
│   │   ├── compression.py         # 上下文压缩（tiktoken）
│   │   ├── console.py             # REPL 命令系统
│   │   ├── teammate_manager.py    # 队友生命周期管理
│   │   └── features/manager.py    # 可插拔功能管理 + 依赖解析
│   ├── models/
│   │   ├── config.py              # 配置数据类 + YAML 加载
│   │   ├── task.py                # 任务模型 + 依赖管理
│   │   ├── teammate.py            # 队友数据模型
│   │   └── todo.py                # 短期待办管理器
│   ├── providers/
│   │   ├── base.py                # LLM Provider 抽象基类
│   │   ├── deepseek.py            # Deepseek API 实现
│   │   ├── anthropic.py           # Anthropic API 实现
│   │   └── manager.py             # Provider 管理器 + 故障转移
│   └── skills/loader.py           # 技能发现与加载器
├── configs/default.yaml           # 默认配置
├── skills/                        # 可加载技能模块（PDF / Git / Python）
├── eval_runner.py                 # 评测基座（9 个基准任务 + 故障注入）
├── s_full.py                      # 原始单体参考实现
└── tests/
    └── integration/
        └── test_all_modules.py    # 85 个集成测试
```

---

## Quick Start

### 环境要求

- Python 3.10+（本项目使用 `py` 启动器，Python 3.14）
- Windows / macOS / Linux

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API 密钥

```bash
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY 和/或 ANTHROPIC_API_KEY
```

### 3. 运行 Agent

```bash
# 主 Agent（交互式 REPL）
py s_full.py

# 运行评测基座
py eval_runner.py

# 运行集成测试
py -m pytest tests/integration/test_all_modules.py -v
```

### 4. REPL 命令

| 命令 | 功能 |
|------|------|
| `/tasks` | 列出所有任务 |
| `/team` | 查看队友状态 |
| `/inbox` | 读取代理间消息 |
| `/compact` | 手动压缩对话上下文 |
| `/help` | 显示所有命令 |
| `/exit` | 退出 |

---

## Key Design Decisions

| 决策 | 选择 | 理由 |
|------|------|------|
| 工具调度 | Dict 路由 | 开闭原则，易扩展，避免 if/elif 脆弱链 |
| 子代理隔离 | Shadow Workspace + 2PC | 文件系统级隔离，失败零泄漏 |
| 代理间通信 | MessageBus + RLock | 线程安全，解耦代理实例 |
| 任务持久化 | 文件系统 JSON | 零依赖，可审计，重启不丢状态 |
| LLM 提供者 | 抽象基类 + 工厂注册 | 多模型支持，故障自动转移 |
| 功能管理 | 依赖解析的特征开关 | 运行时动态配置，避免条件编译 |
| 评测框架 | 故障注入 + 多轮脚本 | 不仅测"对"，更测"错了之后能不能恢复" |

---

*Built as an open-source portfolio project demonstrating Agent architecture design, fault tolerance patterns, and systems thinking.*
