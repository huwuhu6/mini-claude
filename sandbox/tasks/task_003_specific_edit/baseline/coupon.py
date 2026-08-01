# coupon.py

def calculate_new_year_discount(price):
    # 新年优惠：全场8折
    print("[LOG] 正在计算新年优惠...")
    return price * 0.8

def calculate_vip_discount(price):
    # VIP优惠：原本是8折，现在要求精准改成 7折 (price * 0.7)
    print("[LOG] 正在计算VIP专属优惠...")
    return price * 0.8

def calculate_bulk_discount(price):
    # 大宗采购优惠：满100件打8折
    print("[LOG] 正在计算大宗采购优惠...")
    return price * 0.8
