import pytest
from deriv import differentiate
from exprs import assert_derivative


def test_power():
    assert_derivative("x^2", differentiate("x^2"), [1.0, 2.0])


def test_linear():
    assert_derivative("3*x + 1", differentiate("3*x + 1"), [0.0, 1.5])


def test_sin():
    assert_derivative("sin(x)", differentiate("sin(x)"), [0.3, 1.0])


def test_empty_is_invalid():
    with pytest.raises(ValueError):
        differentiate("")
