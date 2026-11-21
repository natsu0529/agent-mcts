import pytest
from semverrange import satisfies


def test_exact():
    assert satisfies("1.2.3", "1.2.3")
    assert not satisfies("1.2.4", "1.2.3")


def test_caret():
    assert satisfies("1.9.0", "^1.2.3")
    assert not satisfies("2.0.0", "^1.2.3")


def test_union():
    assert satisfies("3.0.0", "1.x || >=3.0.0")


def test_invalid_version():
    with pytest.raises(ValueError):
        satisfies("1.2", "*")
