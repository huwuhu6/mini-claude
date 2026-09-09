from report import label
def test_report():
    assert label(1)=='paid'
    assert label(-1)=='credit'
