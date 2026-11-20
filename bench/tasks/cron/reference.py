"""Reference solution, used only to check that the hidden tests are satisfiable."""

from datetime import datetime, timedelta

_MONTHS = {
    n: i + 1
    for i, n in enumerate(
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    )
}
_DAYS = {n: i for i, n in enumerate(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])}


def _value(text, lo, hi, names):
    if text.upper() in names:
        return names[text.upper()]
    if not text.isdigit():
        raise ValueError(f"bad value {text!r}")
    value = int(text)
    if not lo <= value <= hi:
        raise ValueError(f"{value} out of range {lo}-{hi}")
    return value


def _field(text, lo, hi, names):
    out = set()
    for item in text.split(","):
        base, slash, step_text = item.partition("/")
        step = 1
        if slash:
            if not step_text.isdigit() or int(step_text) == 0:
                raise ValueError(f"bad step in {item!r}")
            step = int(step_text)
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, _, b = base.partition("-")
            start, end = _value(a, lo, hi, names), _value(b, lo, hi, names)
            if start > end:
                raise ValueError(f"reversed range {base!r}")
        else:
            start = _value(base, lo, hi, names)
            end = hi if slash else start
        out.update(range(start, end + 1, step))
    return out


def next_run(expr: str, after: datetime) -> datetime:
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError("expected five fields")
    minutes = sorted(_field(fields[0], 0, 59, {}))
    hours = sorted(_field(fields[1], 0, 23, {}))
    doms = _field(fields[2], 1, 31, {})
    months = _field(fields[3], 1, 12, _MONTHS)
    dows = {d % 7 for d in _field(fields[4], 0, 7, _DAYS)}
    dom_star, dow_star = fields[2] == "*", fields[4] == "*"

    def day_ok(day):
        if day.month not in months:
            return False
        dom_ok = day.day in doms
        dow_ok = (day.weekday() + 1) % 7 in dows
        if not dom_star and not dow_star:
            return dom_ok or dow_ok
        return dom_ok and dow_ok

    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = start.replace(hour=0, minute=0)
    for _ in range(366 * 28 + 1):
        if day_ok(day):
            for hour in hours:
                for minute in minutes:
                    candidate = day.replace(hour=hour, minute=minute)
                    if candidate >= start:
                        return candidate
        day += timedelta(days=1)
    raise ValueError("expression never fires")
