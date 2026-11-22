"""Helpers for laying out cell text: padding, truncation and wrapping."""

ELLIPSIS = "…"


def pad(text: str, size: int, align: str) -> str:
    extra = size - len(text)
    if extra <= 0:
        return text
    if align == "right":
        return " " * extra + text
    if align == "center":
        left = extra // 2
        return " " * left + text + " " * (extra - left)
    return text + " " * extra


def truncate(text: str, size: int) -> str:
    if len(text) <= size:
        return text
    return text[: size - 1] + ELLIPSIS


def wrap(text: str, size: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= size:
            current = candidate
            continue
        if current:
            lines.append(current)
        while len(word) > size:
            lines.append(word[:size])
            word = word[size:]
        current = word
    if current or not lines:
        lines.append(current)
    return lines
