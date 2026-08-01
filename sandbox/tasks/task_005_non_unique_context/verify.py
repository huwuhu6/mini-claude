#!/usr/bin/env python3
"""
验证脚本：检查 task_005_non_unique_context 的修复是否正确

验证点：
1. 头部（第30行附近）的 status = "PENDING" 应该保持不变
2. 尾部（reset_order_status 函数内）的 status = "PENDING" 应该被改为 "PROCESSING"
3. 如果没有正确修改，会给出清晰的错误提示
"""

import sys
import re

def test_non_unique_context_fix():
    """测试非唯一上下文的修改是否准确"""
    
    try:
        with open('order_processor_v2.py', 'r', encoding='utf-8') as f:
            content = f.read()
            lines = content.split('\n')
    except FileNotFoundError:
        print("❌ [FAILED] 找不到 order_processor_v2.py 文件")
        return 1
    
    errors = []
    
    # ========== 检查点 1：头部 status 应该保持 "PENDING" ==========
    # 找到 __init__ 方法中的 self.status = "PENDING"
    head_pattern = r'self\.status\s*=\s*"PENDING"'
    head_matches = []
    
    for i, line in enumerate(lines, 1):
        if re.search(head_pattern, line):
            # 检查是否在 __init__ 方法内（大概前100行）
            if i < 100:
                head_matches.append((i, line))
    
    if not head_matches:
        errors.append("❌ 头部未找到 self.status = \"PENDING\"（可能被误改了！）")
    else:
        for line_num, line in head_matches:
            if '"PROCESSING"' in line:
                errors.append(f"❌ 头部第 {line_num} 行被错误修改为 PROCESSING，应该保持 PENDING")
            else:
                print(f"✅ 头部第 {line_num} 行保持 PENDING（正确）")
    
    # ========== 检查点 2：尾部 status 应该被改为 "PROCESSING" ==========
    # 找到 reset_order_status 函数中的 self.orders_db[...]["status"] = "..."
    tail_pattern = r'self\.orders_db\[.*?\]\["status"\]\s*=\s*"([^"]+)"'
    tail_matches = []
    
    in_reset_function = False
    function_start = 0
    
    for i, line in enumerate(lines, 1):
        if 'def reset_order_status' in line:
            in_reset_function = True
            function_start = i
        if in_reset_function and line.strip() and 'def ' in line and i > function_start:
            in_reset_function = False
        
        if in_reset_function and re.search(tail_pattern, line):
            match = re.search(tail_pattern, line)
            status_value = match.group(1)
            tail_matches.append((i, line, status_value))
    
    if not tail_matches:
        errors.append("❌ 尾部 reset_order_status 函数中未找到 status 赋值语句")
    else:
        for line_num, line, status_value in tail_matches:
            if status_value == "PROCESSING":
                print(f"✅ 尾部第 {line_num} 行已正确改为 PROCESSING")
            else:
                errors.append(f"❌ 尾部第 {line_num} 行仍为 {status_value}，应该改为 PROCESSING")
    
    # ========== 额外检查：确保只有一处被修改 ==========
    all_pending = len(re.findall(r'self\.status\s*=\s*"PENDING"', content))
    all_processing = len(re.findall(r'self\.status\s*=\s*"PROCESSING"', content))
    
    # 注意：尾部的是 self.orders_db[...]["status"]，不是 self.status
    tail_pending = len(re.findall(r'self\.orders_db\[.*?\]\["status"\]\s*=\s*"PENDING"', content))
    tail_processing = len(re.findall(r'self\.orders_db\[.*?\]\["status"\]\s*=\s*"PROCESSING"', content))
    
    print(f"\n--- 统计信息 ---")
    print(f"self.status = \"PENDING\" 出现次数: {all_pending}")
    print(f"self.status = \"PROCESSING\" 出现次数: {all_processing}")
    print(f"尾部 self.orders_db[...][\"status\"] = \"PENDING\" 出现次数: {tail_pending}")
    print(f"尾部 self.orders_db[...][\"status\"] = \"PROCESSING\" 出现次数: {tail_processing}")
    
    # ========== 最终判定 ==========
    if errors:
        print("\n" + "="*50)
        print("❌ [FAILED] 验证未通过，发现以下问题：")
        for err in errors:
            print(f"  {err}")
        print("="*50)
        print("\n💡 提示：修改时需要提供足够长的上下文（Unique Context），")
        print("   确保 SEARCH 片段在文件中只出现一次，避免误伤头部代码。")
        return 1
    else:
        print("\n" + "="*50)
        print("✅ [SUCCESS] 恭喜！非唯一上下文修改测试通过！")
        print("   头部代码未被误伤，尾部代码被精准修改。")
        print("="*50)
        return 0


if __name__ == "__main__":
    sys.exit(test_non_unique_context_fix())