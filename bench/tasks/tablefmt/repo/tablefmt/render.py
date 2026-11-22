"""Render rows as a plain-text table."""

from collections.abc import Sequence
from typing import Any

from . import text

ALIGNMENTS = ("left", "right", "center")


def _cell(value: Any) -> str:
    return "" if value is None else str(value)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _alignments(align: str | Sequence[str] | None, grid: list[list[Any]], ncols: int) -> list[str]:
    if align is None:
        result = []
        for col in range(ncols):
            values = [row[col] for row in grid if row[col] is not None]
            numeric = bool(values) and all(_is_number(v) for v in values)
            result.append("right" if numeric else "left")
        return result
    result = [align] * ncols if isinstance(align, str) else list(align)
    if len(result) != ncols:
        raise ValueError(f"align has {len(result)} entries for {ncols} columns")
    for a in result:
        if a not in ALIGNMENTS:
            raise ValueError(f"unknown alignment {a!r}")
    return result


def render(
    rows: Sequence[Sequence[Any]],
    headers: Sequence[Any] | None = None,
    *,
    align: str | Sequence[str] | None = None,
    max_width: int | None = None,
    wrap: bool = False,
) -> str:
    if max_width is not None and max_width < 2:
        raise ValueError("max_width must be at least 2")
    ncols = max([len(r) for r in rows] + [len(headers) if headers is not None else 0], default=0)
    if ncols == 0:
        return ""
    grid = [[r[i] if i < len(r) else None for i in range(ncols)] for r in rows]
    aligns = _alignments(align, grid, ncols)

    def lines_of(value: Any) -> list[str]:
        s = _cell(value)
        if max_width is None or len(s) <= max_width:
            return [s]
        return text.wrap(s, max_width) if wrap else [text.truncate(s, max_width)]

    blocks = [[lines_of(v) for v in row] for row in grid]
    head = None
    if headers is not None:
        head = [lines_of(headers[i] if i < len(headers) else None) for i in range(ncols)]
    every = blocks + ([head] if head is not None else [])
    sizes = [max(len(line) for row in every for line in row[col]) for col in range(ncols)]

    out: list[str] = []

    def emit(row: list[list[str]]) -> None:
        height = max(len(cell) for cell in row)
        for k in range(height):
            parts = [
                text.pad(cell[k] if k < len(cell) else "", sizes[col], aligns[col])
                for col, cell in enumerate(row)
            ]
            out.append(" | ".join(parts).rstrip())

    if head is not None:
        emit(head)
        out.append("-+-".join("-" * size for size in sizes))
    for row in blocks:
        emit(row)
    return "\n".join(out)
