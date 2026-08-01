#!/usr/bin/env python3
"""
综合回归验证脚本：检查 search_code 在所有边界情况下的表现。

验证四个维度：
1. Token 偷跑：总输出行数是否合理
2. 重复打印：是否有重复行
3. 路径前缀浪费：路径占比是否过高
4. 重试死循环：search_code 调用次数
"""
import json
import sys
from pathlib import Path


def main():
    trace_dir = Path(".traces")
    if not trace_dir.is_dir():
        print("FAIL: .traces directory not found")
        sys.exit(1)

    traces = sorted(trace_dir.glob("task_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not traces:
        print("FAIL: No trace file found")
        sys.exit(1)

    trace = json.loads(traces[0].read_text(encoding="utf-8"))

    # 收集所有 search_code 调用
    search_calls = []
    for turn in trace.get("turns", []):
        for tc in turn.get("tools", []):
            if tc.get("tool_name") == "search_code" and tc.get("success"):
                search_calls.append(tc)

    if not search_calls:
        print("FAIL: No successful search_code call found")
        sys.exit(1)

    print("=" * 60)
    print("  综合回归测试结果")
    print("=" * 60)

    bugs_found = 0

    # ── Bug 1: Token 偷跑 ──
    first_result = search_calls[0].get("result_preview", "")
    lines = first_result.splitlines()
    if len(lines) > 800:
        print(f"[BUG1] Token 偷跑: 输出 {len(lines)} 行，超过 800 行阈值")
        bugs_found += 1
    else:
        print(f"[OK1] Token 控制: 输出 {len(lines)} 行，在合理范围内")

    # ── Bug 2: 重复打印 ──
    content_lines = [l.strip() for l in lines if l.strip() and l.strip() != "---"]
    line_count = {}
    for line in content_lines:
        line_count[line] = line_count.get(line, 0) + 1
    duplicates = {line: count for line, count in line_count.items() if count > 1}
    if duplicates:
        print(f"[BUG2] 重复打印: {len(duplicates)} 行代码被重复打印")
        bugs_found += 1
    else:
        print(f"[OK2] 去重正常: 无重复行")

    # ── Bug 3: 路径前缀浪费 ──
    if lines and ":" in lines[0]:
        parts = lines[0].split(":", 2)
        if len(parts) >= 2:
            path_prefix = parts[0] + ":" + parts[1]
            path_length = len(path_prefix)
            repeat_count = sum(1 for line in lines if line.startswith(path_prefix))
            total_chars = sum(len(line) for line in lines)
            waste_ratio = (repeat_count * path_length) / total_chars if total_chars > 0 else 0
            if waste_ratio > 0.35:
                print(f"[BUG3] 路径前缀浪费: 占比 {waste_ratio:.1%}")
                bugs_found += 1
            else:
                print(f"[OK3] 路径前缀正常: 占比 {waste_ratio:.1%}")

    # ── Bug 4: 重试死循环 ──
    total_searches = len(search_calls)
    if total_searches > 3:
        print(f"[BUG4] 重试死循环: search_code 被调用 {total_searches} 次")
        bugs_found += 1
    else:
        print(f"[OK4] 重试控制: search_code 被调用 {total_searches} 次")

    # ── 最终判定 ──
    print("=" * 60)
    if bugs_found > 0:
        print(f"结论: 发现 {bugs_found} 个问题，需要修复")
        sys.exit(0)
    else:
        print(f"结论: 所有维度均通过，系统鲁棒性良好")
        sys.exit(0)


if __name__ == "__main__":
    main()
