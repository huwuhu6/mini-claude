# Mini-Claude 工程架构快照 (Architecture Snapshot)

> 生成时间: 2026-05-07
> 目的: Runtime 架构升级与面试防御设计参考
> 原则: 不粉饰，如实记录运行机制与架构真相

---

# 1. 项目目录树

```
mini-claude/
├── s_full.py                          # [参考] 原始单体实现 (~740行)，本项目的"需求规格说明书"
├── eval_runner.py                     # [评测] 自动化 Benchmark Runner，10个Task A-J
├── INTERVIEW_CHEAT_SHEET.md           # [文档] 面试防御底稿
├── README.md                          # [文档] 项目说明
├── CLAUDE.md                          # [配置] Claude Code 指令文件
├── ARCHITECTURE_SNAPSHOT.md           # [本文档]
├── configs/
│   └── default.yaml                   # [配置] 默认 YAML 配置，含 LLM/Feature/Task/Team/Compression
├── docs/
│   └── agent评测框架.md               # [文档] Agent 评测方法论（面试参考）
├── example/
│   └── normalize_messages.txt         # [示例] 消息标准化示例
├── logs/
│   ├── agent.log                      # [日志] 运行时日志
│   └── eval_results.csv               # [评测] Benchmark 结果 CSV 持久化
├── scripts/
│   └── test_llm.py                    # [工具] LLM 连接测试脚本
├── skills/                            # [技能] 可加载的领域知识模块
│   ├── git-cheatsheet.md              #   平面 .md 技能（name=stem）
│   └── pdf/                           #   嵌套包技能（name=父目录名）
│       ├── SKILL.md                   #     技能主体，含 YAML frontmatter
│       ├── LICENSE.txt
│       ├── forms.md
│       ├── reference.md
│       └── scripts/                   #     技能附带的辅助脚本
│           ├── check_bounding_boxes.py
│           ├── check_fillable_fields.py
│           ├── convert_pdf_to_images.py
│           ├── create_validation_image.py
│           ├── extract_form_field_info.py
│           ├── extract_form_structure.py
│           ├── fill_fillable_fields.py
│           └── fill_pdf_form_with_annotations.py
├── src/                               # [核心] 模块化源码
│   ├── __init__.py
│   ├── agent/                         # [Agent] 主 Agent 实现
│   │   ├── __init__.py
│   │   ├── mini_claude_agent.py       #   ★ 核心：统一 Agent，集成所有子系统
│   │   └── minimal_agent.py           #   最小化 Agent（演示用，无压缩/MessageBus/Teammate）
│   ├── core/                          # [核心] 运行时引擎
│   │   ├── __init__.py
│   │   ├── loop_guard.py              #   ★ 死循环检测 + 强制反思注入
│   │   ├── compression.py             #   ★ 上下文压缩（微压缩 + 全量压缩 + LLM摘要）
│   │   ├── subagent.py                #   ★ 子代理系统（Shadow Workspace 隔离运行）
│   │   ├── background.py              #   后台异步命令执行（线程池）
│   │   ├── console.py                 #   REPL 命令系统（/help, /status, /compact...）
│   │   ├── teammate_manager.py        #   AI 队友生命周期管理
│   │   ├── features/                  #   功能开关与依赖管理
│   │   │   ├── __init__.py
│   │   │   └── manager.py             #     FeatureManager: 注册/启用/禁用/工具过滤
│   │   ├── messaging/                 #   进程内消息总线
│   │   │   ├── __init__.py
│   │   │   └── bus.py                 #     MessageBus: 发送/广播/收件箱/持久化
│   │   └── tools/                     #   工具系统
│   │       ├── __init__.py
│   │       └── base_tools.py          #     BaseTools: bash/read_file/write_file/edit_file/list_files
│   ├── models/                        # [模型] 数据模型层
│   │   ├── __init__.py
│   │   ├── config.py                  #   YAML 配置加载 + 环境变量替换 + dataclass
│   │   ├── task.py                    #   Task/TaskManager: 文件持久化的任务系统
│   │   ├── teammate.py                #   Teammate 数据模型
│   │   └── todo.py                    #   TodoManager: 内存中的 TodoWrite 追踪器
│   ├── providers/                     # [Provider] LLM 提供者抽象
│   │   ├── __init__.py
│   │   ├── base.py                    #   LLMProvider(ABC) / Message / ToolDefinition 基类
│   │   ├── manager.py                 #   ProviderManager: 注册/路由/故障转移
│   │   ├── deepseek.py                #   DeepseekProvider (OpenAI-compatible SDK)
│   │   └── anthropic.py               #   AnthropicProvider (Anthropic SDK)
│   └── skills/                        # [技能] 技能加载器
│       ├── __init__.py
│       └── loader.py                  #   SkillLoader: 发现/加载/缓存 YAML frontmatter 解析
├── tests/                             # [测试]
│   ├── integration/
│   │   └── test_all_modules.py        #   13 模块集成测试（无需 API key）
│   ├── unit/                          #   (空目录，无单元测试)
│   └── evaluation/                    #   (空目录)
├── .claude/                           # [配置] Claude Code 配置
│   ├── settings.local.json
│   └── shadow/                        #   [运行时] ★ Shadow Workspace 目录（子代理隔离执行）
├── .tasks/                            # [运行时] 文件持久化任务队列
│   └── task_8fba9bbe.json
├── .team/                             # [运行时] 队友状态持久化
│   └── teammate_dc66d368.json
├── .inbox/                            # [运行时] MessageBus 消息持久化
├── .transcripts/                      # [运行时] 压缩后的对话记录
├── configs/default.yaml               # [配置]
├── requirements.txt                   # [依赖]
└── requirements-dev.txt               # [开发依赖]
```

---

# 2. 系统主执行链路

## 2.1 完整调用链（Mermaid 流程图）

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           用户输入 "帮我写个脚本"                          │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ main() [mini_claude_agent.py:1056]                                       │
│   └─ agent.chat(user_input) [line:990]                                   │
│       ├─ 以 '/' 开头 → console.execute() → Command handler              │
│       └─ 否则 → agent.run(user_input)                                    │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    ▼                         ▼
        features.tasks==False         features.tasks==True
                    │                         │
                    ▼                         ▼
         _run_simple()               _run_with_tasks()
         [line:350]                  [line:355]
         messages.append(user)       messages.append(user)
         └─ _llm_tool_cycle()        ├─ compressor.should_compress()? → compress()
                                     │  compressor.should_microcompact()? → microcompact()
                                     └─ _llm_tool_cycle() [line:681]
└────────────────────────────────┬──────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ _llm_tool_cycle(max_iterations=35) [mini_claude_agent.py:681]            │
│                                                                          │
│  ┌─ Pre-loop ─────────────────────────────────────────────────────┐      │
│  │ 1. _drain_background_notifications() → inject <bg-results>     │      │
│  │ 2. _check_inbox() → inject <inbox> message from teammates      │      │
│  │ 3. Reset self.last_metrics = {turns:0, tokens:0, errors:0}     │      │
│  └────────────────────────────────────────────────────────────────┘      │
│                                                                          │
│  for iteration in range(35):                                             │
│    ┌─ Pre-LLM Pipeline ────────────────────────────────────────────┐     │
│    │ 1. _microcompact()        → 清除旧 tool 结果（>3条）            │     │
│    │ 2. _check_auto_compress() → threshold 检查 + 触发压缩          │     │
│    │ 3. _clean_tool_chains()   → 确保 tool_calls↔tool 闭环         │     │
│    └───────────────────────────────────────────────────────────────┘     │
│                                                                          │
│    ┌─ LLM Call ────────────────────────────────────────────────────┐     │
│    │ response = provider.create_message(                            │     │
│    │     self.messages,        # 当前完整对话上下文                   │     │
│    │     tool_defs,            # 已注册+过滤后的 Tool schema          │     │
│    │     system=self.system_prompt,                                 │     │
│    │ )                                                              │     │
│    │ parsed = provider.parse_response(response)                     │     │
│    │   → {content, tool_calls[], usage{}}                          │     │
│    └───────────────────────────────────────────────────────────────┘     │
│                                                                          │
│    if no tool_calls:                                                     │
│        → messages.append(assistant content)                              │
│        → return content  # 对话结束                                      │
│                                                                          │
│    ┌─ Tool Execution Loop ─────────────────────────────────────────┐     │
│    │ messages.append(assistant with tool_calls)                     │     │
│    │                                                                │     │
│    │ for each tool_call in tool_calls:                              │     │
│    │   1. JSON parse arguments                                      │     │
│    │   2. DUPLICATE check: (name, args_raw) in seen_tool_sigs?     │     │
│    │   3. LOOP GUARD check: self.loop_guard.check(name, args)      │     │
│    │      ├─ 检测到循环 → 返回 fake error message（工具未执行）      │     │
│    │      └─ 安全通过 → self._execute_tool(name, args)              │     │
│    │   4. loop_guard.record(name, args)                             │     │
│    │   5. SOFT PROMPT check: 连续3次相同命令且相同输出?              │     │
│    │      → 追加 [系统提示] 建议改变策略                             │     │
│    │   6. messages.append(tool result)                              │     │
│    └───────────────────────────────────────────────────────────────┘     │
│                                                                          │
│    ┌─ Nag Reminder ────────────────────────────────────────────────┐     │
│    │ if TodoWrite not used for 3+ rounds AND has open items:        │     │
│    │     messages.append(<reminder>Update your todos.</reminder>)   │     │
│    └───────────────────────────────────────────────────────────────┘     │
│                                                                          │
│  (loop back to LLM call — messages now include tool results)             │
└──────────────────────────────────────────────────────────────────────────┘
```

## 2.2 关键状态流转

### messages 生命周期

```
[] ──(user input)──▶ [user]
                    ──(LLM call)──▶ [user, assistant(tool_calls)]
                    ──(tool exec)─▶ [user, assistant(tool_calls), tool, tool, ...]
                    ──(LLM call)──▶ [..., assistant(content)]  ← 终态，返回给用户
```

### tool_call 执行过程

```
LLM response
  │
  ├─ parsed.tool_calls = [
  │    {id: "call_1", function: {name: "read_file", arguments: '{"path": "x.txt"}'}},
  │    {id: "call_2", function: {name: "bash", arguments: '{"command": "ls"}'}},
  │  ]
  │
  ▼
for tc in tool_calls:
  1. args = json.loads(tc.function.arguments)          ← JSON 解析，失败则注入自愈反馈
  2. sig = (name, canonicalized_args)                   ← 去重检查
  3. loop_msg = loop_guard.check(name, args)            ← 死循环拦截
     ├─ detected → result = fake_error_message          ← 工具未执行！
     └─ clean    → result = _execute_tool(name, args)   ← 真正执行
  4. loop_guard.record(name, args)
  5. messages.append(Message(role='tool', content=result, tool_call_id=tc.id))
```

### Shadow Workspace Commit/Rollback

```
主 Agent 调用 task tool
  │
  ├─ checkpoint = len(self.messages)          ← 上下文快照
  ├─ shadow_dir = .claude/shadow/<task_id>/   ← 创建影子目录
  ├─ shutil.copy2(*, shadow_dir)             ← 镜像主工作区文件
  ├─ result = subagent_manager.run(prompt, workdir=shadow_dir)
  │
  ├─ if result.success:
  │    for p in shadow_dir:
  │       shutil.copy2(p, self.workdir)       ← COMMIT: 合并回主工作区
  │    shutil.rmtree(shadow_dir)              ← 清理
  │
  └─ if not result.success:
       shutil.rmtree(shadow_dir)              ← ROLLBACK: 销毁影子
       self.messages = self.messages[:checkpoint]  ← ROLLBACK: 丢弃脏消息
```

---

# 3. Agent Loop 核心代码

## 3.1 Agent 主循环

文件: [src/agent/mini_claude_agent.py:681-826](src/agent/mini_claude_agent.py#L681-L826)

```python
def _llm_tool_cycle(self, max_iterations: int = 35) -> str:
    provider = self.provider_manager.get_primary_provider()
    if not provider:
        return "错误: 没有可用的 LLM 提供者。"

    tools = self._get_llm_tools()
    tool_defs = [ProviderToolDef(**t) for t in tools]

    # Pre-loop: drain background notifications and inbox
    self._drain_background_notifications()
    self._check_inbox()

    self.last_metrics = {"turns": 0, "total_tokens": 0, "api_errors": 0}
    rounds_without_todo = 0

    for iteration in range(max_iterations):
        try:
            # Pre-LLM pipeline
            self._microcompact()
            self._check_auto_compress()
            self.messages = Compressor._clean_tool_chains(self.messages)

            # LLM call
            response = provider.create_message(
                self.messages, tool_defs,
                system=self.system_prompt,
                max_tokens=self.config.llm.max_tokens,
                temperature=self.config.llm.temperature,
            )
            parsed = self._parse_response(provider, response)
            content = parsed.get('content', '')
            tool_calls = parsed.get('tool_calls', [])

            # Accumulate metrics
            usage = parsed.get('usage', {})
            self.last_metrics["total_tokens"] += usage.get('total_tokens', 0)
            self.last_metrics["turns"] = iteration + 1

            if not tool_calls:
                self.messages.append(Message(role='assistant', content=content))
                return content

            # Store assistant message with tool_calls
            self.messages.append(Message(
                role='assistant', content=content, tool_calls=tool_calls,
            ))

            seen_tool_sigs: set = set()
            used_todo = False

            for tc in tool_calls:
                fn = tc.get('function', {})
                tname = fn.get('name', '')
                args_raw = fn.get('arguments', '{}')
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw

                sig = (tname, args_raw)
                if sig in seen_tool_sigs:
                    continue  # skip duplicate
                seen_tool_sigs.add(sig)

                if tname == "TodoWrite":
                    used_todo = True

                # Loop guard
                loop_msg = self.loop_guard.check(tname, args)
                if loop_msg:
                    result_text = loop_msg
                else:
                    result = self._execute_tool(tname, args)
                    result_text = str(result)
                self.loop_guard.record(tname, args)

                # Soft prompt for repeated commands
                if tname == 'bash' and not loop_msg:
                    sig = f"{tname}:{args_raw}"
                    preview = result_text[:200]
                    if self._cmd_history and self._cmd_history[-1][0] != sig:
                        self._cmd_history.clear()
                    self._cmd_history.append((sig, preview))
                    if len(self._cmd_history) >= 3:
                        prev = [e[1] for e in self._cmd_history]
                        if len(set(prev)) == 1 and '[系统提示]' not in result_text:
                            result_text += (
                                "\n\n[系统提示] 你已连续执行相同命令多次，且输出没有变化。"
                                "如果这是轮询或重试操作请忽略；否则请考虑改变策略或使用不同参数。"
                            )

                self.messages.append(Message(
                    role='tool', content=result_text,
                    tool_call_id=tc.get('id', ''),
                ))

            # Nag reminder
            rounds_without_todo = 0 if used_todo else rounds_without_todo + 1
            if self.todo.has_open_items() and rounds_without_todo >= 3:
                self.messages.append(Message(
                    role='user',
                    content='<reminder>Update your todos.</reminder>',
                ))

        except Exception as e:
            self.last_metrics["api_errors"] += 1
            return f"错误: {str(e)}"

    return "错误: 工具执行次数过多，已自动终止。"
```

## 3.2 Tool Dispatch

文件: [src/agent/mini_claude_agent.py:72-81](src/agent/mini_claude_agent.py#L72-L81) + [src/agent/mini_claude_agent.py:611-623](src/agent/mini_claude_agent.py#L611-L623)

```python
# 在 __init__ 中绑定一次
self.tool_dispatcher = {
    "bash": self._handle_bash,
    "read_file": self._handle_read_file,
    "write_file": self._handle_write_file,
    "edit_file": self._handle_edit_file,
    "load_skill": self._handle_load_skill_dispatch,
    "task": self._handle_task,             # ← 子代理（含 Shadow Workspace + 2PC）
    "TodoWrite": self._handle_todo_write,
}

# 在 _llm_tool_cycle 中调用
def _execute_tool(self, name: str, args: Dict[str, Any]) -> str:
    handler = self.tool_dispatcher.get(name)
    if not handler:
        return f"错误: 未知工具 '{name}'"
    try:
        return handler(**args)
    except TypeError as e:
        return f"错误: 工具 '{name}' 参数无效。详情: {str(e)}"
    except Exception as e:
        return f"错误: {str(e)}"
```

## 3.3 LLM 调用路径

文件: [src/providers/deepseek.py:24-94](src/providers/deepseek.py#L24-L94) (Deepseek) + [src/providers/anthropic.py:25-113](src/providers/anthropic.py#L25-L113) (Anthropic)

```
agent._llm_tool_cycle()
  → provider.create_message(messages, tool_defs, system=..., **kwargs)
    → DeepseekProvider.create_message()
      → format messages for OpenAI API
      → prepend system prompt as role='system' message
      → convert ToolDefinition → {'type':'function', 'function':{name, description, parameters}}
      → client.chat.completions.create(**params)
      → return OpenAI ChatCompletion object

  → provider.parse_response(response)
    → DeepseekProvider.parse_response()
      → {content: response.choices[0].message.content,
         tool_calls: [{id, type, function: {name, arguments}}],
         usage: {prompt_tokens, completion_tokens, total_tokens}}
```

## 3.4 Reflection 插入机制

当 LoopGuard 触发时（见第4章），注入的 fake error message 中包含强制反思指令:

```python
# src/core/loop_guard.py:26-45
def build_loop_block_message(tool_name, args):
    return (
        "⛔ [系统安全拦截 — 防死循环保护]\n"
        "该调用已被系统物理拦截——工具未被执行。\n"
        "你必须在下一次回复中最先输出一个 <reflection> 标签，"
        "在其中深刻分析：\n"
        "  1. 为什么之前的尝试反复失败？\n"
        "  2. 当前策略的根本问题是什么？\n"
        "  3. 有哪些与之前完全不同的替代方案？\n"
        ...
    )
```

---

# 4. LoopGuard 机制分析

## 4.1 核心代码

文件: [src/core/loop_guard.py](src/core/loop_guard.py)

```python
class LoopGuard:
    def __init__(self, max_recent: int = 3, min_occurrences: int = 2):
        self.max_recent = max_recent          # 滑动窗口大小
        self.min_occurrences = min_occurrences # 触发阈值
        self.recent_calls: List[ToolCallRecord] = []  # [(tool_name, canonicalized_args), ...]

    def check(self, tool_name: str, args: Dict[str, Any]) -> Optional[str]:
        """在工具执行前检查。返回 None = 安全，否则返回拦截消息。"""
        if not self.recent_calls:
            return None

        current_sig = canonicalize_args(args)

        # Rule 1: 与上一次调用完全相同（连续重复）
        prev_name, prev_sig = self.recent_calls[-1]
        if prev_name == tool_name and prev_sig == current_sig:
            return build_loop_block_message(tool_name, args)

        # Rule 2: 在滑动窗口内出现 >= min_occurrences 次（频率阈值）
        window = self.recent_calls[-self.max_recent:]
        match_count = sum(1 for name, sig in window
                          if name == tool_name and sig == current_sig)
        if match_count >= self.min_occurrences:
            return build_loop_block_message(tool_name, args)

        return None

    def record(self, tool_name: str, args: Dict[str, Any]) -> None:
        """记录一次 tool call（无论是否被拦截都记录）。"""
        sig = canonicalize_args(args)
        self.recent_calls.append((tool_name, sig))
        if len(self.recent_calls) > self.max_recent * 3:
            self.recent_calls = self.recent_calls[-self.max_recent:]
```

## 4.2 Hash 生成

```python
def canonicalize_args(args: Dict[str, Any]) -> str:
    """生成顺序无关的 JSON 指纹"""
    return json.dumps(args, sort_keys=True, ensure_ascii=False)
```

关键设计: `sort_keys=True` 确保 `{"a":1,"b":2}` 和 `{"b":2,"a":1}` 生成相同的 hash。

## 4.3 检测规则

| 规则 | 条件 | 默认参数 | 含义 |
|------|------|----------|------|
| Rule 1 | `recent_calls[-1] == (name, sig)` | 立即触发 | 当前调用与**上一次**字节相同 |
| Rule 2 | 窗口内 `(name, sig)` 出现次数 >= 2 | `max_recent=3, min_occurrences=2` | 最近3次调用中有2次相同 |

## 4.4 阈值设计

- `max_recent = 3`: 窗口足够小以避免误杀合理的重试，也足够大以捕获 "A→B→A→B→A" 的交替循环模式
- `min_occurrences = 2`: 在3次窗口中出现2次即触发，等价于 "允许一次重试，不允许第二次"
- `max_recent * 3` 的保留上限: 防止列表无限增长，保留最后一小段以确保历史窗口可用

## 4.5 示例输入输出

```
# 正常场景
record("bash", {"command": "ls"})
record("bash", {"command": "cat a.txt"})
record("bash", {"command": "ls"})     # ← check 之前: window = [ls, cat, ls], 当前ls出现1次, OK

# 触发 Rule 1 (连续重复)
record("bash", {"command": "python broken.py"})
check("bash", {"command": "python broken.py"})  # → 返回拦截消息!
# 工具未执行，LLM 看到 fake error → 被迫反思

# 触发 Rule 2 (频率阈值)
record("bash", {"command": "curl api"})
record("read_file", {"path": "x.txt"})
record("bash", {"command": "curl api"})
check("bash", {"command": "curl api"})  # → window = [curl, read, curl], 当前curl出现2次 >= 2 → 拦截!
```

---

# 5. Shadow Workspace + 回滚机制

## 5.1 架构概览

这是一个**文件系统级别的 Two-Phase Commit (2PC)** 实现，用于子代理的隔离执行。

## 5.2 核心代码

文件: [src/agent/mini_claude_agent.py:499-587](src/agent/mini_claude_agent.py#L499-L587)

```python
def _handle_task(self, prompt: str, agent_type: str = "general-purpose") -> str:
    # ──── Phase 0: Context Snapshot ────
    checkpoint = len(self.messages)    # 记录当前消息数量作为回滚点

    # ──── Phase 1: Create Shadow Workspace ────
    shadow_root = self.workdir / ".claude" / "shadow"
    shadow_root.mkdir(parents=True, exist_ok=True)
    task_id = str(uuid.uuid4())[:8]
    shadow_dir = shadow_root / task_id
    shadow_dir.mkdir(parents=True, exist_ok=True)

    # Mirror: copy ALL non-hidden files from main workdir into shadow
    for p in self.workdir.iterdir():
        if p.is_file() and not p.name.startswith("."):
            try:
                shutil.copy2(p, shadow_dir / p.name)
            except OSError:
                pass

    # ──── Phase 2: Execute SubAgent in Shadow ────
    result = self.subagent_manager.run(
        prompt, agent_type=atype, workdir=shadow_dir,
    )

    if not result.success:
        # ──── ROLLBACK ────
        # 1. Destroy shadow directory (no partial file leaks)
        shutil.rmtree(shadow_dir, ignore_errors=True)

        # 2. Context rollback: discard all messages added since checkpoint
        if len(self.messages) > checkpoint:
            snipped = len(self.messages) - checkpoint
            self.messages = self.messages[:checkpoint]  # ← slice rollback

        return f"[子代理执行失败 — 上下文已回滚]\n原因: {result.error}..."

    # ──── COMMIT ────
    committed = 0
    for p in shadow_dir.iterdir():
        if p.is_file():
            try:
                shutil.copy2(p, self.workdir / p.name)
                committed += 1
            except OSError:
                pass

    # Clean up
    shutil.rmtree(shadow_dir, ignore_errors=True)

    return f"[子代理执行完成]\n已提交文件: {committed} 个\n{result.content}"
```

## 5.3 状态变化过程

```
状态 0 (初始)
  workdir/
    a.txt  b.txt  .env
  self.messages = [user_msg_1, assistant_1, ...]  ← length = N

状态 1 (Snapshot)
  checkpoint = N

状态 2 (Shadow Created)
  .claude/shadow/abc123/
    a.txt (copy)  b.txt (copy)   ← .env NOT copied (hidden file)

状态 3 (SubAgent Running in Shadow)
  .claude/shadow/abc123/
    a.txt  b.txt  new_file.txt   ← SubAgent 创建了新文件
  self.messages = [..., "请创建子代理", assistant(tool_call), tool_result, ...] ← length = N + M

状态 4a (COMMIT — success)
  workdir/
    a.txt (可能被覆盖)  b.txt  new_file.txt   ← 从影子合并
  .claude/shadow/abc123/  ← 已删除
  self.messages 长度保持 N + M

状态 4b (ROLLBACK — failure)
  workdir/
    a.txt  b.txt          ← 完全未变
  .claude/shadow/abc123/  ← 已删除
  self.messages = self.messages[:N]  ← 丢弃了 M 条"脏"消息
```

## 5.4 messages slice rollback 实现

```python
# 简单而暴力的 slice rollback
if len(self.messages) > checkpoint:
    self.messages = self.messages[:checkpoint]
```

关键特征:
- **不维护 undo log** — 直接切片截断
- **事务边界清晰** — checkpoint 在 shadow 创建前记录，此时还没有任何"脏"操作
- **零额外存储** — 不需要保存旧内容，slice 本身就是回滚

---

# 6. MessageBus 机制

## 6.1 数据结构

文件: [src/core/messaging/bus.py](src/core/messaging/bus.py)

```python
@dataclass
class Message:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    msg_type: MessageType = MessageType.DIRECT     # DIRECT | BROADCAST | SYSTEM | TASK_UPDATE | TEAMMATE_STATUS
    sender: str = ""
    recipient: str = ""                            # empty = broadcast
    content: str = ""
    priority: MessagePriority = MessagePriority.NORMAL  # LOW=0 | NORMAL=1 | HIGH=2 | CRITICAL=3
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
```

内部存储:
```python
class MessageBus:
    def __init__(self, storage_dir=None):
        self._inboxes: Dict[str, List[Message]] = {}       # recipient_name → [messages]
        self._subscribers: Dict[str, List[Callable]] = {}   # recipient_name → [callbacks]
        self._storage_dir = storage_dir                     # .inbox/ 目录
        self._lock = threading.Lock()                       # 全局锁
```

## 6.2 `threading.Lock` 范围

```python
def send(self, message: Message) -> str:
    with self._lock:                                  # ← 锁开始
        if message.recipient not in self._inboxes:
            self._inboxes[message.recipient] = []
        self._inboxes[message.recipient].append(message)
        self._notify_subscribers(message.recipient, message)
        self._persist_message(message)
    return message.id                                 # ← 锁释放

def read_inbox(self, recipient: str, mark_read=True) -> List[Message]:
    with self._lock:                                  # ← 锁开始
        messages = list(self._inboxes.get(recipient, []))
        if mark_read and messages:
            self._inboxes[recipient] = []
            # 同时删除持久化文件
            if self._storage_dir:
                for msg in messages:
                    fpath = self._storage_dir / f"{msg.id}.json"
                    try:
                        fpath.unlink(missing_ok=True)
                    except OSError:
                        pass
    return messages                                   # ← 锁释放
```

锁的范围 = **整个 inbox 读/写操作**。订阅者回调在锁内调用（有死锁风险，见技术债）。

## 6.3 Disk Persistence

```python
def _persist_message(self, message: Message) -> None:
    if not self._storage_dir:
        return
    try:
        file_path = self._storage_dir / f"{message.id}.json"
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(message.to_dict(), f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"持久化消息失败: {e}")
```

- 每条消息存为独立 JSON 文件: `.inbox/<uuid>.json`
- 读取时文件被删除 (consume semantics)
- 崩溃恢复通过 `load_persisted_messages()` 扫描 `.inbox/` 目录

## 6.4 Message 生命周期

```
创建: msg = Message(sender="alice", recipient="bob", content="Hello")
  ↓
发送: bus.send(msg)
  ├── 写入内存 inbox dict
  ├── 通知订阅者回调
  └── 持久化到 .inbox/<msg.id>.json
  ↓
读取: bus.read_inbox("bob")
  ├── 返回 messages list
  ├── 清空内存 inbox
  └── 删除 .inbox/*.json 文件
  ↓
消息消失 (consume once)
```

## 6.5 并发冲突避免

- `threading.Lock()` 互斥锁保护 inbox dict
- 不存在跨线程消息争抢（单 Agent 主线程消费）
- 但: **没有 ACK / 重试 / 死信队列** — consume-then-delete 语义下，如果消费者在读取后崩溃，消息丢失

---

# 7. Tool System

## 7.1 所有 Tool 列表

| Tool Name | Schema | Handler | 功能 |
|-----------|--------|---------|------|
| `bash` | `{command: string}` | `_handle_bash` → `BaseTools.run_bash()` | Shell 命令执行（三层安全拦截） |
| `read_file` | `{path: string, limit?: int}` | `_handle_read_file` → `BaseTools.read_file()` | 读取文件（路径白名单校验） |
| `write_file` | `{path: string, content: string}` | `_handle_write_file` → `BaseTools.write_file()` | 写入文件 |
| `edit_file` | `{path: string, old_text: string, new_text: string}` | `_handle_edit_file` → `BaseTools.edit_file()` | 精确文本替换 |
| `load_skill` | `{name: string}` | `_handle_load_skill_dispatch` → `SkillLoader` | 加载领域知识模块 |
| `task` | `{prompt: string, agent_type?: enum}` | `_handle_task` → `SubAgentManager.run()` | 创建子代理（含 Shadow Workspace + 2PC） |
| `TodoWrite` | `{items: [{content, status, activeForm}]}` | `_handle_todo_write` → `TodoManager.update()` | 更新任务追踪列表 |

## 7.2 Tool Schema 定义

文件: [src/agent/mini_claude_agent.py:369-471](src/agent/mini_claude_agent.py#L369-L471)

```python
def _get_llm_tools(self) -> List[Dict[str, Any]]:
    all_tools = [
        {
            'name': 'bash',
            'description': 'Run a shell command.',
            'input_schema': {
                'type': 'object',
                'properties': {'command': {'type': 'string', 'description': '...'}},
                'required': ['command'],
            },
        },
        # ... (其他 6 个工具类似)
    ]
    return self.feature_manager.filter_tools(all_tools)
    # FeatureManager 根据 tool→feature 映射过滤掉已禁用功能的工具
```

## 7.3 Tool Dispatch 机制

```python
# Dict-based routing（非 if/elif 链）
self.tool_dispatcher = {
    "bash": self._handle_bash,
    "read_file": self._handle_read_file,
    ...
}

def _execute_tool(self, name: str, args: Dict[str, Any]) -> str:
    handler = self.tool_dispatcher.get(name)  # O(1) dict lookup
    if not handler:
        return f"错误: 未知工具 '{name}'"
    return handler(**args)  # args unpacking → 类型安全的参数传递
```

## 7.4 Tool 权限结构 (BaseTools)

文件: [src/core/tools/base_tools.py:101-191](src/core/tools/base_tools.py#L101-L191)

bash 工具有三层命令注入防御:

```
Layer 1: Shell 控制字符检测
  regex: [|;&$()`]
  拦截: "ls | grep x", "echo $(whoami)", "cmd1;cmd2"

Layer 2: 高危执行器检测
  regex: powershell, pwsh, wmic, cmd /c, start /, runas
  拦截: PowerShell, WMIC, cmd.exe 直通

Layer 3: 破坏性文件操作检测
  regex: rm -r, rmdir /s, del /f, format, mkfs, fdisk, dd if=, shutdown, reboot
  拦截: "rm -rf /", "format c:", "dd if=/dev/zero"
```

## 7.5 Tool 返回结构

```python
@dataclass
class ToolResult:
    content: str       # 工具输出文本（或错误消息）
    success: bool = True
```

---

# 8. Eval Harness

## 8.1 Task A-J 评测矩阵

文件: [eval_runner.py:102-288](eval_runner.py#L102-L288)

| Task | 名称 | 评测维度 | 核心机制 |
|------|------|----------|----------|
| **A** | File IO | 基础文件读写 | `write_file` → `read_file` |
| **B** | 环境探针 | 环境变量读取 + 计算 | `bash` 执行 + 结果验证 |
| **C** | 容错测试 | 文件不存在的错误处理 | 验证响应包含错误关键词 |
| **D** | 长上下文记忆 | 记忆保持 + 压缩容错 | 4次无意义调用后仍能记住密钥 |
| **E** | 子代理委托 | 子代理创建 + Shadow Workspace | `task` 工具 + 文件验证 |
| **F** | 底层异常与重试 | **故障注入** + 自愈 | mock.patch read_file → 第一次抛异常 |
| **G** | 多轮对话退化 | 跨轮记忆 + 对话降解 | 3轮 prompts: 记忆→干扰→回忆 |
| **H** | 参数幻觉自愈 | 幻觉纠正 | 故意用错误文件名 real.txt 而文件叫 real_file.txt |
| **I** | 回滚状态防泄露 | **2PC Rollback 正确性** | 子代理写 dirty data → fail → 验证 data.jsonl 不存在 |
| **J** | 死循环打破与反思 | **LoopGuard + Reflection** | compile.py 需 `--force-override` 才成功，强制 LLM 反思 |

## 8.2 Fault Injection 机制

```python
# Task F: 第一次 read_file 调用崩溃，迫使 LLM 分析错误并重试
def _task_f_fault_inject():
    _original_read = BaseTools.read_file
    _fault_counter = [0]

    def _injected(self_, path, limit=None):
        _fault_counter[0] += 1
        if _fault_counter[0] == 1:
            raise Exception("CRITICAL SYSTEM FAILURE: Disk read timeout (Error 0x8007045D)")
        return _original_read(self_, path, limit)

    return mock.patch.object(BaseTools, 'read_file', _injected)
    # ← 使用 unittest.mock.patch.object 猴子补丁

# Task I: 强制子代理在 1 次 tool call 后 crash
def _patch_subagent_for_fast_fail(agent):
    _original_run = agent.subagent_manager.run
    def _fast_fail_run(prompt, agent_type=None, workdir=None, max_iterations=30, **kw):
        return _original_run(prompt, ..., fail_after_tool_calls=1)
    agent.subagent_manager.run = _fast_fail_run  # ← 猴子补丁
```

## 8.3 mock.patch 注入点

```
eval_runner.py
  ├── _task_f_fault_inject()  → mock.patch.object(BaseTools, 'read_file', _injected)
  └── _patch_subagent_for_fast_fail() → monkey-patch SubAgentManager.run
```

注: 注入点是 `BaseTools` **类级别**，影响该 Agent 实例的所有工具调用。

## 8.4 Metrics 统计

```python
# 每个任务收集的指标
result = {
    "timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "task_id":      task.id,
    "task_name":    task.name,
    "success":      success,        # verify() callback 返回值
    "turns":        m.get("turns", 0),           # LLM 调用轮数
    "total_tokens": m.get("total_tokens", 0),    # 总 token 消耗
    "api_errors":   m.get("api_errors", 0),      # API 异常次数
    "duration_s":   round(t_end - t_start, 2),  # 端到端耗时
}
```

来源: `agent.last_metrics` — 在 `_llm_tool_cycle` 开始时重置，每轮累加。

## 8.5 Eval Runner 核心代码

```python
def run_benchmark(workdir=None):
    for task in BENCHMARK_TASKS:
        if task.setup:
            task.setup(workdir)          # 创建前置文件

        agent = MiniClaudeAgent(workdir=workdir)

        if task.compression_threshold:
            agent.compressor.token_threshold = task.compression_threshold

        if task.post_agent_init:
            task.post_agent_init(agent)   # 猴子补丁注入

        t_start = time.perf_counter()
        executor = task.fault_inject if task.fault_inject else _null_context
        with executor():                  # 进入 fault injection context
            if task.prompts:
                for p in task.prompts[:-1]:
                    agent.chat(p)          # 多轮对话
                response = agent.chat(task.prompts[-1])
            else:
                response = agent.chat(task.prompt)

        success = task.verify(workdir, response)

        # Hard turn caps enforcement
        if task.max_turns and turns > task.max_turns:
            success = False
        if task.min_turns and turns < task.min_turns:
            success = False

        task.cleanup(workdir)
        agent.shutdown()

    # 输出 Markdown 表格 + 追加 CSV
    print_markdown_table(results)
    append_csv(results, "logs/eval_results.csv")
```

---

# 9. 当前"半完成"或"技术债"模块

## 9.1 最脆弱架构点

| 严重程度 | 位置 | 问题 | 影响 |
|----------|------|------|------|
| **高** | `mini_claude_agent.py:681` `_llm_tool_cycle` | **单线程串行工具循环**。`for tc in tool_calls` 是顺序执行，无法并发执行多个独立工具调用 | if LLM 一次性调用 5 个无关工具，耗时 = sum(所有工具耗时)，而非 max(所有工具耗时) |
| **高** | `subagent.py:191` | **子代理与主代理工具代码重复**。`SubAgent.execute_tool()` 和 `MiniClaudeAgent._execute_tool()` 是两套独立的 dict dispatch，工具 schema 也重复定义 | 添加新工具需要改两处，容易不同步 |
| **高** | `eval_runner.py:66-95` | **`unittest.mock.patch` 直接猴子补丁 `BaseTools`**。这是测试入侵生产代码的典型反模式。没有正式的 Interceptor/Middleware 层 | 评测框架与 Agent 代码紧耦合，无法在不修改源码的情况下注入故障 |
| **中** | `compression.py:265-303` | **`_clean_tool_chains` + `sanitize_openai_messages` 双重清理**。两套逻辑有重叠，维护成本高 | 压缩后消息流可能仍有 400 错误 |
| **中** | `messaging/bus.py:153-158` | **订阅者回调在锁内执行** (`_notify_subscribers` 在 `with self._lock` 内调用) | 如果回调中尝试 `send()` 或 `read_inbox()` → **死锁** |

## 9.2 未真正完成的模块

| 模块 | 完成度 | 说明 |
|------|--------|------|
| **Teammate 自治执行** | 30% | `TeammateManager` 管理状态和生命周期，但**没有 Teammate 的实际 LLM 执行循环**。它只是个状态机，不跑 Agent |
| **Provider 故障转移** | 60% | `ProviderManager.create_message()` 有 try/except 回退逻辑，但 `fail_after_tool_calls` 注入点不经过它 |
| **`minimal_agent.py`** | 50% | 没有 `_llm_tool_cycle` 的多轮循环，只有 1 次 LLM call + 1 轮工具执行 |
| **单元测试** | 0% | `tests/unit/` 目录存在但完全为空 |
| **Message ACK / 重试** | 0% | consume-then-delete 语义，崩溃即丢失 |
| **Agent 状态快照** | 0% | `self.messages` 完全在内存中，崩溃后全部丢失 |

## 9.3 容易出问题的地方

1. **Deepseek API 对消息格式的严格要求**: tool 消息必须紧跟在 assistant(tool_calls) 之后，中间不能有 user 消息。`_drain_background_notifications()` 和 `_check_inbox()` 必须在循环开始前（而非循环内）执行，否则 400 错误
2. **Compression 后的消息完整性**: `compress()` 保留 head 2 条 + tail 10 条，但如果 tail 窗口恰好切在一个 tool_calls→tool 链中间，`_clean_tool_chains` 可能会丢弃整个链
3. **LoopGuard 的 record/check 顺序**: `check()` 必须在 `record()` 之前调用。如果顺序反了，本次调用会被计入窗口，可能误触发自我拦截
4. **Shadow Workspace 的环境变量泄露**: 子代理的 `BaseTools(workdir=shadow_dir)` 设置了 shadow 目录的 workdir，但子代理没有环境变量沙箱，可以通过 `os.environ` 读取敏感信息

## 9.4 当前实现中的 Trade-off

| 选择 | 收益 | 代价 |
|------|------|------|
| 文件 JSON 持久化 | 零依赖、可调试、崩溃可恢复 | 延迟高、无 ACK、无索引查询 |
| `shutil.copy2` 全量镜像 | 隔离绝对性、回滚原子性、跨平台 | O(n) I/O、大文件场景不可用 |
| `threading.Lock()` | 正确性优先、语义清晰 | 锁内回调有死锁风险、非异步 |
| Dict 路由 dispatch | O(1)、可扩展、无 if/elif 气味 | 参数校验靠 `**args` + `TypeError` 兜底 |
| `messages[:checkpoint]` slice rollback | 简单、零存储、正确 | 需要 checkpoint 在正确的时机记录，没有自动化的 undo stack |
| `unittest.mock.patch` 故障注入 | 开发快、无需中间件层 | 紧耦合、不可配置、不可组合 |

---

# 10. 最值得继续演进的方向（从代码角度）

## 方向 1: 工具拦截器层 (Tool Interceptor/Middleware)

**为什么高性价比**: 当前 `_execute_tool()` 中 LoopGuard、去重、soft prompt 都硬编码在 `_llm_tool_cycle` 的 for 循环里。抽象出一个 `ToolInterceptor` 管道:

```python
# 当前代码 smell
for tc in tool_calls:
    # duplicate check inline
    # loop guard inline
    # execute inline
    # soft prompt inline
    # record inline
```

改为:
```python
# 预期架构
for tc in tool_calls:
    result = interceptor_chain.run(tc)  # 可插拔的拦截器管道
```

**影响**: 评测框架的 `mock.patch` 可以被正式的 FaultInjectInterceptor 取代；LoopGuard 成为一个拦截器节点；可以按需组合（去重→安全→日志→执行→metrics）。

## 方向 2: 对话状态增量持久化 (Conversation Snapshotting)

**为什么高性价比**: 当前 `self.messages` 完全在内存中，是单点故障。实现增量 checkpoint:

```python
# 每 N 轮自动保存
if iteration % 5 == 0:
    self._snapshot_messages()  # append-only JSONL 写入 .transcripts/
```

**影响**: 崩溃后可以恢复到最近的 checkpoint；长对话不再害怕进程退出；也是 debug 和 audit 的基础设施。

## 方向 3: SubAgent 工具统一 (Tool System Dedup)

**为什么高性价比**: 当前 `MiniClaudeAgent._get_llm_tools()` 和 `SubAgent.get_tools()` 各自定义工具 schema。应该复用同一份工具注册表:

```python
# src/core/tools/registry.py
class ToolRegistry:
    def register(self, name, schema, handler, permissions):
        ...
    def get_tools(self, agent_type_filter=None):
        ...
```

**影响**: 添加新工具只需改一处；子代理和主代理的工具一致性自动保证；评测框架可以按 agent_type 精确控制工具集。

## 方向 4: 可观测性 (Observability) — Trace + Metrics

**为什么高性价比**: 当前 `last_metrics` 只有 3 个字段 (turns/tokens/errors)，日志是文本行。需要结构化:

```python
# 每个 turn 的结构化 trace
trace = {
    "iteration": i,
    "messages_count": len(self.messages),
    "tool_calls": [{"name": tname, "args_hash": hash(args_raw), "result_hash": hash(result_text[:100])}],
    "tokens_used": usage,
    "loop_guard_triggered": bool(loop_msg),
}
```

**影响**: 评测框架可以直接消费 trace 做过程分析（不仅是 pass/fail）；问题定位从 "这个任务失败了" 变为 "在第 3 轮，read_file 参数错误触发了自愈，第 4 轮恢复"。

## 方向 5: 并发工具执行 (Concurrent Tool Execution)

**为什么高性价比**: 当前 `for tc in tool_calls` 是串行。若 LLM 一次调用了 3 个独立工具:

```python
# 改为 ThreadPoolExecutor + 依赖分析
from concurrent.futures import ThreadPoolExecutor

with ThreadPoolExecutor(max_workers=3) as executor:
    futures = {executor.submit(self._execute_tool, tc): tc for tc in independent_tools}
    for future in as_completed(futures):
        results.append(future.result())
```

**影响**: 多工具场景下延迟从 sum 降为 max；配合 Shadow Workspace 的目录隔离，并发写不会冲突；但需要分析工具间的数据依赖（`bash` 创建文件后 `read_file` 读取 → 不能并发）。

---

*文档结束。本快照基于 2026-05-07 的代码状态，后续代码变更可能导致部分描述失效。*
