# tablefmt

Print rows as a plain-text table that lines up in a terminal. Standard library only.

```python
from tablefmt import render

print(render([["notes.txt", 3], ["photo.png", 1250]], ["name", "KB"]))
```

```
name      |   KB
----------+-----
notes.txt |    3
photo.png | 1250
```

## `render(rows, headers=None, *, align=None, max_width=None, wrap=False) -> str`

- `rows` is a sequence of rows, each a sequence of cells. Cells are shown with `str()`,
  and `None` is shown as an empty cell. Short rows are filled with empty cells.
- `headers`, if given, is printed first, followed by a rule of `-` with `-+-` between
  columns.
- Columns are separated by ` | ` and padded to the width of their widest cell, header
  included. Trailing spaces are removed from every line.
- `align` is `"left"`, `"right"` or `"center"` for every column, or a sequence with one
  of those per column. `"center"` puts the odd space on the right. By default a column
  is right-aligned when all its non-empty cells are numbers (`int` or `float`, not
  `bool`), and left-aligned otherwise. Headers follow their column's alignment.
- `max_width` limits how wide a cell may be (at least 2). A longer cell is cut short
  and ends with `…`, or, with `wrap=True`, continues on the next lines: it is broken at
  spaces, and a word that does not fit on a line of its own is split. A row is as tall
  as its tallest cell.

The result has no trailing newline. `ValueError` is raised for an unknown alignment, an
`align` sequence of the wrong length, or `max_width` below 2.

## Running the tests

    python -m pytest -q
