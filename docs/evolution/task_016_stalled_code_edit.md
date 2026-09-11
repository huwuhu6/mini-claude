# task_016_stalled_code_edit

## 目标

验证 Agent 在局部编辑持续失败且工作区没有实际状态变化时，运行时是否会在有限轮次内触发硬熔断，而不是继续修改同一个文件。

## Fixture

`baseline/math_utils.py` 保留两段完全相同的 `divide_numbers` 函数代码。针对该函数的全局精确替换会出现多处匹配，`edit_file` 因此返回失败；`calculate_ratio` 是独立的正常业务函数，用于检查无关代码没有被误伤。

## 运行时行为

`CommandNormalizer` 仍然保留不同编辑载荷的意图区分。`CircuitBreaker` 在登记 `edit_file` 失败时按目标文件归并失败次数，防止 Agent 通过微调 search 字符串绕过同一文件的熔断预算。

## 验证口径

`verify.py` 读取当前任务 Trace，要求在 8 轮内记录至少 3 次失败的 `edit_file` 调用、出现 Loop Guard 或 Circuit Breaker 干预，并以 `CIRCUIT_BROKEN` 结束。同时确认重复函数和原始除法实现仍在工作区，说明没有成功写入或伪造修复。
