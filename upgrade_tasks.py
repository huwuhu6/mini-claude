#!/usr/bin/env python3
"""
upgrade_tasks.py — 一次性升级脚本：将 tasks/ 下碎片化的 problem.md 统一为 config.json。
"""

from __future__ import annotations
import json
import shutil
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
_TASKS_DIR = _PROJECT_ROOT / "sandbox" / "tasks"

_DIRTY_DIRS = {".traces", "logs", "__pycache__", ".transcripts", "tmp"}


def _clean_baseline(task_dir: Path) -> None:
    """抹除 baseline/ 下的动态运行时污染目录。"""
    baseline_dir = task_dir / "baseline"
    if not baseline_dir.is_dir():
        return
    for item in baseline_dir.iterdir():
        if item.name in _DIRTY_DIRS and item.is_dir():
            shutil.rmtree(item)
            print(f"    [clean] 已清除污染目录: {item.relative_to(_PROJECT_ROOT)}")


def _upgrade_task(task_dir: Path) -> bool:
    """处理单个题目文件夹，返回是否成功。"""
    case_id = task_dir.name
    problem_path = task_dir / "problem.md"
    verify_path = task_dir / "verify.py"
    config_path = task_dir / "config.json"

    # ── 读取 problem.md ──────────────────────────────────
    if not problem_path.exists():
        print(f"  [skip] [{case_id}] 缺少 problem.md，跳过")
        return False

    prompt = problem_path.read_text(encoding="utf-8").strip()

    # ── 判定 verify_script_file ──────────────────────────
    verify_script_file = "verify.py" if verify_path.exists() else None

    # ── 写入 config.json ─────────────────────────────────
    config = {
        "case_id": case_id,
        "prompt": prompt,
        "verify_script_file": verify_script_file,
    }
    try:
        config_path.write_text(
            json.dumps(config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"  [err] [{case_id}] config.json 写入失败: {exc}")
        return False

    # ── 安全删除 problem.md ──────────────────────────────
    try:
        problem_path.unlink()
    except OSError as exc:
        # 删除失败时回滚：删掉刚写的 config.json，避免不一致状态
        config_path.unlink(missing_ok=True)
        print(f"  [err] [{case_id}] problem.md 删除失败，已回滚 config.json: {exc}")
        return False

    print(f"  [OK] [{case_id}] config.json 已生成，problem.md 已清除")
    return True


def main() -> None:
    if not _TASKS_DIR.is_dir():
        print(f"错误：任务目录不存在: {_TASKS_DIR}")
        sys.exit(1)

    task_dirs = sorted([d for d in _TASKS_DIR.iterdir() if d.is_dir()])
    if not task_dirs:
        print("没有找到任何题目文件夹。")
        sys.exit(0)

    print(f"扫描到 {len(task_dirs)} 个题目文件夹\n")

    ok = 0
    fail = 0
    for task_dir in task_dirs:
        print(f"── {task_dir.name} ──")
        _clean_baseline(task_dir)
        if _upgrade_task(task_dir):
            ok += 1
        else:
            fail += 1
        print()

    print(f"完成：{ok} 成功，{fail} 失败 / 共 {len(task_dirs)} 个任务")


if __name__ == "__main__":
    main()
