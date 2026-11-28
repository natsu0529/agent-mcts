"""Build hidden/test_hidden_freebusy.py: expected values come from a brute-force oracle
that walks every minute, and reference.py must agree with every one of them.

    python gen_hidden.py

Run `ruff format` on the generated file afterwards.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import reference

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MIN = timedelta(minutes=1)


# ---------------------------------------------------------------- the oracle


def _parse(text):
    return datetime.fromisoformat(text).astimezone(UTC)


def _working_minutes(person, lo, n):
    """A list of n booleans: is `person` working at minute lo + i.

    Read the wall clock at each minute and check it against the entries of the weekday
    it shows, and against the overnight entries of the day before.
    """
    tz = ZoneInfo(person["tz"])
    entries = {
        day: [tuple(mins(x) for x in e.split("-")) for e in items]
        for day, items in person["hours"].items()
    }
    work = []
    for i in range(n):
        wall = (lo + i * MIN).astimezone(tz)
        minute = wall.hour * 60 + wall.minute
        today = entries.get(DAYS[wall.weekday()], [])
        yesterday = entries.get(DAYS[(wall.weekday() - 1) % 7], [])
        work.append(
            any(s <= minute < e if s < e else s <= minute for s, e in today)
            or any(e < s and minute < e for s, e in yesterday)
        )
    return work


def oracle(attendees, start, end, min_minutes):
    lo, hi = _parse(start), _parse(end)
    n = (hi - lo) // MIN
    free = [True] * n
    for person in attendees:
        work = _working_minutes(person, lo, n)
        free = [f and w for f, w in zip(free, work, strict=True)]
        for bs, be in person.get("busy", []):
            s, e = _parse(bs), _parse(be)
            for k in range(max(0, (s - lo) // MIN), min(n, (e - lo) // MIN)):
                free[k] = False
    slots, i = [], 0
    while i < n:
        if not free[i]:
            i += 1
            continue
        j = i
        while j < n and free[j]:
            j += 1
        if j - i >= min_minutes:
            slots.append((_z(lo + i * MIN), _z(lo + j * MIN)))
        i = j
    return slots


def _z(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- the cases


def every(*entries, days=DAYS):
    return {d: list(entries) for d in days}


def hm(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def mins(text):
    h, m = map(int, text.split(":"))
    return h * 60 + m


def wd(d):
    return DAYS[d.weekday()]


def window(d, before=2, after=2):
    """A UTC search window from `before` days before `d` to `after` days after it."""
    return (
        _z(datetime(d.year, d.month, d.day, tzinfo=UTC) - timedelta(days=before)),
        _z(datetime(d.year, d.month, d.day, tzinfo=UTC) + timedelta(days=after)),
    )


def jump_instant(tz, d, wall_before):
    """The UTC instant of the clock change on local date d, which happens at wall_before."""
    zone = ZoneInfo(tz)
    probe = datetime(d.year, d.month, d.day, tzinfo=UTC) - timedelta(days=1)
    off = probe.astimezone(zone).utcoffset()
    while (probe + MIN).astimezone(zone).utcoffset() == off:
        probe += MIN
    jump = probe + MIN
    shown = (jump - MIN).astimezone(zone).replace(tzinfo=None) + MIN
    assert shown.time().hour * 60 + shown.time().minute == wall_before % (24 * 60), (tz, d)
    return jump


CASES = {}
ERRORS = {}


def case(name, attendees, start, end, min_minutes=30):
    assert name not in CASES, name
    CASES[name] = {
        "attendees": attendees,
        "start": start,
        "end": end,
        "min_minutes": min_minutes,
    }


# (zone, local date of the forward jump, wall time jumped from, wall time jumped to)
SPRING = [
    ("America/New_York", date(2025, 3, 9), "02:00", "03:00"),
    ("Europe/London", date(2025, 3, 30), "01:00", "02:00"),
    ("Europe/Berlin", date(2025, 3, 30), "02:00", "03:00"),
    ("Australia/Sydney", date(2025, 10, 5), "02:00", "03:00"),
    ("Australia/Lord_Howe", date(2025, 10, 5), "02:00", "02:30"),
    ("Pacific/Chatham", date(2025, 9, 28), "02:45", "03:45"),
    ("America/Santiago", date(2025, 9, 7), "00:00", "01:00"),
    ("America/Havana", date(2025, 3, 9), "00:00", "01:00"),
    ("America/St_Johns", date(2025, 3, 9), "02:00", "03:00"),
    ("Africa/Cairo", date(2025, 4, 25), "00:00", "01:00"),
]
# (zone, local date on which the repeated hour starts, first repeated time, end of repeat)
FALL = [
    ("America/New_York", date(2025, 11, 2), "01:00", "02:00"),
    ("Europe/London", date(2025, 10, 26), "01:00", "02:00"),
    ("Europe/Berlin", date(2025, 10, 26), "02:00", "03:00"),
    ("Australia/Sydney", date(2025, 4, 6), "02:00", "03:00"),
    ("Australia/Lord_Howe", date(2025, 4, 6), "01:30", "02:00"),
    ("Pacific/Chatham", date(2025, 4, 6), "02:45", "03:45"),
    ("America/Santiago", date(2025, 4, 5), "23:00", "24:00"),
    ("America/Havana", date(2025, 11, 2), "00:00", "01:00"),
    ("America/St_Johns", date(2025, 11, 2), "01:00", "02:00"),
    ("Africa/Cairo", date(2025, 10, 30), "23:00", "24:00"),
]


def short(tz):
    return tz.split("/")[-1].lower()


def spring_cases():
    for tz, d, g0, g1 in SPRING:
        a, b = mins(g0), mins(g1)
        mid = a + (b - a) // 2
        name = short(tz)
        start, end = window(d)
        jump = jump_instant(tz, d, a)
        # A working day that starts inside the gap starts when the clocks jump.
        case(
            f"spring_{name}_start_in_gap",
            [{"tz": tz, "hours": every(f"{hm(mid)}-12:00")}],
            start,
            end,
        )
        # A night shift that ends inside the gap ends when the clocks jump.
        case(
            f"spring_{name}_end_in_gap",
            [{"tz": tz, "hours": every(f"20:00-{hm(mid)}")}],
            start,
            end,
        )
        # Hours that lie wholly inside the gap are never shown on that date.
        inner = f"{hm(a + 5)}-{hm(b - 5)}"
        case(f"spring_{name}_inside_gap", [{"tz": tz, "hours": every(inner)}], start, end, 10)
        # A colleague in UTC is busy until shortly after the jump.
        busy_end = _z(jump + (mid - a) // 3 * MIN)
        case(
            f"spring_{name}_start_in_gap_after_busy",
            [
                {"tz": tz, "hours": {wd(d): [f"{hm(mid)}-11:00"]}},
                {
                    "tz": "UTC",
                    "hours": every("00:00-24:00"),
                    "busy": [[_z(jump - timedelta(hours=3)), busy_end]],
                },
            ],
            *window(d, 1, 2),
            15,
        )
    # Controls: boundaries at the very start or end of the gap, and short nights.
    for tz, d, g0, g1 in SPRING[:6]:
        name = short(tz)
        start, end = window(d)
        case(
            f"spring_{name}_end_at_gap_start",
            [{"tz": tz, "hours": every(f"20:00-{g0}")}],
            start,
            end,
        )
        case(
            f"spring_{name}_start_at_gap_end",
            [{"tz": tz, "hours": every(f"{g1}-09:00")}],
            start,
            end,
        )
    for tz, d, g0, g1 in SPRING[::3]:
        start, end = window(d)
        night = 8 * 60
        case(
            f"spring_{short(tz)}_short_night",
            [{"tz": tz, "hours": every("22:00-06:00")}],
            start,
            end,
            night - (mins(g1) - mins(g0)) + 1,
        )


def fall_cases():
    for tz, d, f0, f1 in FALL:
        a, b = mins(f0), mins(f1)
        mid = a + (b - a) // 2
        name = short(tz)
        start, end = window(d)
        # A start or end inside the repeated hour is reached twice: the entry stops
        # when the clock goes back and resumes when it reaches that time again.
        case(
            f"fall_{name}_start_in_repeat",
            [{"tz": tz, "hours": every(f"{hm(mid)}-12:00")}],
            start,
            end,
        )
        case(
            f"fall_{name}_end_in_repeat",
            [{"tz": tz, "hours": every(f"18:00-{hm(mid)}")}],
            start,
            end,
        )
        # Hours that lie wholly inside the repeated hour are covered twice.
        inner = f"{hm(a + 5)}-{hm(b - 5)}"
        case(f"fall_{name}_inside_repeat", [{"tz": tz, "hours": every(inner)}], start, end, 10)
        # The night that contains the repeated time is longer than the others.
        case(
            f"fall_{name}_long_night",
            [{"tz": tz, "hours": every("22:00-06:00")}],
            start,
            end,
            8 * 60 + (b - a),
        )
    for tz, d, f0, f1 in FALL[::3]:
        start, end = window(d, 1, 2)
        case(
            f"fall_{short(tz)}_long_day",
            [{"tz": tz, "hours": {wd(d): ["00:00-24:00"]}}],
            start,
            end,
            24 * 60 + mins(f1) - mins(f0),
        )


def apia_cases():
    # Samoa skipped Friday 2011-12-30 entirely: Thursday 23:59 -10:00 was followed by
    # Saturday 00:00 +14:00.
    tz = "Pacific/Apia"
    start, end = "2011-12-28T00:00:00Z", "2012-01-02T00:00:00Z"
    case(
        "skipped_day_apia_friday_hours", [{"tz": tz, "hours": {"fri": ["09:00-17:00"]}}], start, end
    )
    case(
        "skipped_day_apia_thursday_night",
        [{"tz": tz, "hours": {"thu": ["20:00-04:00"], "sat": ["10:00-12:00"]}}],
        start,
        end,
    )
    case(
        "skipped_day_apia_two_zones",
        [
            {"tz": tz, "hours": every("08:00-18:00")},
            {"tz": "Pacific/Honolulu", "hours": every("06:00-22:00")},
        ],
        start,
        end,
        60,
    )


WEEK = ("mon", "tue", "wed", "thu", "fri")


def ordinary_cases():
    case(
        "one_attendee_utc_week",
        [{"tz": "UTC", "hours": every("09:00-17:00", days=WEEK)}],
        "2025-06-02T00:00Z",
        "2025-06-09T00:00Z",
    )
    case(
        "new_york_and_berlin_summer",
        [
            {"tz": "America/New_York", "hours": every("09:00-17:00", days=WEEK)},
            {"tz": "Europe/Berlin", "hours": every("09:00-17:00", days=WEEK)},
        ],
        "2025-06-02T00:00:00Z",
        "2025-06-05T00:00:00Z",
    )
    case(
        "new_york_and_berlin_winter",
        [
            {"tz": "America/New_York", "hours": every("08:00-16:00", days=WEEK)},
            {"tz": "Europe/Berlin", "hours": every("10:00-18:00", days=WEEK)},
        ],
        "2025-01-13T00:00:00+01:00",
        "2025-01-15T00:00:00+01:00",
    )
    case(
        "tokyo_weekday_is_local",
        [
            {"tz": "Asia/Tokyo", "hours": every("09:00-18:00", days=WEEK)},
            {"tz": "Europe/London", "hours": every("08:00-17:00", days=WEEK)},
        ],
        "2025-07-06T00:00Z",
        "2025-07-12T12:00Z",
    )
    case(
        "kolkata_and_kathmandu",
        [
            {"tz": "Asia/Kolkata", "hours": every("09:30-18:00", days=WEEK)},
            {"tz": "Asia/Kathmandu", "hours": every("10:00-17:15", days=WEEK)},
        ],
        "2025-06-02T00:00Z",
        "2025-06-04T00:00Z",
    )
    case(
        "busy_overlapping_and_touching",
        [
            {
                "tz": "Europe/Berlin",
                "hours": every("09:00-17:00", days=WEEK),
                "busy": [
                    ["2025-06-03T10:00:00+02:00", "2025-06-03T11:00:00+02:00"],
                    ["2025-06-03T09:30:00Z", "2025-06-03T10:30:00Z"],
                    ["2025-06-03T08:30Z", "2025-06-03T09:30Z"],
                ],
            },
            {
                "tz": "Europe/London",
                "hours": every("08:00-16:00", days=WEEK),
                "busy": [["2025-06-03T13:00:00+01:00", "2025-06-03T13:15:00+01:00"]],
            },
        ],
        "2025-06-03T00:00Z",
        "2025-06-04T00:00Z",
        15,
    )
    case(
        "busy_outside_window",
        [
            {
                "tz": "UTC",
                "hours": every("09:00-17:00"),
                "busy": [
                    ["2025-06-01T09:00Z", "2025-06-03T10:00Z"],
                    ["2025-06-03T16:00Z", "2025-06-09T00:00Z"],
                ],
            }
        ],
        "2025-06-03T00:00Z",
        "2025-06-04T00:00Z",
    )
    case(
        "min_exactly_met",
        [
            {
                "tz": "UTC",
                "hours": every("09:00-12:00"),
                "busy": [["2025-06-03T10:30Z", "2025-06-03T11:00Z"]],
            }
        ],
        "2025-06-03T00:00Z",
        "2025-06-04T00:00Z",
        90,
    )
    case(
        "min_just_missed",
        [
            {
                "tz": "UTC",
                "hours": every("09:00-12:00"),
                "busy": [["2025-06-03T10:29Z", "2025-06-03T11:00Z"]],
            }
        ],
        "2025-06-03T00:00Z",
        "2025-06-04T00:00Z",
        90,
    )
    case(
        "adjacent_entries_merge_before_min",
        [{"tz": "Asia/Kolkata", "hours": every("09:00-12:00", "12:00-17:00")}],
        "2025-06-03T00:00Z",
        "2025-06-05T00:00Z",
        400,
    )
    case(
        "overlapping_entries",
        [{"tz": "Asia/Kolkata", "hours": every("09:00-13:00", "11:00-17:00", "16:30-17:30")}],
        "2025-06-03T00:00Z",
        "2025-06-04T00:00Z",
    )
    case(
        "whole_days_merge",
        [{"tz": "America/Sao_Paulo", "hours": every("00:00-24:00")}],
        "2025-06-02T05:00Z",
        "2025-06-06T17:30Z",
        5000,
    )
    case(
        "night_shift_overlaps_next_day",
        [
            {
                "tz": "Europe/Berlin",
                "hours": {"fri": ["20:00-04:00"], "sat": ["02:00-10:00"], "sun": ["09:00-10:00"]},
            }
        ],
        "2025-06-05T00:00Z",
        "2025-06-10T00:00Z",
        600,
    )
    case(
        "end_24_00",
        [{"tz": "Asia/Tokyo", "hours": {"mon": ["18:00-24:00"], "tue": ["00:00-06:00"]}}],
        "2025-06-01T00:00Z",
        "2025-06-04T00:00Z",
        600,
    )
    case(
        "honolulu_previous_local_day",
        [{"tz": "Pacific/Honolulu", "hours": {"sun": ["18:00-02:00"]}}],
        "2025-06-02T00:00Z",
        "2025-06-02T23:00Z",
    )
    case(
        "kiritimati_next_local_day",
        [
            {"tz": "Pacific/Kiritimati", "hours": {"mon": ["09:00-17:00"]}},
            {"tz": "Pacific/Honolulu", "hours": {"sun": ["06:00-23:00"]}},
        ],
        "2025-06-01T00:00Z",
        "2025-06-02T00:00Z",
    )
    case(
        "window_clips_slots",
        [{"tz": "Europe/Berlin", "hours": every("09:00-17:00")}],
        "2025-06-02T10:30:00+02:00",
        "2025-06-03T16:15:00+02:00",
    )
    case(
        "nobody_works",
        [{"tz": "UTC", "hours": {}}, {"tz": "UTC", "hours": every("09:00-17:00")}],
        "2025-06-02T00:00Z",
        "2025-06-09T00:00Z",
    )
    case(
        "southern_hemisphere_three_zones",
        [
            {"tz": "Australia/Lord_Howe", "hours": every("08:00-17:00", days=WEEK)},
            {"tz": "Pacific/Chatham", "hours": every("10:00-19:00", days=WEEK)},
            {"tz": "Australia/Adelaide", "hours": every("07:30-16:30", days=WEEK)},
        ],
        "2025-06-01T12:00Z",
        "2025-06-05T12:00Z",
    )
    case(
        "busy_key_missing_and_empty",
        [
            {"tz": "America/Los_Angeles", "hours": every("07:00-15:00", days=WEEK)},
            {"tz": "America/New_York", "hours": every("09:00-17:00", days=WEEK), "busy": []},
        ],
        "2025-06-02T00:00Z",
        "2025-06-04T00:00Z",
    )
    case(
        "min_longer_than_anything",
        [{"tz": "UTC", "hours": every("09:00-17:00")}],
        "2025-06-02T00:00Z",
        "2025-06-09T00:00Z",
        481,
    )
    case(
        "busy_splits_and_touches_edges",
        [
            {
                "tz": "Asia/Kathmandu",
                "hours": every("09:00-17:00"),
                "busy": [
                    ["2025-06-03T09:00:00+05:45", "2025-06-03T09:45:00+05:45"],
                    ["2025-06-03T06:00:00Z", "2025-06-03T06:15:00Z"],
                    ["2025-06-03T16:00:00+05:45", "2025-06-03T17:00:00+05:45"],
                ],
            }
        ],
        "2025-06-03T00:00Z",
        "2025-06-04T00:00Z",
        15,
    )
    case(
        "different_hours_per_weekday",
        [
            {
                "tz": "America/Chicago",
                "hours": {
                    "mon": ["09:00-17:00"],
                    "tue": ["12:00-20:00"],
                    "wed": ["07:00-11:00", "13:00-15:00"],
                    "fri": ["09:00-12:00"],
                },
            },
            {"tz": "Europe/London", "hours": every("09:00-18:00", days=WEEK)},
        ],
        "2025-06-02T00:00Z",
        "2025-06-07T00:00Z",
    )


def mixed_dst_cases():
    # Two attendees whose clocks change on different dates in the same window.
    case(
        "europe_and_us_change_on_different_weeks",
        [
            {"tz": "America/New_York", "hours": every("09:00-17:00", days=WEEK)},
            {"tz": "Europe/London", "hours": every("09:00-17:00", days=WEEK)},
        ],
        "2025-03-07T00:00Z",
        "2025-04-01T00:00Z",
    )
    case(
        "sydney_and_london_both_change",
        [
            {"tz": "Australia/Sydney", "hours": every("07:00-19:00")},
            {"tz": "Europe/London", "hours": every("18:00-06:00")},
        ],
        "2025-03-28T00:00Z",
        "2025-04-08T00:00Z",
    )
    case(
        "lord_howe_half_hour_night",
        [{"tz": "Australia/Lord_Howe", "hours": every("23:00-02:15")}],
        "2025-10-02T00:00Z",
        "2025-10-07T00:00Z",
    )
    case(
        "new_york_spring_busy_given_in_local_offsets",
        [
            {
                "tz": "America/New_York",
                "hours": every("00:00-24:00"),
                "busy": [
                    ["2025-03-09T01:00:00-05:00", "2025-03-09T03:30:00-04:00"],
                    ["2025-03-09T04:00:00-04:00", "2025-03-09T05:00:00-04:00"],
                ],
            }
        ],
        "2025-03-09T00:00:00-05:00",
        "2025-03-09T12:00:00-04:00",
        1,
    )
    case(
        "new_york_fall_busy_in_second_one_thirty",
        [
            {
                "tz": "America/New_York",
                "hours": every("00:00-24:00"),
                "busy": [["2025-11-02T01:30:00-05:00", "2025-11-02T02:00:00-05:00"]],
            }
        ],
        "2025-11-02T00:00:00-04:00",
        "2025-11-02T04:00:00-05:00",
        1,
    )


def error_cases():
    ok = {"tz": "UTC", "hours": every("09:00-17:00")}
    s, e = "2025-06-02T00:00Z", "2025-06-03T00:00Z"

    def err(name, attendees, start=s, end=e, min_minutes=30):
        ERRORS[name] = {
            "attendees": attendees,
            "start": start,
            "end": end,
            "min_minutes": min_minutes,
        }

    err("no_attendees", [])
    err("unknown_zone", [ok, {"tz": "Mars/Olympus_Mons", "hours": {}}])
    err("bad_weekday", [{"tz": "UTC", "hours": {"monday": ["09:00-17:00"]}}])
    err("one_digit_hour", [{"tz": "UTC", "hours": {"sun": ["9:00-17:00"]}}])
    err("minute_60", [{"tz": "UTC", "hours": {"mon": ["09:00-17:60"]}}])
    err("start_24_00", [{"tz": "UTC", "hours": {"mon": ["24:00-06:00"]}}])
    err("end_24_30", [{"tz": "UTC", "hours": {"mon": ["20:00-24:30"]}}])
    err("empty_entry", [{"tz": "UTC", "hours": {"mon": ["09:00-09:00"]}}])
    err("start_without_offset", [ok], start="2025-06-02T00:00")
    err("busy_without_offset", [{**ok, "busy": [["2025-06-02T10:00", "2025-06-02T11:00"]]}])
    err("start_not_whole_minute", [ok], start="2025-06-02T00:00:30Z")
    err("busy_not_whole_minute", [{**ok, "busy": [["2025-06-02T10:00Z", "2025-06-02T11:00:01Z"]]}])
    err("garbage_timestamp", [ok], end="tomorrow")
    err("start_equals_end", [ok], end=s)
    err("start_equals_end_other_offset", [ok], end="2025-06-02T02:00:00+02:00")
    err("start_after_end_other_offset", [ok], start="2025-06-02T23:30:00-01:00")
    err("busy_empty", [{**ok, "busy": [["2025-06-02T10:00Z", "2025-06-02T10:00Z"]]}])
    err(
        "busy_inverted_outside_window",
        [{**ok, "busy": [["2025-07-02T11:00Z", "2025-07-02T10:00:00+02:00"]]}],
    )
    err("min_zero", [ok], min_minutes=0)
    err("min_negative", [ok], min_minutes=-30)


# ---------------------------------------------------------------- output


def main() -> None:
    ordinary_cases()
    spring_cases()
    fall_cases()
    apia_cases()
    mixed_dst_cases()
    error_cases()

    expected = {}
    mismatches = []
    for name, c in CASES.items():
        want = oracle(c["attendees"], c["start"], c["end"], c["min_minutes"])
        got = reference.free_slots(c["attendees"], c["start"], c["end"], c["min_minutes"])
        if got != want:
            mismatches.append((name, want, got))
        expected[name] = want
    for name, c in ERRORS.items():
        try:
            reference.free_slots(c["attendees"], c["start"], c["end"], c["min_minutes"])
            mismatches.append((name, "ValueError", "no error"))
        except ValueError:
            pass
    if mismatches:
        for m in mismatches:
            print("MISMATCH", *m, sep="\n  ")
        raise SystemExit(1)

    lines = [
        "import pytest",
        "from freebusy import free_slots",
        "",
        "# name -> (attendees, start, end, min_minutes, expected slots)",
        "CASES = {",
        *(
            f"    {name!r}: ({c['attendees']!r}, {c['start']!r}, {c['end']!r}, "
            f"{c['min_minutes']!r}, {expected[name]!r}),"
            for name, c in CASES.items()
        ),
        "}",
        "",
        "# name -> (attendees, start, end, min_minutes): each must raise ValueError",
        "ERRORS = {",
        *(
            f"    {name!r}: ({c['attendees']!r}, {c['start']!r}, {c['end']!r}, "
            f"{c['min_minutes']!r}),"
            for name, c in ERRORS.items()
        ),
        "}",
        "",
        "",
        '@pytest.mark.parametrize("name", list(CASES))',
        "def test_free_slots(name):",
        "    attendees, start, end, min_minutes, expected = CASES[name]",
        "    got = free_slots(attendees, start, end, min_minutes)",
        "    assert [tuple(slot) for slot in got] == expected",
        "",
        "",
        '@pytest.mark.parametrize("name", list(ERRORS))',
        "def test_invalid(name):",
        "    with pytest.raises(ValueError):",
        "        free_slots(*ERRORS[name])",
        "",
    ]
    out = Path(__file__).parent / "hidden" / "test_hidden_freebusy.py"
    out.write_text("\n".join(lines))
    print(f"{len(CASES)} cases, {len(ERRORS)} error cases")


if __name__ == "__main__":
    main()
