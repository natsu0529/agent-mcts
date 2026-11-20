"""Reference solution, used only to check that the hidden tests are satisfiable."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "hidden"))
from _ref_exprs import parse


def _has_x(n):
    return n[0] == "x" or any(_has_x(c) for c in n[1:] if isinstance(c, tuple))


def _d(n):
    k = n[0]
    if k == "num":
        return ("num", 0.0)
    if k == "x":
        return ("num", 1.0)
    if k == "neg":
        return ("neg", _d(n[1]))
    if k in ("add", "sub"):
        return (k, _d(n[1]), _d(n[2]))
    if k == "mul":
        return ("add", ("mul", _d(n[1]), n[2]), ("mul", n[1], _d(n[2])))
    if k == "div":
        return (
            "div",
            ("sub", ("mul", _d(n[1]), n[2]), ("mul", n[1], _d(n[2]))),
            ("pow", n[2], ("num", 2.0)),
        )
    if k == "pow":
        f, g = n[1], n[2]
        if not _has_x(g):
            if not _has_x(f):
                return ("num", 0.0)
            return ("mul", ("mul", g, ("pow", f, ("sub", g, ("num", 1.0)))), _d(f))
        if not _has_x(f):
            return ("mul", ("mul", n, ("fn", "ln", f)), _d(g))
        return (
            "mul",
            n,
            ("add", ("mul", _d(g), ("fn", "ln", f)), ("div", ("mul", g, _d(f)), f)),
        )
    name, a = n[1], n[2]
    da = _d(a)
    if name == "sin":
        outer = ("fn", "cos", a)
    elif name == "cos":
        outer = ("neg", ("fn", "sin", a))
    elif name == "tan":
        outer = ("div", ("num", 1.0), ("pow", ("fn", "cos", a), ("num", 2.0)))
    elif name == "exp":
        outer = n
    elif name == "ln":
        outer = ("div", ("num", 1.0), a)
    else:
        outer = ("div", ("num", 1.0), ("mul", ("num", 2.0), n))
    return ("mul", outer, da)


def _s(n):
    k = n[0]
    if k == "num":
        return format(n[1], "f").rstrip("0").rstrip(".") or "0"
    if k == "x":
        return "x"
    if k == "neg":
        return f"(-{_s(n[1])})"
    if k == "fn":
        return f"{n[1]}({_s(n[2])})"
    op = {"add": "+", "sub": "-", "mul": "*", "div": "/", "pow": "^"}[k]
    return f"({_s(n[1])}{op}{_s(n[2])})"


def differentiate(expr: str) -> str:
    return _s(_d(parse(expr)))
