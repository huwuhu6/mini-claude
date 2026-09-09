from discounts import parse_coupon,subtotal,apply_discount
def test_coupon_normalization(): assert parse_coupon(' save10 ')=='SAVE10'
def test_subtotal_uses_quantity(): assert subtotal([{'price':10,'quantity':3}])==30
def test_discount_is_percentage(): assert apply_discount([{'price':100,'quantity':1}],'SAVE10',0)==90
def test_empty_order(): assert apply_discount([],'NONE',0)==0
def test_tax_after_discount(): assert apply_discount([{'price':100,'quantity':1}],'SAVE10',0.1)==99
