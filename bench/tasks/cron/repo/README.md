# cronnext

Implement `next_run(expr: str, after: datetime) -> datetime` in `cronnext.py`.

It returns the first time **strictly after** `after` at which the five-field cron
expression `expr` fires. All datetimes are naive (no time zones). The result always has
seconds and microseconds set to zero. Use only the standard library.

## Expression format

Five fields separated by whitespace (leading and trailing whitespace is ignored):

| field        | range | names                       |
|--------------|-------|-----------------------------|
| minute       | 0-59  |                             |
| hour         | 0-23  |                             |
| day of month | 1-31  |                             |
| month        | 1-12  | `JAN`-`DEC`                 |
| day of week  | 0-7   | `SUN`-`SAT` (0 and 7 = Sunday) |

Each field is a comma-separated list of items. An item is one of:

- `*`: every value in the field's range
- `N`: one value
- `N-M`: an inclusive range, with `N <= M`
- any of the above followed by `/S`: every `S`-th value, starting from the first value of
  the item. `*/15` in the minute field is 0, 15, 30, 45; `10-30/5` is 10, 15, 20, 25, 30;
  `N/S` means "from `N` to the end of the field's range, every `S`": `5/20` in the
  minute field is 5, 25, 45.

Names are case insensitive and can be used wherever a number can, including in ranges
(`MON-FRI`) but not as a step.

In the day-of-week field, 7 means Sunday, so `5-7` is Friday, Saturday, Sunday.

## Day of month and day of week

- If **both** the day-of-month field and the day-of-week field are restricted, a day
  matches when **either** of them matches.
- Otherwise a day must match both (the unrestricted one matches every day anyway).
- A field counts as unrestricted only if it is exactly `*`. `*/2` is restricted.

## Errors

Raise `ValueError` when:

- the expression does not have exactly five fields;
- a value is out of range, not a number or known name, or empty (`1,,2`);
- a range is reversed (`30-10`), or a step is zero, negative or not a number;
- the expression can never fire (for example `0 0 30 2 *`, February 30th).

## Performance

A call must return quickly (well under a second), including for expressions that fire
rarely, such as `0 0 29 2 *`.

## Running the tests

    python -m pytest -q
