def parse_coupon(code): return code.strip().upper()
def subtotal(items): return sum(item['price']*item.get('quantity',1) for item in items)
def apply_discount(items,coupon,tax_rate=0.1):
    total=subtotal(items)
    if parse_coupon(coupon)=='SAVE10': total*=0.9
    return round(total*(1+tax_rate),2)
