"""Reference parser and evaluator for the expression grammar in README.md."""

from __future__ import annotations

import math
import re

FUNCTIONS = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "exp": math.exp,
    "ln": math.log,
    "sqrt": math.sqrt,
}

_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|([a-z]+)|(.))")


def tokenize(expr: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    text = expr.rstrip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if m is None:
            raise ValueError(f"cannot tokenize at {pos}: {expr!r}")
        number, name, symbol = m.groups()
        if number is not None:
            end = m.end()
            if end < len(text) and (text[end] == "." or text[end].isalpha()):
                raise ValueError(f"malformed number in {expr!r}")
            tokens.append(("num", number))
        elif name is not None:
            if name != "x" and name not in FUNCTIONS:
                raise ValueError(f"unknown name {name!r}")
            tokens.append(("name", name))
        else:
            if symbol not in "+-*/^()":
                raise ValueError(f"unexpected character {symbol!r}")
            tokens.append(("op", symbol))
        pos = m.end()
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.i = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        tok = self.peek()
        if tok is None:
            raise ValueError("unexpected end of expression")
        self.i += 1
        return tok

    def accept(self, symbol: str) -> bool:
        if self.peek() == ("op", symbol):
            self.i += 1
            return True
        return False

    def expr(self) -> tuple:
        node = self.term()
        while True:
            if self.accept("+"):
                node = ("add", node, self.term())
            elif self.accept("-"):
                node = ("sub", node, self.term())
            else:
                return node

    def term(self) -> tuple:
        node = self.factor()
        while True:
            if self.accept("*"):
                node = ("mul", node, self.factor())
            elif self.accept("/"):
                node = ("div", node, self.factor())
            else:
                return node

    def factor(self) -> tuple:
        if self.accept("-"):
            return ("neg", self.factor())
        base = self.atom()
        if self.accept("^"):
            return ("pow", base, self.factor())
        return base

    def atom(self) -> tuple:
        kind, value = self.take()
        if kind == "num":
            return ("num", float(value))
        if kind == "name":
            if value == "x":
                return ("x",)
            if not self.accept("("):
                raise ValueError(f"{value} must be called with parentheses")
            arg = self.expr()
            if not self.accept(")"):
                raise ValueError("missing )")
            return ("fn", value, arg)
        if value == "(":
            node = self.expr()
            if not self.accept(")"):
                raise ValueError("missing )")
            return node
        raise ValueError(f"unexpected {value!r}")


def parse(expr: str) -> tuple:
    """Parse `expr` into a nested-tuple syntax tree; ValueError if it is not valid."""
    parser = _Parser(tokenize(expr))
    node = parser.expr()
    if parser.peek() is not None:
        raise ValueError(f"unexpected trailing input in {expr!r}")
    return node


def eval_tree(node: tuple, x: float) -> float:
    kind = node[0]
    if kind == "num":
        return node[1]
    if kind == "x":
        return x
    if kind == "neg":
        return -eval_tree(node[1], x)
    if kind == "fn":
        return FUNCTIONS[node[1]](eval_tree(node[2], x))
    a, b = eval_tree(node[1], x), eval_tree(node[2], x)
    if kind == "add":
        return a + b
    if kind == "sub":
        return a - b
    if kind == "mul":
        return a * b
    if kind == "div":
        return a / b
    if kind == "pow":
        result = a**b
        if isinstance(result, complex):
            raise ValueError("negative base with a fractional exponent")
        return result
    raise AssertionError(kind)


def evaluate(expr: str, x: float) -> float:
    """Evaluate `expr` at `x`. Raises ValueError/ZeroDivisionError outside its domain."""
    return eval_tree(parse(expr), x)


def assert_derivative(expr: str, result: str, points: list[float]) -> None:
    """Check `result` against a central finite difference of `expr` at each point."""
    h = 1e-5
    for x in points:
        expected = (evaluate(expr, x + h) - evaluate(expr, x - h)) / (2 * h)
        got = evaluate(result, x)
        assert abs(got - expected) <= 1e-4 * max(1.0, abs(expected)), (
            f"d/dx {expr} at x={x}: expected about {expected}, got {got} from {result!r}"
        )
