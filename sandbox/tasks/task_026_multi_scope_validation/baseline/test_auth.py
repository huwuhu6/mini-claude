from auth import can_view
def test_auth():
    assert can_view({'role':'admin'}) is True
    assert can_view({'role':'auditor'}) is True
