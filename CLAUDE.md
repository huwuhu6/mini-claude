# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## Documentation Map (CRITICAL)

当需要理解系统设计或历史决策时，严禁盲目扫描整个项目。请优先读取以下精简文档：

- **当前架构、入口和运行方式**: [README.md](README.md)
- **技术演进历史**（防重复调用、FI 发展史）: [docs/evolution/tool_deduplication.md](docs/evolution/tool_deduplication.md)

## Language Rules

- **所有日志输出必须使用中文** - 包括但不限于：logger 日志信息、print 输出、控制台提示、用户交互信息、错误消息等。
- **代码注释**不受此限制，可以根据需要继续使用英文。
- **变量名、函数名、类名**应保持英文命名规范。

## Python 版本说明

- **使用 `py` 命令代替 `python`**
- `py` 命令使用 Python 3.14.3（高版本，SSL 兼容性好）
- `python` 命令使用 Python 3.8.6（低版本，存在 SSL/TLS 连接问题）
- 所有运行、测试命令都应使用 `py` 而非 `python`

## Quick Start

```bash
pip install -e .
mini-claude [path] [-y]
```

Run tests:
```bash
py -m pytest tests/integration/ -v
```
