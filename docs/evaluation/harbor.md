# Harbor 评测接入

Harbor 负责下载公开任务、创建隔离容器、执行 verifier 和保存 reward；MiniClaude 只接收任务指令与容器工作目录，在容器内运行自己的工具和 Trace。适配器不解释 Terminal-Bench 或其他数据集的成功标准。当前固定 Harbor `0.23.0`；更换版本前应复核 custom installed agent API。

## 准备

在本分支的干净 checkout 中安装 Python 3.12+、Docker Desktop（Linux containers）和 Harbor：

```powershell
py -m pip install -r benchmark/harbor/requirements.txt
harbor --version
docker info
```

Harbor host Python 与容器 Python 是两个环境。适配器从当前 checkout 构建 MiniClaude wheel，上传 wheel 与 `configs/default.yaml`，在任务容器的 `/installed-agent/mini-claude-venv` 中安装依赖。优先使用容器自带的 Python 3.10+；缺失时通过官方安装器固定的 uv `0.12.17` 安装 Python 3.12。容器需要包索引访问，缺失 Python 时还需要下载 uv/Python。MiniClaude 的 workspace 使用 Harbor task 的 `workdir`，未声明时读取容器的 `pwd`。不挂载或发送宿主项目工作区。

默认配置使用 DashScope `deepseek-v4-flash-0731`。在 Harbor 进程的环境中设置 `DASHSCOPE_API_KEY`；适配器通过 Harbor 的 agent 阶段环境传给容器，不把密钥写入命令、配置、fixture 或 Trace。不要把密钥放到仓库文件或评测命令参数中。Harbor 官方的 agent 环境变量文档解释了各阶段的隔离边界。

## 逐级 smoke

先用 Oracle 验证 Harbor、Docker、Terminal-Bench 2.0 下载与 verifier 链路，仅跑一个任务：

```powershell
harbor run -d terminal-bench/terminal-bench-2 -a oracle -l 1 -n 1 -o benchmark/harbor/jobs --job-name oracle-smoke
```

从 Oracle job 的结果中记录实际 task name。仅当 Oracle 有有效 verifier 结果后，用相同 task name 运行 MiniClaude 一次：

```powershell
harbor run -d terminal-bench/terminal-bench-2 -i <full-task-name> -a benchmark.harbor.agent:MiniClaudeHarborAgent -l 1 -n 1 -o benchmark/harbor/jobs --job-name mini-claude-smoke
```

`-i` 必须使用完整名称，例如 `terminal-bench/make-mips-interpreter`，短名 `make-mips-interpreter` 不匹配 Harbor 的过滤器。首次链路完整后，可移除 `-i` 并改为 `-l 3` 做稳定性 smoke。不要直接执行整个数据集。`-m` 不控制 MiniClaude 模型；模型与 Provider 由上传的 `configs/default.yaml` 决定。

Harbor jobs、trial/verifier 日志与 reward 位于 `benchmark/harbor/jobs/`；MiniClaude 原生 Trace 位于每个 trial 的 `agent/mini-claude/traces/`，执行摘要位于 `agent/mini-claude-result.json`。Trace 包含轮次、工具调用、Token、缓存和压缩数据；read 重叠指标若当前分支未提供，则不能在报告中伪称已采集。Harbor verifier 的 reward 是任务结果的唯一判定来源，MiniClaude 的 `final_status` 只表示其运行循环如何结束。

Windows PowerShell 若使用 GBK 控制台，应在运行前设置 `$env:PYTHONUTF8 = '1'`，避免 Harbor Rich 输出中的字符编码异常。Docker 镜像拉取失败属于 Harbor/Host 环境故障，应先解决并通过 Oracle smoke，再启动真实 Provider 任务。

已观察到 USTC registry mirror 在拉取 `alexgshaw/make-mips-interpreter:20251031` 时返回 EOF，而 Docker Hub 经本机代理可达。仅针对该镜像，可用 `docker pull registry-1.docker.io/alexgshaw/make-mips-interpreter:20251031` 后再 `docker tag registry-1.docker.io/alexgshaw/make-mips-interpreter:20251031 alexgshaw/make-mips-interpreter:20251031`，避免改动 Docker Desktop 全局配置。该方式要求代理/网络本身可用；不要将其视作所有任务镜像的通用解决方案。

以后切换其他 Harbor dataset 时更改 `-d` 与任务过滤条件即可；适配器和 headless runner 无需修改。SWE-bench 的预测导出和官方推理流程属于后续工作。
