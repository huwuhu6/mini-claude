# 项目防死循环机制完整分析

## 上下文

用户希望全面了解当前项目中用于防止 Agent 陷入无限循环/重复失败循环的所有机制。这是对现有代码的复盘分析，而非新功能规划。已通过三个 Explore agent 完成对 `src/core/loop_controller.py`、`src/agent/mini_claude_agent.py`、`src/core/failure_intelligence/`、`src/core/loop_guard.py`、`src/core/compression.py`、`src/core/runtime_context/command_policy.py`、`sandbox/eval_results/` 目录下的历史 trace、`docs/decision_log.md`、`docs/evolution/tool_deduplication.md`、`src/core/tracing/models.py` 的全面审计。

---

## 一、整体架构：七层渐进式防御管线

按从软到硬的顺序，项目共有七层独立的反死循环/反重复失败机制：

| 层 | 机制 | 文件 | 阻塞方式 | LLM 是否感知 |
|---|---|---|---|---|
| 1 | 上下文压缩 (Compression) | `src/core/compression.py` | Token 水位管理，间接防漂移 | 是（消息被摘要替代） |
| 2 | 同轮去重 (Same-turn Dedup) | `mini_claude_agent.py:869-884` | 跳过执行，不返回结果 | 否（完全静默） |
| 3 | 命令安全策略 (CommandPolicy) | `runtime_context/command_policy.py` | 安全规则阻断，返回模拟结果 | 是（收到阻断消息） |
| 4 | V3 LoopGuard (意图感知循环守卫) | `loop_controller.py:288-366` | 物理拦截工具执行，注入拦截消息 | 是（收到阻断消息） |
| 5 | Failure Intelligence (失败智能分析) | `failure_intelligence/` | 升级消息替换 tool_result | 是（收到升级建议） |
| 6 | Hard Circuit Breaker (硬断路器) | `loop_controller.py:371-435` | 抛出异常，物理终止整个循环 | 否（消息直接返回给用户） |
| 7 | max_iterations 硬上限 | `mini_claude_agent.py:778/813` | 循环自然退出 | 否（直接返回给用户） |

---

## 二、LoopGuard 详细机制

### 2.1 现有两个版本

| 特性 | Legacy LoopGuard (V1) | V3LoopGuard |
|---|---|---|
| 文件 | `src/core/loop_guard.py` | `src/core/loop_controller.py:288-366` |
| 当前状态 | **死代码** — 未被 `check()` 调用，仅用于 `record()` 兼容 | **主防御** |
| 匹配方式 | JSON 精确字符串匹配 (`canonicalize_args`) | 语义意图指纹 (`NormalizedIntent.to_key()`) |
| 滑动窗口 | `max_recent=3` | `max_recent=5` |
| 触发阈值 | `min_occurrences=2` | `min_occurrences=2` |
| 拦截后动作 | 返回拦截消息 | 拦截 + 注入 `LOOP_GUARD_PREVENTED` 到 CircuitBreaker + FailureMemory |
| 反思要求 | **有** — 强制 `<reflection>` 标签 | **无** — 简短消息，无反思要求 |

### 2.2 V3LoopGuard 触发条件

```python
# loop_controller.py:332-340
window = self.recent_intents[-self.max_recent - 1:-1]  # 最近 5 条（排除当前）
match_count = sum(1 for i in window if i.to_key() == intent_key)
if match_count >= self.min_occurrences:  # >= 2
    # → 拦截
```

- **跨轮次比较**：recent_intents 累积所有工具调用（不限 LLM 响应轮次），但窗口只有最近 5 条
- **语义匹配**：基于 `CommandNormalizer.normalize()` 生成的 `{tool}:{action}:{target}` 指纹
- **触发时机**：同一意图在最近 5 条调用中出现 ≥ 3 次（2 次匹配 + 当前第 3 次）

### 2.3 CommandNormalizer 归一化管道

```python
# loop_controller.py:66-281（五步管道）
1. shlex.split(cmd, posix=False) → 分词
2. _split_compound() → 在 &&/; 处分割为多个 segment
3. _find_action_segment() → 跳过 chcp/cd/set/pushd 开头 segment
4. _strip_redirect_tail() → 剥离 2>&1、> file、| type 等尾部标记
5. _classify_action() + _extract_target() → 生成 (action, target)
```

**归一化示例**：以下全部归一化为 `bash:EXECUTE:run_test.py`
- `python run_test.py`
- `cd /d X:\path && python run_test.py`
- `chcp 65001 > nul && set ENV=... && python -X utf8 run_test.py 2>&1`
- `pushd path && python run_test.py | type nul`

### 2.4 是否区分不同策略模式

**不区分。** `CommandNormalizer` 过于激进地归一化：
- `python -c "compile('a.py')"` 和 `python -c "compile('b.py')"` → 都归一化为 `EXECUTE::-c`
- 不同文件但同一操作模式 → 同一语义指纹 → 被误判为重复

这是已知问题（详见第七节）。

---

## 三、Circuit Breaker 详细机制

### 3.1 实现位置

`src/core/loop_controller.py:371-435` — `CircuitBreaker` 类

### 3.2 触发条件

```python
# 默认配置
STRIKE_LIMIT: int = 5

def register_failure(self, tool_name, args, failure_category):
    intent = CommandNormalizer.normalize(tool_name, args)
    key = intent.to_key()
    self._strikes[key] = self._strikes.get(key, 0) + 1
    total = self._strikes[key]
    if total >= self.STRIKE_LIMIT and key not in self._escalated:
        self._escalated.add(key)
        raise RuntimeEscalationException(...)
```

- **计数器范围**：按意图 key（`tool:action:target`）隔离
- **累计范围**：agent 实例全生命周期，**不随 task 切换重置**
- **两种失败都计数**：真实 `TOOL_CRASH` + 虚拟 `LOOP_GUARD_PREVENTED`
- **触发动作**：抛出 `RuntimeEscalationException`，在 `mini_claude_agent.py:940-963` 被捕获

### 3.3 捕获后的处理

```python
# mini_claude_agent.py:940-963
except RuntimeEscalationException as e:
    self.trace.record_circuit_breaker()
    self.trace.record_tool_call(..., circuit_breaker_triggered=True)
    self.trace.end_task("CIRCUIT_BROKEN")
    return str(e)  # 直接返回给用户，LLM 无感知
```

### 3.4 与 LoopGuard 的交互

```
工具调用前 → LoopController.check()
  ├─ V3LoopGuard.check_and_record() → 语义匹配
  │   ├─ 无匹配 → 返回 None → 工具被正常执行
  │   └─ 匹配 → 拦截 + CircuitBreaker.register_failure(LOOP_GUARD_PREVENTED)
  │                                     └─ strikes++ → >= 5？→ RuntimeEscalationException
  └─ 返回拦截消息或 None

工具执行失败 → FailureAnalyzer.analyze() → FailureMemory.record()
            → LoopController.register_failure(TOOL_CRASH)
                              └─ CircuitBreaker.register_failure()
                                  └─ strikes++ → >= 5？→ RuntimeEscalationException
```

---

## 四、Failure Intelligence 详细机制

### 4.1 组件结构

| 组件 | 文件 | 职责 |
|---|---|---|
| `FailureSignatureMatcher` | `failure_intelligence/signatures.py` | 正则匹配错误文本到 11 个预定义类别 |
| `FailureAnalyzer` | `failure_intelligence/analyzer.py` | 编排分析流程，输出 `FailureSignature` |
| `FailureMemory` | `failure_intelligence/memory.py` | 按 task_id 隔离的失败计数存储 |
| `FailureEscalationPolicy` | `failure_intelligence/policy.py` | 判定是否应升级（停止重试） |

### 4.2 触发条件

只要 `_is_tool_error()` 返回 True：
- 工具返回 `[Exit Code: N]` 且 `N != 0`
- 工具抛出异常被捕获为 `错误: ...`

### 4.3 失败分类

11 个 `FailureCategory`（`failure_intelligence/models.py`）：

| 类别 | 可恢复性 |
|---|---|
| `PERMISSION_DENIED` | USER_INTERVENTION_REQUIRED |
| `NETWORK_UNREACHABLE` | USER_INTERVENTION_REQUIRED |
| `TIMEOUT` | PARTIALLY_RECOVERABLE |
| `PACKAGE_NOT_FOUND` | USER_INTERVENTION_REQUIRED |
| `FILE_NOT_FOUND` | SELF_HEALABLE |
| `SYNTAX_ERROR` | SELF_HEALABLE |
| `COMMAND_NOT_FOUND` | USER_INTERVENTION_REQUIRED |
| `OUT_OF_MEMORY` | USER_INTERVENTION_REQUIRED |
| `DISK_FULL` | USER_INTERVENTION_REQUIRED |
| `TOOL_CRASH` | PARTIALLY_RECOVERABLE |
| `UNKNOWN` | UNKNOWN |

### 4.4 升级判定规则

```python
# policy.py
SAME_CATEGORY_ESCALATION = 5    # 高水位：5次无条件升级
SAME_CATEGORY_HIGH_WATER = 3    # 低多样性触发：3次且多样性<2
MIN_STRATEGY_DIVERSITY = 2      # 最低策略多样性要求
```

三条规则（任一满足则升级）：
1. **高水位规则**：同一类别失败 ≥ 5 次 → 无条件升级
2. **低多样性规则**：同一类别失败 ≥ 3 次 + 策略多样性 < 2 + 类别属于 `USER_INTERVENTION_CATEGORIES` → 升级
3. **不可恢复规则**：`recoverability == NON_RECOVERABLE` → 立即升级

### 4.5 升级后的反馈

```python
f"[系统级 Escalation — Failure Intelligence]\n"
f"类型: {category}\n"
f"可恢复性: {recoverability}\n"
f"根因: {root_cause}\n\n"
f"同一类型失败已发生 {count} 次 ({category})，\n"
f"Runtime 判定继续重试无效\n\n"
f"建议操作:\n  → 请用户检查环境配置或网络设置\n"
f"  → 或提供新的操作指令绕过此问题"
```

---

## 五、其他保护机制

### 5.1 强制反思节点

**仅存在于 Legacy LoopGuard（`loop_guard.py:24-45`），V3 中已被移除。**

Legacy 版本被 block 后，LLM 收到消息要求：

```
你必须在下一次回复中最先输出一个 <reflection> 标签，
在其中深刻分析：
  1. 为什么之前的尝试反复失败？
  2. 当前策略的根本问题是什么？
  3. 有哪些与之前完全不同的替代方案？
```

**现状**：V3 `_build_block_message()`（`loop_controller.py:350-361`）不包含任何反思要求。由于 V3 的 `check()` 优先于 Legacy 调用，Legacy 的反思提示永远不会被执行。

### 5.2 max_iterations 硬上限

- 位置：`mini_claude_agent.py:778` — `def _llm_tool_cycle(self, max_iterations: int = 35)`
- 触发：for 循环迭代 35 次后自然退出
- 处理：`final_status = "LOOP_ABORTED"`，返回 `"错误: 工具执行次数过多，已自动终止。"`

### 5.3 总 Token / 时间限制

**两者均不存在。** `last_metrics["total_tokens"]` 仅用于统计，无检查逻辑。唯一的超时是 ShellSession 单次命令执行的 `timeout=120s`。

### 5.4 相同错误消息重复检测

通过 Failure Intelligence 实现：
- `FailureMemory` 按 task_id 记录各类失败的发生次数和策略多样性
- 但这不是"相同消息文本"的比较，而是类别 + 策略指纹的匹配

缺少基于错误文本 hash 的精确去重。

### 5.5 同轮去重

- 位置：`mini_claude_agent.py:869-884`
- 条件：同一 LLM 响应内，`(tool_name, args_raw)` 精确匹配
- 动作：直接 `continue`，静默跳过，**无任何反馈给 LLM**

---

## 六、配置参数汇总

| 参数 | 默认值 | 位置 | 说明 |
|---|---|---|---|
| `max_iterations` | 35 | `mini_claude_agent.py:778` | 单次 `_llm_tool_cycle` 最大迭代 |
| `LoopGuard.max_recent` | 3 | `loop_guard.py:50` | Legacy: 滑动窗口大小 |
| `LoopGuard.min_occurrences` | 2 | `loop_guard.py:50` | Legacy: 触发阈值 |
| `V3LoopGuard.max_recent` | 5 | `loop_controller.py:298` | V3: 滑动窗口大小 |
| `V3LoopGuard.min_occurrences` | 2 | `loop_controller.py:298` | V3: 触发阈值 |
| `CircuitBreaker.STRIKE_LIMIT` | 5 | `loop_controller.py:381` | 硬断路器累计失败阈值 |
| `SAME_CATEGORY_ESCALATION` | 5 | `policy.py` | FI 高水位升级阈值 |
| `SAME_CATEGORY_HIGH_WATER` | 3 | `policy.py` | FI 低多样性升级阈值 |
| `MIN_STRATEGY_DIVERSITY` | 2 | `policy.py` | FI 最低策略多样性 |
| `Compression.token_threshold` | 100000 | `compression.py` | 压缩触发阈值 |
| `ShellSession.execute().timeout` | 120 | `shell_session.py` | 单条命令超时 |

> 注意：没有集中配置入口。各参数分散在各自类的构造函数或类变量中。

---

## 七、已知问题与限制

### 7.1 核心问题：`python -c` 跨文件误杀

**Trace 证据**：`v6.5_fix_read_file/trace_task_006_cross_file_drift_r02.json`

- 连续对 `db.py`、`service.py`、`controller.py` 分别执行 `python -c "py_compile.compile(...)"`
- 前两个成功，第三个被 LoopGuard 拦截（同一轮内 3 次 `EXECUTE::` 触发阈值）
- 损失：额外 1 轮 + ~8,452 tokens 写临时脚本绕过

**根因**：`CommandNormalizer` 将 `python -c "compile('a.py')"` 和 `python -c "compile('b.py')"` 都归一化为 `EXECUTE::-c`。目标文件信息在 `-c` 参数中被丢弃。

### 7.2 V3 缺失反思机制

- Legacy LoopGuard 有强制 `<reflection>` 标签，V3 移除了
- V3 的拦截消息简短且无指导性
- ADR-013 记录了此问题："错误消息无指导性"

### 7.3 计数器永不重置

- `LoopController.clear()` 和 `CircuitBreaker.reset()` 存在但从未被调用
- 同一 agent 实例的多次对话间，断路器计数器持续累积
- 但每次 `_llm_tool_cycle` 调用会通过 `failure_memory.set_task(tid)` 重置 FI 计数

### 7.4 阈值未收敛

- ADR-013 指出 "N=2 vs N=3 的阈值选择未收敛"
- v6.5 的 r01/r02/r03 三次运行中，同一任务的 loop_guard_trigger_count 分别为 0、3、0
- 高敏感度导致因 LLM 随机性而触发不稳定

### 7.5 改进方向

基于 ADR-013 遗留问题和历史 trace 分析：
1. **白名单/豁免机制**：允许对 `python -c` 特定模式或已知合法操作免除拦截
2. **N=2→N=3 阈值提升**：降低误杀率（ADR-013 明确建议）
3. **V3 拦截消息增加反思要求**：复用 Legacy 的 `<reflection>` 提示模板
4. **跨次对话计数器重置**：`LoopController.clear()` 应在每次 `_llm_tool_cycle` 开始时调用
5. **集中配置入口**：将所有参数收敛到可配置的配置对象中

---

## 八、Trace 中的循环防护字段

`src/core/tracing/models.py` 中与循环防护相关的字段：

| 字段 | 位置 | 说明 |
|---|---|---|
| `ToolTrace.loop_guard_blocked` | `models.py:33` | 本次调用是否被 LoopGuard 拦截 |
| `ToolTrace.circuit_breaker_triggered` | `models.py:42` | 是否触发了硬断路器 |
| `ToolTrace.failure_category` | `models.py:36` | 失败类别（TOOL_CRASH / LOOP_GUARD_PREVENTED 等） |
| `TaskTrace.loop_guard_trigger_count` | `models.py:108` | LoopGuard 累计触发次数 |
| `TaskTrace.circuit_breaker_trigger_count` | `models.py:110` | 断路器累计触发次数 |
| `TaskTrace.compression_count` | `models.py:106` | 上下文压缩次数 |
| `TaskTrace.reflection_count` | `models.py:109` | 强制反射输出次数 |
| `TaskTrace.rollback_count` | `models.py:107` | 事务回滚次数 |
| `TaskFinalStatus.CIRCUIT_BROKEN` | `models.py:19` | 熔断终态 |
| `TaskFinalStatus.LOOP_ABORTED` | `models.py:18` | 迭代上限终态 |

---

## 九、各版本 Trace 对比数据

基于 `sandbox/eval_results/` 目录下 19 个 trace 文件的统计：

| 版本区间 | 总 trace 数 | LOOP_ABORTED 占比 | CIRCUIT_BROKEN 占比 | LoopGuard>0 占比 |
|---|---|---|---|---|
| v2~v4（前期） | 7 | 43% | 0% | 57% |
| v5~v6.5（后期） | 12 | 0% | 0% | 58% |
| **总计** | **19** | **3 例** | **1 例** | **11 例 (58%)** |

关键转折点：v5（edit_file 批量事务化）将 task_006 从 LOOP_ABORTED（35 轮/289K tokens）变为 SUCCESS（20 轮/137K tokens），验证了减少工具调用次数是降低 LoopGuard 误伤的最有效手段。

---

## 十、总结

当前项目的防死循环架构是一个从软到硬的七层防御体系，核心价值在于 V3 的意图归一化（`CommandNormalizer`）使得语义级重复检测成为可能，而硬断路器（`CircuitBreaker`）提供了无法被 LLM 忽略的终止手段。主要已知问题集中在 `python -c` 类操作的误杀、V3 缺失反思机制、以及阈值参数未经系统调优。
