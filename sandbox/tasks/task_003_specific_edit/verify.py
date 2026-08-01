import os
import sys
import ast

# 动态将当前工作根目录加入系统路径，确保导入的是 Agent 修改后的 coupon.py
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# 1. 功能测试：验证各个函数的输出是否符合预期
try:
    # 强制刷新或直接导入当前根目录下的 coupon 模块
    if 'coupon' in sys.modules:
        del sys.modules['coupon']
    import coupon
    
    # VIP 折扣必须是 7 折
    if coupon.calculate_vip_discount(100) != 70:
        print("[Verify Failed]: VIP折扣修改失败，计算结果不为 70")
        sys.exit(1)
        
    # 新年和大宗折扣必须保持 8 折，不能被误伤
    if coupon.calculate_new_year_discount(100) != 80:
        print("[Verify Failed]: 错误！新年优惠被误修改了")
        sys.exit(1)
        
    if coupon.calculate_bulk_discount(100) != 80:
        print("[Verify Failed]: 错误！大宗采购优惠被误修改了")
        sys.exit(1)

except Exception as e:
    print(f"[Verify Error]: 脚本运行或动态导入崩溃: {e}")
    sys.exit(1)

# 2. 源码精准度检查：检查当前根目录下被 Agent 修改后的 coupon.py
try:
    target_file = os.path.join(current_dir, "coupon.py")
    with open(target_file, "r", encoding="utf-8") as f:
        source = f.read()
    
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            # 检查新年和大宗采购函数
            if node.name in ["calculate_new_year_discount", "calculate_bulk_discount"]:
                # 寻找函数内的数字字面量 0.8
                has_08 = any(
                    isinstance(subnode, ast.Constant) and subnode.value == 0.8 
                    for subnode in ast.walk(node)
                )
                if not has_08:
                    print(f"[Verify Error]: 约束违反！{node.name} 内部的代码被修改了。")
                    sys.exit(1)
except Exception as e:
    print(f"[Verify Error]: 源码AST检查失败 (可能文件未生成或语法错误): {e}")
    sys.exit(1)

print("【🎉 STAGE-3 PASSED: 特定位置精准修改测试完美通过！】")
sys.exit(0)
