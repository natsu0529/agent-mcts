"""Reference solution for the freebusy task."""

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})$")
_HOURS_RE = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")
_MINUTE = timedelta(minutes=1)


def _instant(text):
    if not isinstance(text, str) or not _STAMP_RE.match(text):
        raise ValueError(f"bad timestamp: {text!r}")
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.second or value.microsecond:
        raise ValueError(f"timestamp not on a whole minute: {text!r}")
    return value.astimezone(UTC)


def _zone(name):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError(f"unknown time zone: {name!r}") from exc


def _entry(text):
    m = _HOURS_RE.match(text) if isinstance(text, str) else None
    if not m:
        raise ValueError(f"bad working hours: {text!r}")
    sh, sm, eh, em = (int(g) for g in m.groups())
    if sh > 23 or sm > 59 or em > 59 or eh > 24 or (eh == 24 and em != 0):
        raise ValueError(f"bad working hours: {text!r}")
    start, end = sh * 60 + sm, eh * 60 + em
    if start == end:
        raise ValueError(f"empty working hours: {text!r}")
    return start, end


def _offset_periods(tz, lo, hi):
    """Split [lo, hi) into (start, end, utc offset) pieces with a constant offset in `tz`."""
    step = timedelta(minutes=30)  # no zone changes its offset twice within half an hour
    periods, start = [], lo
    offset = lo.astimezone(tz).utcoffset()
    t = lo
    while t < hi:
        nxt = min(t + step, hi)
        if nxt.astimezone(tz).utcoffset() != offset:
            # Find the first minute in (t, nxt] with the new offset.
            a, b = t, nxt
            while b - a > _MINUTE:
                mid = a + (b - a) // 2
                mid -= timedelta(seconds=mid.second, microseconds=mid.microsecond)
                mid = max(mid, a + _MINUTE)
                if mid.astimezone(tz).utcoffset() == offset:
                    a = mid
                else:
                    b = mid
            periods.append((start, b, offset))
            start, offset = b, b.astimezone(tz).utcoffset()
            nxt = b
        t = nxt
    periods.append((start, hi, offset))
    return periods


def _union(intervals):
    merged = []
    for s, e in sorted(intervals):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def _intersect(a, b):
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        s, e = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if s < e:
            out.append([s, e])
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def _subtract(a, b):
    out = []
    for s, e in a:
        for bs, be in b:
            if be <= s or bs >= e:
                continue
            if bs > s:
                out.append([s, bs])
            s = max(s, be)
            if s >= e:
                break
        if s < e:
            out.append([s, e])
    return out


def _working(tz, hours, lo, hi):
    """The instants in [lo, hi) at which the wall clock in `tz` shows working hours."""
    if not isinstance(hours, dict):
        raise ValueError("hours must be a dict")
    entries = []
    for day, items in hours.items():
        if day not in WEEKDAYS:
            raise ValueError(f"bad weekday: {day!r}")
        entries.extend((WEEKDAYS.index(day), *_entry(item)) for item in items)
    # Working hours as wall-clock intervals, on every date that can be shown in [lo, hi).
    walls = []
    d = lo.astimezone(tz).date() - timedelta(days=2)
    last = hi.astimezone(tz).date() + timedelta(days=1)
    while d <= last:
        for weekday, start, end in entries:
            if d.weekday() == weekday:
                end_day = d if start < end < 24 * 60 else d + timedelta(days=1)
                walls.append((_wall(d, start), _wall(end_day, end % (24 * 60))))
        d += timedelta(days=1)
    # Within a piece of constant offset, wall clock time = UTC + offset.
    covered = []
    for p_start, p_end, offset in _offset_periods(tz, lo, hi):
        for w_start, w_end in walls:
            s = max((w_start - offset).replace(tzinfo=UTC), p_start)
            e = min((w_end - offset).replace(tzinfo=UTC), p_end)
            if s < e:
                covered.append((s, e))
    return _union(covered)


def _wall(day: date, minutes: int) -> datetime:
    return datetime.combine(day, time(minutes // 60, minutes % 60))


def _fmt(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def free_slots(attendees, start, end, min_minutes):
    """Return the periods in [start, end) in which everyone works and nobody is busy."""
    if not attendees:
        raise ValueError("no attendees")
    lo, hi = _instant(start), _instant(end)
    if lo >= hi:
        raise ValueError("start must be before end")
    if isinstance(min_minutes, bool) or not isinstance(min_minutes, int) or min_minutes < 1:
        raise ValueError("min_minutes must be a positive int")

    free = [[lo, hi]]
    busy = []
    for person in attendees:
        tz = _zone(person["tz"])
        free = _intersect(free, _working(tz, person["hours"], lo, hi))
        for pair in person.get("busy", []):
            s, e = (_instant(x) for x in pair)
            if s >= e:
                raise ValueError("busy period must start before it ends")
            busy.append((s, e))
    free = _subtract(_union(free), _union(busy))
    keep = timedelta(minutes=min_minutes)
    return [(_fmt(s), _fmt(e)) for s, e in _union(free) if e - s >= keep]
