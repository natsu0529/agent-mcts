import pytest
from _ref_exprs import assert_derivative
from deriv import differentiate

CASES = [
    ("5", [0.0, 2.0]),
    ("x", [0.0, 2.0]),
    ("x^2", [-2.0, 0.0, 1.5]),
    ("3*x+1", [-1.0, 2.0]),
    ("x^3 - 2*x", [-2.0, 0.0, 1.5]),
    ("sin(x)", [0.3, 1.0]),
    ("cos(x)", [0.3, 1.0]),
    ("tan(x)", [0.3, 1.0]),
    ("exp(x)", [-1.0, 0.5]),
    ("ln(x)", [0.5, 2.0]),
    ("sqrt(x)", [0.5, 2.0]),
    ("x*sin(x)", [0.7, -1.2]),
    ("sin(x)/x", [0.7, -1.2]),
    ("sin(x^2)", [0.7, -1.2]),
    ("exp(-x)", [-1.0, 0.5]),
    ("ln(x^2+1)", [-1.0, 0.0, 2.0]),
    ("sqrt(1+x^2)", [-1.0, 0.0, 2.0]),
    ("-x^2", [-1.0, 2.0]),
    ("2^-x", [-1.0, 0.5]),
    ("2^3^2*x", [0.0, 1.0]),
    ("x-1-2*x", [0.0, 1.0]),
    ("8/2/x", [1.0, 2.0]),
    ("-(-x)", [0.0, 1.0]),
    ("x - -x", [0.0, 1.0]),
    ("2^x", [-1.0, 0.5]),
    ("x^x", [0.5, 2.0]),
    ("(x-5)^2", [1.0, 3.0]),
    ("x^3", [-2.0, 0.0]),
    ("(-x)^3", [1.0, 2.0]),
    ("x^-2", [-1.5, 2.0]),
    ("(sin(x))^2", [4.0, 1.0]),
    ("x^0.5", [2.0, 0.5]),
    ("(x^2+1)^x", [-1.0, 1.0]),
    ("  x  ^ 2 + 1 ", [1.0, -1.0]),
    ("0.5*x^2", [1.0, -1.0]),
    ("1.5*x", [1.0, -1.0]),
    ("sin(cos(exp(x)))", [0.2, -0.7]),
    ("exp(x)/(1+exp(x))", [0.2, -0.7]),
    ("ln(sqrt(x))", [0.5, 2.0]),
    ("(2*x+1)^4", [-2.0, 0.3]),
    ("1/(x-3)^2", [1.0, 5.0]),
    ("x^2*x^3", [-1.0, 0.0, 1.2]),
]

INVALID = [
    "",
    "   ",
    "2 +",
    "sin x",
    "y",
    "(x",
    "x)",
    "2 x",
    "2x",
    "x^",
    "2..3",
    "sin()",
    "foo(x)",
    "x**2",
    "1e3",
    "x +* 2",
]


@pytest.mark.parametrize("expr,points", CASES, ids=[c[0] for c in CASES])
def test_derivative(expr, points):
    assert_derivative(expr, differentiate(expr), points)


@pytest.mark.parametrize("expr", INVALID, ids=[repr(e) for e in INVALID])
def test_invalid(expr):
    with pytest.raises(ValueError):
        differentiate(expr)
