# deriv

Implement `differentiate(expr: str) -> str` in `deriv.py`.

It takes a mathematical expression in the single variable `x` and returns its derivative
with respect to `x`, as an expression **in the same grammar**.

## Grammar

- **Numbers**: one or more digits, optionally followed by `.` and one or more digits
  (`3`, `0.5`, `12.25`). No sign, no scientific notation (`1e3` is invalid), no leading
  or trailing dot.
- **Variable**: `x` (the only one).
- **Binary operators**: `+`, `-`, `*`, `/`, `^` (power).
- **Unary minus**: `-x`, `-(x + 1)`, `x - -x`.
- **Parentheses** for grouping.
- **Functions**, always called with parentheses and exactly one argument:
  `sin`, `cos`, `tan`, `exp`, `ln` (natural log), `sqrt`.
- Whitespace between tokens is ignored.
- There is no implicit multiplication: `2x` and `2 x` are invalid.

Precedence, from loosest to tightest:

1. `+` and `-` (left associative)
2. `*` and `/` (left associative): `8/2/x` is `(8/2)/x`
3. unary minus
4. `^` (right associative): `2^3^2` is `2^(3^2)`

So `-x^2` means `-(x^2)`. The exponent may itself start with a unary minus: `2^-x` means
`2^(-x)`.

## Requirements

- The result must be a valid expression in the grammar above. It does not have to be
  simplified, and any equivalent form is accepted.
- The result must evaluate to the correct derivative at **every real `x` where the input
  expression is differentiable**. For example the derivative of `x^3` must evaluate
  correctly at negative `x` and at `x = 0`.
- The exponent of `^` can be any expression, including one that depends on `x` (`2^x`,
  `x^x`).
- Raise `ValueError` for any input that is not a valid expression in the grammar
  (including the empty string).

`tests/exprs.py` contains `evaluate(expr, x)`, a reference evaluator for the grammar. The
tests use it to check your result numerically against a finite difference.

## Running the tests

    python -m pytest -q
