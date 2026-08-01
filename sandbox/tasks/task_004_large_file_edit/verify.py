import sys
import order_processor

def test_bug_fixes():
    # 创建实例
    proc = order_processor.OrderProcessor()
    
    # Bug 1: 验证 VERSION 拼写修复
    assert hasattr(proc, "VERSION"), "Bug 1: VERSION variable still misspelled"
    assert proc.VERSION == "1.0.0", "Bug 1: VERSION value incorrect"
    
    # 创建测试订单
    order_id = proc.create_order("test_user", [{"price": 100, "quantity": 1, "name": "test"}])
    assert order_id is not None
    
    # Bug 2: 验证折扣计算修复 (应该是减法，不是加法)
    discount_result = proc.calculate_discount(order_id, 0.2)
    assert discount_result == 80.0, f"Bug 2: discount calculation wrong, got {discount_result}, expected 80.0"
    
    # Bug 3: 验证 logger 修复 (不应该有 NameError)
    try:
        proc.send_order_confirmation(order_id)
    except NameError as e:
        if "log_system" in str(e):
            assert False, "Bug 3: logger name still has log_system"
    
    print("✅ [SUCCESS] 所有 3 处 Bug 已修复！")
    return 0

if __name__ == "__main__":
    sys.exit(test_bug_fixes())