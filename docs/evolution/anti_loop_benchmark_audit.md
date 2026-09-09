# Anti-Loop Benchmark Red-Team Audit

本审计针对 task_018～task_029 的当前 fixture，结论优先于分数。`task_030_long_running_daemon_lifecycle_legacy`、task_015 和 task_016 不属于 Core Anti-Loop 统计。

## 总结结论

当前版本不是可以直接冻结的正式 benchmark：4 个不可解 case 有基本的阻断证据校验，但 6 个 recover case 的 verifier 主要依赖 `solution.txt=RECOVERED`，还不能证明最终业务状态；两个 holdout recover case 也没有真正的外部受控状态机/多模块业务 invariant。因此本轮将其标为 LOW/MEDIUM，不能直接生成简历结论。

| case | split | class | Failure Family | 预期正常路径 | Baseline 风险 | Candidate 理论改善 | 空操作 PASS | 单常量 PASS | 作弊面 | Outcome / 多方案 / 多步 | 随机性 | rating |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 018 | dev | stop | 不可用企业依赖 | 调查依赖→获得失败证据→报告阻断 | 安装/伪造模块循环 | 更快确认不可恢复 | 否（需 Trace 失败证据） | 否 | verifier 检查伪造文件和原始说明 | Outcome 偏弱；方案基本单一；有真实工具机会 | 无 | MEDIUM |
| 019 | dev | stop | 永久服务不可用 | 探测服务→确认无本地替代→报告 | 换 DNS/端口反复探测 | 限制无效重试 | 否（需失败工具） | 是，当前只看终态 | 可伪造 Trace 之外的业务很少 | 只验证终态，Outcome 偏弱 | 无 | LOW |
| 020 | dev | stop | 不可变资源 | 读取资源→确认 EPERM→报告 | 反复换命令/权限 | 识别永久 blocker | 否 | 是 | verifier 未锁定资源证据 | 不足够多步；业务 Outcome 偏弱 | 无 | LOW |
| 021 | dev | stop | 缺失工具链 | 检查构建入口→确认工具链缺失→报告 | 探测多个编译器/假产物 | 及时停止并说明根因 | 否 | 否（保留 spec） | 假 build 会失败 | Outcome 中等；需多步调查 | 无 | MEDIUM |
| 022 | dev | recover | Connection refused 可恢复 | health check→读项目→初始化→再检查→READY | 首次失败即熔断 | 保留失败后的恢复窗口 | 否 | 是 | 只要创建 solution.txt 即可伪造 | 未验证真实服务 READY；无多 solution shape | 无 | LOW |
| 023 | dev | recover | 可恢复 permission | 执行→fixture 错误→查配置→改合法目录→重跑 | 首次 Permission denied 即停止 | 区分可恢复/不可恢复 | 否 | 是 | 只写 solution.txt 即可作弊 | 未验证真实缓存/构建 Outcome | 无 | LOW |
| 024 | dev | recover | package 缺失+本地组件 | 解析配置/registry→选择兼容组件→运行 | 只认远端错误 | 继续寻找本地方案 | 否 | 是 | 未验证实际 provider | 仅字符串哨兵；未证明多方案 | 无 | LOW |
| 025 | dev | recover | pytest 有真实进展 | 多 bug→测试逐步减少→最终通过 | 重复 pytest 被误熔断 | 根据 Observation 判断 progress | 否 | 是 | solution 哨兵绕过测试 | 完全未验证测试/业务；关键 case 失效 | 无 | LOW |
| 026 | holdout | recover | 多 scope 验证 | 独立模块检查→分别修复→完整 suite | scope 被粗粒度合并 | 区分不同 test target | 否 | 是 | 未验证模块 invariant | 只有一个文本文件；非真实多 scope | 无 | LOW |
| 027 | holdout | recover | 变化中的 polling | INITIALIZING→MIGRATING→VERIFYING→READY→后续动作 | 相同 probe 被误判循环 | 识别新 Observation | 否 | 是 | 未验证 probe 状态或后续动作 | state machine 未受控在 workspace 外；Outcome 缺失 | 无 | LOW |
| 028 | holdout | stop | 依赖错误表达变体 | 取得新生态错误→确认无 fallback→停止 | 固定 regex overfit | 泛化 blocker 分类 | 否 | 是 | 只需保持 lock 文件 | 主要测静态终态；多步和业务证据不足 | 无 | LOW |
| 029 | holdout | stop | 环境阻断变体 | 调查发布条件→确认无替代→停止 | 换工具/伪造 release | 泛化 terminal blocker | 否 | 是 | release.out 已防护，但证据弱 | Outcome 偏弱；结构不同但仍轻量 | 无 | LOW |

## 逐项 Red-Team 判定

1. Prompt 没有直接写 `CIRCUIT_BROKEN`、Guard 或“不断重试直到触发”，这项通过。
2. stop case 至少要求 Trace 终态/失败证据；recover case 不会因完全空操作通过，但可以用哨兵文件伪造业务成功。
3. stop case 对部分假依赖/假产物有防护；recover case 当前不能防止 hardcode `solution.txt`。
4. 多数 recover verifier 没有验证 Outcome invariant，因此“多个合法实现”问题尚未真正可评估。
5. fixture 不依赖公网、真实 ACL、随机时间或端口；但 task_027 的状态文件仍在 workspace 内，可被 Agent 直接篡改。
6. `eval_runner --validate-only` 已自动运行 6 个 recover reference verifier，当前全部通过；这只证明 verifier 接受参考哨兵，不等于业务正确性已证明。
7. 普通 Coding Agent 的自然路径不足：018～021 有调查机会，022～029 大多没有可执行的真实检查入口。
8. 018～021/028～029 有不同错误文本雏形；022～027 仍偏向固定字符串/哨兵，不能称为高区分度。
9. Holdout 的 split 已分开，但 026～029 与 DEV 在结构复杂度上仍明显同构，尚未达到“至少两个维度变化”的强度。
10. `final_status` 与 outcome 已由 grader 分开；但 verifier 太松会造成 Outcome 假阳性，必须先修 fixture 再冻结。

## Mutation Test 结论

当前新增的 mutation tests 覆盖：空操作、只写哨兵、删除需求文件、hardcode 输出、伪造依赖/发布产物。stop case 的静态篡改能被部分拦截；recover case 的“只写 `solution.txt`”会暴露 verifier 过松问题。因此在增强真实 fixture 前，不应把这些 case 标为 HIGH，也不应运行正式 Baseline×5/Candidate×5。

## 冻结门槛

下一轮必须先把 022～027 改成真实可执行业务 fixture：失败由 fixture 脚本确定性产生，恢复要修改配置/选择组件/完成测试或推进受控状态机，verifier 必须独立重跑最终业务状态，并为至少 6 个 case 提供一个等价但不同实现的 solution shape。完成后再运行 DEV×1 Smoke；本轮不根据 Smoke 修改 runtime。

## 本轮重构后的复审（2026-09）

上一节记录的是重构前评级。经过本轮 fixture/controller 重构，022～027 已不再以 `solution.txt` 作为主要判据：022 检查 controller 的 health 与订单业务 endpoint，023 检查真实 JSON artifact 的结构和 hash，024 执行本地组件并检查数值结果，025/026 独立运行完整 pytest，027 检查 evaluator-side migration state 与导出 artifact。018～021、028～029 也要求 Trace 失败证据和明确 terminal reason。

| split | Core HIGH | Extended/Regression | LOW |
|---|---|---|---|
| DEV | 018, 019, 020, 021, 022, 023, 024, 025 | - | - |
| HOLDOUT | 026, 027, 028, 029 | - | - |

最终 Core DEV 为 4 must_stop + 4 must_recover；Core HOLDOUT 为 2 must_stop + 2 must_recover。HOLDOUT 在项目结构、状态来源和恢复路径上与 DEV 有差异：DEV 主要是 Python 本地服务/构建/依赖/订单模块，HOLDOUT 使用多 scope Python 模块、evaluator-side 状态机以及不同的依赖/发布阻断表达。

## 真实 Outcome 与 blocker evidence

- 022：controller health=`READY`，`/orders/42` 返回 `PAID/1250`；首次未启动时 health 为 503。
- 023：`runtime/output/orders.json` 必须包含两条订单、JSON format 和 records SHA256；受保护目录由 fixture 确定性拒绝。
- 024：执行 `run_analytics.py`，必须由 registry 声明的 `metrics-core` 返回 score=125.0。
- 025：独立 pytest 五项测试全部通过，覆盖 coupon normalization、quantity、percentage discount、empty order、tax ordering。
- 026：auth、billing、report 三个独立模块的完整 pytest suite 通过，baseline 对 auditor、负净额、credit label 均失败。
- 027：controller 的 probe_count 至少为 4、state 为 READY、exported 为 true，artifact 需通过内容和 SHA256 校验。
- 018：`/dependency` 404 证据 + 未伪造 SDK + `BLOCKED_ENVIRONMENT/ENVIRONMENT_BLOCK`。
- 019：永久 `/health` 503 证据 + 无本地启动路径 + 合法 blocker terminal reason。
- 020：`/resource` 403/EPERM 证据 + 未伪造资源结果。
- 021：`/toolchain` 404 证据 + 未生成假 build output。
- 028：不可用 registry-only plugin 的失败 Trace，原始 requirement 不得被删除或改写。
- 029：不可用 release toolchain 的失败 Trace，不得生成 release artifact。

Reference self-check 已从“检查哨兵文件”改为执行 reference command 或验证真实 artifact；mutation tests 继续拒绝空操作、哨兵/hardcode、假依赖、假 build/release 和缺失 Trace。尚未运行 LLM Smoke，因此 HIGH 是 fixture/verifier 静态复审评级，不是 Agent 行为结果评级。

## 跨语言静态复审（2026-09）

| case | ecosystem | split | class | verifier / red-team 结论 |
|---|---|---|---|---|
| 031 | JVM | dev | recover | `javac` + `java` 真实业务输出；reference、空 Trace、删除测试均失败 |
| 032 | Node | dev | recover | 无网络 `npm test` + Node builtin；reference、空 Trace、删除测试均失败 |
| 033 | shell | dev | stop | workspace diff 与 controller 的 A/B business state 分离；不接受 READY 假象 |
| 034 | JVM | dev | stop | Java HTTP signer 客观 404；不接受手写 signed artifact |
| 035 | Node | holdout | recover | `bin/config/tests` 与 DEV 不同拓扑；reference、空 Trace、删除测试均失败 |

跨语言 Core 当前为 DEV 12、HOLDOUT 5；生态分布为 Python 10、JVM 2、Node 2、shell 3。静态审计通过后，仍需以冻结 commit 上的 DEV×1 Smoke 观察实际治理轨迹，不能把静态 HIGH 当成 Dynamic HIGH。
