"""Reference solution for the duration task."""

import calendar
import re
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

_NUM = r"[0-9]+(?:[.,][0-9]+)?"
_RE = re.compile(
    rf"^([+-])?P(?:([0-9]+)Y)?(?:([0-9]+)M)?(?:({_NUM})W)?(?:({_NUM})D)?"
    rf"(?:(T)(?:({_NUM})H)?(?:({_NUM})M)?(?:({_NUM})S)?)?$"
)
_US = {
    "W": 7 * 86_400_000_000,
    "D": 86_400_000_000,
    "H": 3_600_000_000,
    "TM": 60_000_000,
    "S": 1_000_000,
}


def add_duration(start: datetime, duration: str) -> datetime:
    m = _RE.fullmatch(duration)
    if not m:
        raise ValueError(f"invalid duration: {duration!r}")
    sign, years, months, weeks, days, t, hours, minutes, seconds = m.groups()
    parts = [
        ("Y", years),
        ("M", months),
        ("W", weeks),
        ("D", days),
        ("H", hours),
        ("TM", minutes),
        ("S", seconds),
    ]
    present = [(k, v) for k, v in parts if v is not None]
    if not present:
        raise ValueError("no components")
    if t and not any(v is not None for v in (hours, minutes, seconds)):
        raise ValueError("'T' without a time component")
    if weeks is not None and len(present) > 1:
        raise ValueError("weeks cannot be combined")
    for k, v in present[:-1]:
        if not v.isdigit():
            raise ValueError(f"fraction on {k}, which is not the last component")
    neg = -1 if sign == "-" else 1

    total_months = neg * (12 * int(years or 0) + int(months or 0))
    index = start.year * 12 + start.month - 1 + total_months
    year, month0 = divmod(index, 12)
    if not 1 <= year <= 9999:
        raise ValueError("result out of range")
    month = month0 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    moved = start.replace(year=year, month=month, day=day)

    micro = Decimal(0)
    for k, v in present:
        if k in _US:
            micro += Decimal(v.replace(",", ".")) * _US[k]
    micro = int(micro.quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
    try:
        return moved + timedelta(microseconds=neg * micro)
    except OverflowError as e:
        raise ValueError("result out of range") from e
