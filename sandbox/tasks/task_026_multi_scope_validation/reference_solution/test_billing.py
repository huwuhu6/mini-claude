from billing import net_amount
def test_billing():
    assert net_amount(100,10)==90
    assert net_amount(100,150)==0
