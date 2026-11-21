# isoduration

Implement `add_duration(start: datetime, duration: str) -> datetime` in
`isoduration.py`. It parses an ISO 8601 duration such as `P1Y2M10DT2H30M` and returns
`start` moved by that duration, following the rules below. All datetimes are naive (no
time zones, no daylight saving). Use only the standard library.

## Syntax

```
[sign] P [nY] [nM] [nW] [nD] [T [nH] [nM] [nS]]
```

- `sign` is optional: `-` makes the whole duration negative, `+` changes nothing.
- `P` is required. The designators are upper case only, and components must appear in
  the order shown. Each component appears at most once.
- `M` before `T` means months; `M` after `T` means minutes.
- `n` is one or more ASCII digits (leading zeros are fine). Only the **last** component
  written may have a fractional part, using `.` or `,` as the decimal separator, with at
  least one digit on each side (`PT1.5S`, `PT1,5S`). Fractions are not allowed on years
  or months.
- `W` (weeks) cannot be combined with any other component: `P2W` is valid, `P2W3D` is
  not.
- At least one component is required, and `T` must be followed by at least one time
  component. So `P`, `PT`, `P1DT` are invalid.
- No whitespace anywhere. A sign on a component (`P-1D`) is invalid.

Raise `ValueError` for anything that does not follow this syntax.

## Applying a duration

Apply the parts in this order:

1. **Years and months together.** Move by `12 * years + months` calendar months in one
   step. If the day of month does not exist in the target month, use the last day of
   that month. Hours, minutes and seconds are kept. So 2024-01-31 plus `P1M` is
   2024-02-29, and 2024-02-29 plus `P1Y1M` is 2025-03-29 (13 months in one step, not one
   year and then one month).
2. **Weeks and days**, as exact multiples of 7 days and 1 day.
3. **Hours, minutes and seconds**, as exact amounts of time.

Fractions are exact amounts of time: `P0.5D` is 12 hours, `PT0.5H` is 30 minutes,
`P0.5W` is 3.5 days. The result keeps microsecond precision; round any finer remainder to
the nearest microsecond.

A negative duration applies every part negated, in the same order. So 2024-03-31 minus
`P1M` is 2024-02-29, and 2024-03-01 minus `P1M1D` is 2024-01-31 (first back one month to
2024-02-01, then back one day).

Raise `ValueError` if the result (or an intermediate step) falls outside the range
that `datetime` supports.

## Running the tests

    python -m pytest -q
