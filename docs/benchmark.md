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

## 📌 后续演进路线演进 (Next Steps Log)
1. **[P0] 物理状态泄漏修补**：需要让主任务推理循环也能捕获 `RuntimeEscalationException`，并强行联动影子沙箱执行回滚，阻止“被熔断的任务却由于原地直写通过了客观校验”的状态漂移 Bug。
2. **[P2] 跨语言正则扩展**：根据 DeepSeek Review 结论，后续需扩充 `CommandNormalizer` 的正则规则以支持非 Python 生态工具链（Roadmap 同步登记）。