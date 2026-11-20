"""Build hidden/test_hidden_cron.py: expected values come from reference.py and are
cross-checked against an independent minute-by-minute scan.

Run `ruff format` on the generated file afterwards.
"""

from datetime import datetime, timedelta
from pathlib import Path

import reference

D = datetime
CASES = [
    ("* * * * *", D(2025, 3, 10, 12, 0)),
    ("* * * * *", D(2025, 3, 10, 12, 0, 59, 999999)),
    ("* * * * *", D(2025, 12, 31, 23, 59)),
    ("0 * * * *", D(2025, 3, 10, 12, 0)),
    ("30 9 * * *", D(2025, 3, 10, 9, 30)),
    ("30 9 * * *", D(2025, 3, 10, 9, 29, 59)),
    ("0 0 1 * *", D(2025, 1, 31, 12, 0)),
    ("0 0 31 * *", D(2025, 1, 31, 0, 0)),
    ("0 0 29 2 *", D(2025, 3, 1, 0, 0)),
    ("0 0 29 2 *", D(2096, 3, 1, 0, 0)),
    ("59 23 31 12 *", D(2025, 12, 31, 23, 59)),
    ("*/15 * * * *", D(2025, 3, 10, 12, 45)),
    ("10-30/5 * * * *", D(2025, 3, 10, 12, 26)),
    ("5/20 * * * *", D(2025, 3, 10, 12, 46)),
    ("5/20 * * * *", D(2025, 3, 10, 12, 4)),
    ("0 */6 * * *", D(2025, 3, 10, 18, 0)),
    ("0 8-18/5 * * *", D(2025, 3, 10, 13, 0)),
    ("*/7 * * * *", D(2025, 3, 10, 12, 56)),
    ("0,30 1,13 * * *", D(2025, 3, 10, 13, 0)),
    ("15,45 * * * *", D(2025, 3, 10, 12, 45)),
    ("0 0 * * 0", D(2025, 3, 10, 0, 0)),
    ("0 0 * * 7", D(2025, 3, 10, 0, 0)),
    ("0 0 * * SUN", D(2025, 3, 9, 0, 0)),
    ("0 9 * * MON-FRI", D(2025, 3, 14, 9, 0)),
    ("0 9 * * mon-fri", D(2025, 3, 15, 8, 0)),
    ("0 0 * * 5-7", D(2025, 3, 10, 0, 0)),
    ("0 0 * * 5-7", D(2025, 3, 15, 0, 0)),
    ("0 0 * * 6,0", D(2025, 3, 15, 0, 0)),
    ("0 0 * * */2", D(2025, 3, 10, 0, 0)),
    ("0 0 * JAN,jul *", D(2025, 3, 10, 0, 0)),
    ("0 0 1 Feb-Apr *", D(2025, 4, 1, 0, 0)),
    ("0 0 * 12 *", D(2025, 12, 31, 0, 0)),
    ("0 0 13 * FRI", D(2025, 3, 10, 0, 0)),
    ("0 0 13 * FRI", D(2025, 3, 13, 0, 0)),
    ("0 0 1,15 * MON", D(2025, 3, 11, 0, 0)),
    ("0 0 */2 * MON", D(2025, 3, 9, 0, 0)),
    ("0 0 */2 * MON", D(2025, 3, 31, 0, 0)),
    ("0 0 * * MON", D(2025, 3, 10, 0, 0)),
    ("0 0 13 * *", D(2025, 3, 13, 0, 0)),
    ("0 0 31 4,5 *", D(2025, 4, 1, 0, 0)),
    ("0 0 30 * *", D(2025, 1, 30, 0, 0)),
    ("0 12 29 2 MON", D(2025, 2, 24, 12, 0)),
    ("0 0 31 2 SUN", D(2025, 2, 1, 0, 0)),
    ("  5   4  *  *  *  ", D(2025, 3, 10, 12, 0)),
    ("0\t0 * * *", D(2025, 3, 10, 12, 0)),
    ("0 0 1 1 *", D(2025, 1, 1, 0, 0)),
    ("0 0 29 2 *", D(2024, 2, 28, 23, 59)),
    ("30 2 * 3 SUN", D(2025, 3, 30, 2, 30)),
    ("0-5 0 * * *", D(2025, 3, 10, 0, 5)),
    ("0 23/1 * * *", D(2025, 3, 10, 23, 0)),
]

INVALID = [
    "",
    "* * * *",
    "* * * * * *",
    "60 * * * *",
    "* 24 * * *",
    "* * 0 * *",
    "* * 32 * *",
    "* * * 0 *",
    "* * * 13 *",
    "* * * * 8",
    "*/0 * * * *",
    "*/x * * * *",
    "30-10 * * * *",
    "1,,2 * * * *",
    "a * * * *",
    "* * * FOO *",
    "-1 * * * *",
    "1- * * * *",
    "* * * * MON/TUE",
    "0 0 30 2 *",
    "0 0 31 4,6,9,11 *",
    "0 0 31 FEB *",
    "1.5 * * * *",
    "*/-5 * * * *",
]


def brute(expr, after):
    """Independent check: test every minute against directly expanded sets."""
    f = expr.split()
    sets = [
        reference._field(f[0], 0, 59, {}),
        reference._field(f[1], 0, 23, {}),
        reference._field(f[2], 1, 31, {}),
        reference._field(f[3], 1, 12, reference._MONTHS),
        {d % 7 for d in reference._field(f[4], 0, 7, reference._DAYS)},
    ]
    t = after.replace(second=0, microsecond=0)
    for _ in range(60 * 24 * 366 * 9):
        t += timedelta(minutes=1)
        if t.minute not in sets[0] or t.hour not in sets[1] or t.month not in sets[3]:
            continue
        dom, dow = t.day in sets[2], t.isoweekday() % 7 in sets[4]
        both = f[2] != "*" and f[4] != "*"
        if (dom or dow) if both else (dom and dow):
            return t
    raise ValueError


lines = [
    "from datetime import datetime",
    "",
    "import pytest",
    "",
    "from cronnext import next_run",
    "",
    "CASES = [",
]
for expr, after in CASES:
    expected = reference.next_run(expr, after)
    assert brute(expr, after) == expected, (expr, after)
    lines.append(
        f"    ({expr!r}, {after!r}, {expected!r}),".replace("datetime.datetime", "datetime")
    )
lines += ["]", "", "INVALID = ["]
for expr in INVALID:
    try:
        reference.next_run(expr, D(2025, 3, 10))
    except ValueError:
        pass
    else:
        raise AssertionError(f"reference accepts {expr!r}")
    lines.append(f"    {expr!r},")
lines += [
    "]",
    "",
    "",
    "@pytest.mark.parametrize(",
    '    "expr,after,expected", CASES, ids=[f"{c[0]} @ {c[1]}" for c in CASES]',
    ")",
    "def test_next_run(expr, after, expected):",
    "    assert next_run(expr, after) == expected",
    "",
    "",
    '@pytest.mark.parametrize("expr", INVALID, ids=[repr(e) for e in INVALID])',
    "def test_invalid(expr):",
    "    with pytest.raises(ValueError):",
    "        next_run(expr, datetime(2025, 3, 10))",
    "",
]
Path(__file__).with_name("hidden").joinpath("test_hidden_cron.py").write_text("\n".join(lines))
print(len(CASES), "cases,", len(INVALID), "invalid")
