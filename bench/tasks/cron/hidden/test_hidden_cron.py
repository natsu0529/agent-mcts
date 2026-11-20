from datetime import datetime

import pytest
from cronnext import next_run

CASES = [
    ("* * * * *", datetime(2025, 3, 10, 12, 0), datetime(2025, 3, 10, 12, 1)),
    ("* * * * *", datetime(2025, 3, 10, 12, 0, 59, 999999), datetime(2025, 3, 10, 12, 1)),
    ("* * * * *", datetime(2025, 12, 31, 23, 59), datetime(2026, 1, 1, 0, 0)),
    ("0 * * * *", datetime(2025, 3, 10, 12, 0), datetime(2025, 3, 10, 13, 0)),
    ("30 9 * * *", datetime(2025, 3, 10, 9, 30), datetime(2025, 3, 11, 9, 30)),
    ("30 9 * * *", datetime(2025, 3, 10, 9, 29, 59), datetime(2025, 3, 10, 9, 30)),
    ("0 0 1 * *", datetime(2025, 1, 31, 12, 0), datetime(2025, 2, 1, 0, 0)),
    ("0 0 31 * *", datetime(2025, 1, 31, 0, 0), datetime(2025, 3, 31, 0, 0)),
    ("0 0 29 2 *", datetime(2025, 3, 1, 0, 0), datetime(2028, 2, 29, 0, 0)),
    ("0 0 29 2 *", datetime(2096, 3, 1, 0, 0), datetime(2104, 2, 29, 0, 0)),
    ("59 23 31 12 *", datetime(2025, 12, 31, 23, 59), datetime(2026, 12, 31, 23, 59)),
    ("*/15 * * * *", datetime(2025, 3, 10, 12, 45), datetime(2025, 3, 10, 13, 0)),
    ("10-30/5 * * * *", datetime(2025, 3, 10, 12, 26), datetime(2025, 3, 10, 12, 30)),
    ("5/20 * * * *", datetime(2025, 3, 10, 12, 46), datetime(2025, 3, 10, 13, 5)),
    ("5/20 * * * *", datetime(2025, 3, 10, 12, 4), datetime(2025, 3, 10, 12, 5)),
    ("0 */6 * * *", datetime(2025, 3, 10, 18, 0), datetime(2025, 3, 11, 0, 0)),
    ("0 8-18/5 * * *", datetime(2025, 3, 10, 13, 0), datetime(2025, 3, 10, 18, 0)),
    ("*/7 * * * *", datetime(2025, 3, 10, 12, 56), datetime(2025, 3, 10, 13, 0)),
    ("0,30 1,13 * * *", datetime(2025, 3, 10, 13, 0), datetime(2025, 3, 10, 13, 30)),
    ("15,45 * * * *", datetime(2025, 3, 10, 12, 45), datetime(2025, 3, 10, 13, 15)),
    ("0 0 * * 0", datetime(2025, 3, 10, 0, 0), datetime(2025, 3, 16, 0, 0)),
    ("0 0 * * 7", datetime(2025, 3, 10, 0, 0), datetime(2025, 3, 16, 0, 0)),
    ("0 0 * * SUN", datetime(2025, 3, 9, 0, 0), datetime(2025, 3, 16, 0, 0)),
    ("0 9 * * MON-FRI", datetime(2025, 3, 14, 9, 0), datetime(2025, 3, 17, 9, 0)),
    ("0 9 * * mon-fri", datetime(2025, 3, 15, 8, 0), datetime(2025, 3, 17, 9, 0)),
    ("0 0 * * 5-7", datetime(2025, 3, 10, 0, 0), datetime(2025, 3, 14, 0, 0)),
    ("0 0 * * 5-7", datetime(2025, 3, 15, 0, 0), datetime(2025, 3, 16, 0, 0)),
    ("0 0 * * 6,0", datetime(2025, 3, 15, 0, 0), datetime(2025, 3, 16, 0, 0)),
    ("0 0 * * */2", datetime(2025, 3, 10, 0, 0), datetime(2025, 3, 11, 0, 0)),
    ("0 0 * JAN,jul *", datetime(2025, 3, 10, 0, 0), datetime(2025, 7, 1, 0, 0)),
    ("0 0 1 Feb-Apr *", datetime(2025, 4, 1, 0, 0), datetime(2026, 2, 1, 0, 0)),
    ("0 0 * 12 *", datetime(2025, 12, 31, 0, 0), datetime(2026, 12, 1, 0, 0)),
    ("0 0 13 * FRI", datetime(2025, 3, 10, 0, 0), datetime(2025, 3, 13, 0, 0)),
    ("0 0 13 * FRI", datetime(2025, 3, 13, 0, 0), datetime(2025, 3, 14, 0, 0)),
    ("0 0 1,15 * MON", datetime(2025, 3, 11, 0, 0), datetime(2025, 3, 15, 0, 0)),
    ("0 0 */2 * MON", datetime(2025, 3, 9, 0, 0), datetime(2025, 3, 10, 0, 0)),
    ("0 0 */2 * MON", datetime(2025, 3, 31, 0, 0), datetime(2025, 4, 1, 0, 0)),
    ("0 0 * * MON", datetime(2025, 3, 10, 0, 0), datetime(2025, 3, 17, 0, 0)),
    ("0 0 13 * *", datetime(2025, 3, 13, 0, 0), datetime(2025, 4, 13, 0, 0)),
    ("0 0 31 4,5 *", datetime(2025, 4, 1, 0, 0), datetime(2025, 5, 31, 0, 0)),
    ("0 0 30 * *", datetime(2025, 1, 30, 0, 0), datetime(2025, 3, 30, 0, 0)),
    ("0 12 29 2 MON", datetime(2025, 2, 24, 12, 0), datetime(2026, 2, 2, 12, 0)),
    ("0 0 31 2 SUN", datetime(2025, 2, 1, 0, 0), datetime(2025, 2, 2, 0, 0)),
    ("  5   4  *  *  *  ", datetime(2025, 3, 10, 12, 0), datetime(2025, 3, 11, 4, 5)),
    ("0\t0 * * *", datetime(2025, 3, 10, 12, 0), datetime(2025, 3, 11, 0, 0)),
    ("0 0 1 1 *", datetime(2025, 1, 1, 0, 0), datetime(2026, 1, 1, 0, 0)),
    ("0 0 29 2 *", datetime(2024, 2, 28, 23, 59), datetime(2024, 2, 29, 0, 0)),
    ("30 2 * 3 SUN", datetime(2025, 3, 30, 2, 30), datetime(2026, 3, 1, 2, 30)),
    ("0-5 0 * * *", datetime(2025, 3, 10, 0, 5), datetime(2025, 3, 11, 0, 0)),
    ("0 23/1 * * *", datetime(2025, 3, 10, 23, 0), datetime(2025, 3, 11, 23, 0)),
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


@pytest.mark.parametrize("expr,after,expected", CASES, ids=[f"{c[0]} @ {c[1]}" for c in CASES])
def test_next_run(expr, after, expected):
    assert next_run(expr, after) == expected


@pytest.mark.parametrize("expr", INVALID, ids=[repr(e) for e in INVALID])
def test_invalid(expr):
    with pytest.raises(ValueError):
        next_run(expr, datetime(2025, 3, 10))
