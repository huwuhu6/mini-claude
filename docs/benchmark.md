# 📊 基准评测实验对照账本 (Benchmark Registry)

本文件用于持久化记录 Mini-Claude 运行时（Runtime）在面对复杂环境退化（Degradation）与死循环时的控制层防御过程指标。通过固定 Baseline（A组）与新策略（B组）进行对照实验。

---

## 🖥️ 评测物理环境基准 (Environment Context)
- **OS**: Windows 11 Pro (23H2)
- **Terminal Shell**: CMD (Non-TTY non-interactive pipeline)
- **System Default Encoding**: CP936 (GBK)
- **Test Baseline Target**: `sandbox/tasks/task_001_db_port` (多文件跨模块自愈测试)

---

## 🏁 实验对照数据总览 (A/B Testing Metrics Summary)

| 实验组别 | 核心防线策略 (Defense Strategy) | 最终任务状态 (Final Status) | 客观物理判定 (Verify Script) | 总耗费轮数 (Total Turns) | 全局 Token 消耗 (Total Tokens) | 拦截器防漏风率 (Guard Block Rate) | 综合行为退化分 (Degradation Score) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **A 组 (旧)** | V2: Failure Intelligence (Soft Escalation) | `LOOP_ABORTED` (轮数耗尽) | ✅ PASS (Dirty Pass) | 35 Turns | 196,383 Tokens | 0.0% (哈希完全逃逸) | 71.0 (高频压缩/行为严重劣化) |
| **B 组 (新)** | V3: 意图归一化 + 守卫报警链 + 硬断路器 | `CIRCUIT_BROKEN` (受受控熔断) | ✅ PASS (State Leak) | 20 Turns (↓ 42.9%) | 62,458 Tokens (↓ 68.2%) | **100.0%** (意图完美收敛) | 16.0 (提早熔断止损) |

---

## 🔍 微观 Trace 行为演进审计 (Micro-Trace Audit)

### 1. A 组现场复盘 (v2_baseline_fi_soft 归档日志分析)
- **归档路径**: `sandbox/eval_results/v2_baseline_fi_soft/task_001_db_port_v2_trace.json`
- **行为退化特征**: 
  - Agent 在 Turn 2 通过 `edit_file` 精准修对了代码。但在 Turn 3 运行验证脚本时，遭遇 Windows 管道 GBK 编码越界死锁，脚本抛出 `UnicodeDecodeError`。
  - 由于旧 `LoopGuard` 仅校验字面量参数哈希，大模型在随后 32 个回合中通过添加环境变量、切换目录前缀（如 `cd /d`）等方式不断微调 CLI 语法，成功逃逸拦截。
  - 触发了 22 次高频上下文压缩，引发大模型“运行时失忆”，开始回头重复已失败的指令。最终虽由于物理文件被改对而通过 `verify.py`，但白白烧毁了近 20 万 Token。

### 2. B 组现场复盘 (v3_circuit_breaker 归档日志分析)
- **归档路径**: `sandbox/eval_results/v3_circuit_breaker/task_001_db_port_v3_trace.json`
- **行为自愈特征**:
  - 引入 `CommandNormalizer` 后，大模型微调的 `chcp 65001 && python run_test.py` 等马甲命令被精准脱敏剥离，统一收敛为标准意图特征 `{action: EXECUTE, target: run_test.py}`。
  - `LoopGuard` 成功在语义层面捕捉到模型的复读倾向，并在拦截当轮向 `FailureMemory` 强制上报 `LOOP_GUARD_PREVENTED` 虚拟失败，打通控制层盲区。
  - 意图指纹连续触顶 5 次后，Runtime 物理拉响硬断路器，抛出 `RuntimeEscalationException` 强行断电，成功在第 20 轮终止了 Token 暴风雨，为系统净节省 **133,925** 个 Token。

---

### 3. C 组现场复盘 (v5_enhanced_edit_file)

- **归档路径**: `sandbox/eval_results/v5_enhanced_edit_file/`
- **评测用例**: task_005_non_unique_context, task_006_cross_file_drift
- **核心变化**: `edit_file` 从单次 old_text/new_text 升级为批量 `edits` 数组 + 事务性回滚 + 符号归一化兜底

#### task_006_cross_file_drift（跨文件重构）

| 指标 | v4（旧） | v5（批量编辑） | Δ |
|------|---------|---------------|---|
| **验证结果** | SUCCESS | SUCCESS | — |
| **最终状态** | LOOP_ABORTED (轮数耗尽) | SUCCESS | ✅ |
| **总轮次** | 35 | 20 | 🔻42.9% |
| **总 Token** | 289,110 | 137,567 | 🔻52.4% |
| **编辑阶段** | 7 次单次 edit_file → 被 LoopGuard 误伤 | 2 次批量 edits → 无重复 | ✅ |
| **LoopGuard 触发** | 4 次（含编辑误伤） | 2 次（仅限 bash） | 🔻50% |
| **压缩次数** | 21 | 11 | 🔻47.6% |

### 4. D 组现场复盘 (v6_enhanced_read_file)

- **归档路径**: `sandbox/eval_results/v6_enhanced_read_file/`
- **评测用例**: task_006_cross_file_drift
- **核心变化**: `read_file` 新增 `start_line`/`end_line` 参数，返回行前缀行号 + 元数据头

#### 结果：Token 大幅反弹，效果不及预期

| 指标 | v5（批量编辑） | v6（+局部视窗读取） | Δ |
|------|--------------|-------------------|---|
| **验证结果** | SUCCESS | SUCCESS | — |
| **最终状态** | SUCCESS | SUCCESS | — |
| **总轮次** | 20 | 34 | 🔺70% |
| **总 Token** | 137,567 | 300,242 | 🔺118% |
| **总延迟** | 54.8s | 109.4s | 🔺99.6% |
| **read_file 调用** | 8 | 19 | 🔺137% |
| **每轮 Token** | 6,878 | 8,831 | 🔺28.4% |
| **压缩次数** | 11 | 25 | 🔺127% |
| **工具命中率** | 92% | 95% | 🔺3pp |

#### 回归根因分析

1. **行号前缀膨胀**：每行前缀 `f”{line_num:4d} | “` 增加约 6-7 字节/行，导致 read_file 返回体量增加约 15-20%。
2. **调用次数激增**：局部视窗让 LLM 倾向于”小窗多次”读取（8 次 → 19 次），每次返回仍包含行号前缀，Token 总量叠加放大。
3. **压缩频率上升**：更大的上下文触发更频繁的自动压缩（11 次 → 25 次），压缩本身消耗 Token，形成负反馈循环。

#### 经验教训

**单纯的读取侧增强不一定会降低 Token 成本，甚至可能适得其反。** 局部视窗工具暴露给 LLM 后，LLM 倾向于更细粒度地”窥探”文件，调度次数和单次返回体积同时增加。减少 Token 的关键在于**减少 LLM 调用工具的决策次数**（如批量编辑将 7 次编辑压到 2 次），而不是优化单次返回的 Token 效率。

---

## 📌 后续演进路线 (Next Steps Log)
1. **[P0] 物理状态泄漏修补**：已修复，task_001 已正确返回 FAIL(CIRCUIT_BROKEN)
2. **[P2] 跨语言正则扩展**：扩充 CommandNormalizer 支持非 Python 生态工具链
3. **[P1] 防死循环粒度过粗** — 识别合理重复模式（如 python -c 批处理）
4. **[P1] 拦截阈值自适应** — 基于历史行为动态调节 N 值
5. **[P1] read_file 局部视窗收益重验证** — 行号前缀 + 小窗多次导致 Token 反弹 118%，需探索调用频次限制或连续读取合并缓存
6. **[P2] 错误消息增强** — 拦截时给出可操作建议
7. **[P2] 白名单/豁免机制** — 允许模型注册预期内重复行为