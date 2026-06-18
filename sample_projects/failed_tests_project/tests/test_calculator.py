from calculator import add


def test_add_returns_expected_business_value():
    # Intentional failure used to validate release blocking.
    assert add(2, 3) == 6