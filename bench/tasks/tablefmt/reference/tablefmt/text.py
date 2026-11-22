"""Helpers for laying out cell text: padding, truncation and wrapping."""

import unicodedata

ELLIPSIS = "…"


def char_width(ch: str) -> int:
    """Columns one character takes up in a terminal."""
    if unicodedata.combining(ch):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def width(text: str) -> int:
    return sum(char_width(ch) for ch in text)


def _prefix(text: str, limit: int) -> str:
    """The longest start of `text` no wider than `limit`, with combining marks kept."""
    used = 0
    for i, ch in enumerate(text):
        w = char_width(ch)
        if used + w > limit:
            return text[:i]
        used += w
    return text


def pad(text: str, size: int, align: str) -> str:
    extra = size - width(text)
    if extra <= 0:
        return text
    if align == "right":
        return " " * extra + text
    if align == "center":
        left = extra // 2
        return " " * left + text + " " * (extra - left)
    return text + " " * extra


def truncate(text: str, size: int) -> str:
    if width(text) <= size:
        return text
    return _prefix(text, size - 1) + ELLIPSIS


def wrap(text: str, size: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if width(candidate) <= size:
            current = candidate
            continue
        if current:
            lines.append(current)
        while width(word) > size:
            head = _prefix(word, size)
            lines.append(head)
            word = word[len(head) :]
        current = word
    if current or not lines:
        lines.append(current)
    return lines
