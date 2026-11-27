# freebusy

Implement `free_slots(attendees: list[dict], start: str, end: str, min_minutes: int)
-> list[tuple[str, str]]` in `freebusy.py`.

It returns the periods between `start` and `end` in which every attendee is within their
working hours and none of them is busy, for attendees who live in different time zones.
Use only the standard library (`zoneinfo` is fine).

## Arguments

- `attendees`: a non-empty list of dicts, one per attendee, with these keys:
  - `"tz"`: the attendee's IANA time zone name, such as `"America/New_York"`.
  - `"hours"`: a dict from weekday name (`"mon"`, `"tue"`, `"wed"`, `"thu"`, `"fri"`,
    `"sat"`, `"sun"`) to a list of working-hours entries (see below). A weekday that is
    missing, or whose list is empty, has no working hours.
  - `"busy"` (optional; missing means none): a list of `[start, end]` pairs of
    timestamps, the periods in which the attendee is busy.
- `start`, `end`: timestamps bounding the search window.
- `min_minutes`: a positive `int`, the shortest slot worth returning.

## Timestamps

A timestamp is a string `YYYY-MM-DDTHH:MM` or `YYYY-MM-DDTHH:MM:SS`, followed by a UTC
offset: `Z`, or `+HH:MM` / `-HH:MM`. Examples: `2025-03-09T10:00Z`,
`2025-03-09T10:00:00-04:00`, `2025-03-09T15:45:00+05:45`. A timestamp denotes one
instant; the offset only says how it is written and need not be the attendee's offset.

Every timestamp must fall on a whole minute (seconds, if given, are `00`).

## Working hours

A working-hours entry is a string `"HH:MM-HH:MM"` (a start and an end time, exactly two
digits each, `HH` from `00` to `23`, `MM` from `00` to `59`). The end may also be
`24:00`, meaning midnight at the end of the day. Start and end must differ.

An entry listed under weekday `W` applies to every date `D` that falls on `W`, and
covers these local dates and times (start included, end excluded):

- if the end is later than the start (`"09:00-17:00"`): from the start time to the end
  time on `D`;
- if the end is `24:00` or earlier than the start (`"22:00-06:00"`): from the start time
  on `D` to the end time on the day after `D` (`24:00` is `00:00` of the day after `D`).
  So `"22:00-06:00"` under `"fri"` covers Friday 22:00 to Saturday 06:00.

**An attendee is working at an instant if the date and time shown at that instant by a
wall clock in their time zone is covered by one of their entries.** Entries may overlap,
within a day or across days. Only what the wall clock shows matters, so on days when the
clocks change, an entry can cover more or less time than its clock times suggest:

- When the clocks are set forward, the skipped times are never shown, so nobody works
  during them. In `America/New_York` on Sunday 2025-03-09 the clocks jump from 02:00 EST
  to 03:00 EDT at `07:00Z`. That day an entry `"02:30-04:00"` covers `07:00Z` to
  `08:00Z` (03:00 to 04:00 EDT), `"02:10-02:50"` covers nothing, and `"22:00-02:30"`
  under `"sat"` covers `03:00Z` to `07:00Z`. The same holds when the jump is shorter or
  longer than an hour, when it skips midnight, and when it skips a whole date.
- When the clocks are set back, the repeated times are shown twice and are covered both
  times. In `America/New_York` on Sunday 2025-11-02 the clocks go from 01:59 EDT back
  to 01:00 EST at `06:00Z`, so 01:00 to 01:59 is shown from `05:00Z` and again from
  `06:00Z`. That day `"00:00-03:00"` covers `04:00Z` to `08:00Z` (four hours);
  `"01:30-09:00"` covers `05:30Z` to `06:00Z` and `06:30Z` to `14:00Z`, but not
  `06:00Z` to `06:30Z`, when the clock shows 01:00 to 01:29 for the second time; and
  `"22:00-01:30"` under `"sat"` covers `02:00Z` to `05:30Z` and again `06:00Z` to
  `06:30Z`.

## Busy periods

A busy pair `[s, e]` makes its attendee busy from `s` (included) to `e` (excluded).
Busy periods may overlap or touch each other, and may extend outside the search window.

## Result

An instant is free if it is at or after `start`, before `end`, every attendee is
working at it and no attendee is busy at it.

Return the free time as a list of `(slot_start, slot_end)` tuples, one per maximal free
period (free time is never split at a point where it continues, for example where one
working-hours entry or day ends and the next begins), keeping only those that last at
least `min_minutes` minutes. Sort them by start. Write each instant in UTC as
`YYYY-MM-DDTHH:MM:SSZ`, for example `("2025-03-10T13:00:00Z", "2025-03-10T15:00:00Z")`.
Return `[]` when nothing is free.

## Errors

Raise `ValueError` when:

- `attendees` is empty;
- a `"tz"` is not a time zone known to `zoneinfo`;
- an `"hours"` key is not one of the seven weekday names, or an entry is not of the form
  above (`"9:00-17:00"`, `"09:00-17:60"`, `"24:00-06:00"`, `"09:00-09:00"`);
- a timestamp is not of the form above, has no UTC offset (`2025-03-09T10:00`), or is
  not on a whole minute (`2025-03-09T10:00:30Z`);
- `start` is not before `end`, or a busy pair's start is not before its end;
- `min_minutes` is less than 1.

## Running the tests

    python -m pytest -q
