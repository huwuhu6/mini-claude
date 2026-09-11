# 上下文管理演进记录

这份文档记录读取输出、对话压缩和动态上下文注入对 Agent 成本与稳定性的影响。

## 1. read_file 输出信息量

### 尝试

在 `b3f9e71` 中为 `read_file` 增加行号和上下界，目的是帮助 Agent 精确编辑并减少重新定位。

### 反效果

行号会增加每次读取的上下文长度。提交记录显示，该方案带来了 Token 和执行轮数上涨，尤其在需要多次读取的任务中成本明显。

### 最终取舍

在 `ee24a6b` 中移除默认行号输出，保留按需指定行范围的能力。默认返回更紧凑的代码内容，需要精确定位时再显式请求范围。

## 2. 对话压缩与 Stub 替换

### 问题

完全保留历史会持续增加 Token；简单截断又可能丢失任务目标、关键工具结果和失败上下文。

### 当前方案

在 `4d8cc16` 中引入基于 Token 阈值的分层压缩，并使用 Stub 替换部分已经完成或低价值的历史内容，尽量保留结构而不是保留全部原文。

## 当前结论

上下文管理的目标不是单纯压缩 Token，而是在成本、记忆完整性和重复读取之间取得平衡。任何压缩策略都必须同时观察任务成功率和重复工具调用。

## 2026-09-11

Commit: `402f474`
Commit Description: `fix(context): 修复上下文压缩与工具输出基础缺陷`

### Description

本阶段修复了 Context Management 的基础正确性问题：Token 估算补齐持久化的 `tool_calls`，微压缩不再凭工具名猜测成功，Full Compression 的 tail 边界不再切断 Tool Call / Tool Result Chain，`read_file` 增加统一的行数、字符数和字节数硬上限，并清理无效的 `microcompact_threshold` 配置。自动微压缩只有实际改变消息时才会被视为触发。

### Result / Evidence

新增 deterministic regression tests 覆盖上述不变量；Context Foundation 测试 15 passed，受影响的 unit/integration 测试共 190 passed。未运行真实 Provider Benchmark。

### Decision / Limitation

本阶段保留现有 0.7 micro-compaction 阈值和统计摘要策略，没有引入 Provider-aware Budget、Project Context、长期 Memory、Value-aware Retention 或新的 Summary 架构。实现 Commit Hash 已在后续 docs 提交中回填。

## 2026-09-11

Commit: `08fd7cf`
Commit Description: `fix(context): 修复压缩事务与跨进程 Transcript retention`

### Description

本阶段确认并修复两类可靠性错误。配置了 Summary Provider 时，Provider 异常、返回空内容或返回不可解析结果不再降级为统计摘要并覆盖原历史；Full Compression 只有在摘要有效且候选消息构造完成后才写入 Transcript。未配置 Provider 时仍保留原有 deterministic 统计摘要路径。

Transcript retention 现在会在 Compressor 初始化时加载目录中的有效 `transcript_*.json`，因此新进程创建 Transcript 时也会把上一进程留下的记录纳入 retention 排序和淘汰。损坏的 Transcript 文件被跳过，非 Transcript 文件不参与删除。

### Result / Evidence

新增 deterministic regression tests 覆盖 Provider 失败关闭、自动压缩不误报、统计摘要兼容、跨进程加载、最旧记录淘汰、损坏文件和无关文件保护；本轮相关测试通过。未运行真实 Provider Benchmark。

### Decision / Limitation

根据当前项目主线，本轮不修改 Anthropic 等非 OpenAI-compatible Provider 适配，不实现 Hot Context peek/deliver/ack，也不修改 MessageBus、Inbox、Team 或 Background 投递语义。CTX-006 留作 follow-up；Provider-aware Budget、Summary 架构和长期 Memory 同样不在本轮范围内。
