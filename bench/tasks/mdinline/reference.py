"""Inline Markdown (emphasis, code spans, backslash escapes) to HTML. See README.md."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

_ASCII_PUNCT = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _is_space(ch: str) -> bool:
    return ch in "\t\n\f\r" or unicodedata.category(ch) == "Zs"


def _is_punct(ch: str) -> bool:
    return ch in _ASCII_PUNCT or unicodedata.category(ch)[0] in "PS"


@dataclass
class _Node:
    html: str


@dataclass
class _Delim:
    node: _Node
    char: str
    count: int
    length: int
    can_open: bool
    can_close: bool


def render(text: str) -> str:
    """Render one line of inline Markdown as HTML."""
    if "\n" in text or "\r" in text:
        raise ValueError("text must be a single line")
    nodes: list[_Node] = []
    delims: list[_Delim] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            nodes.append(_Node(_escape("".join(buf))))
            buf.clear()

    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n and text[i + 1] in _ASCII_PUNCT:
            buf.append(text[i + 1])
            i += 2
        elif ch == "`":
            j = i
            while j < n and text[j] == "`":
                j += 1
            size = j - i
            # Look for the next backtick string of exactly the same length.
            k = j
            close = -1
            while k < n:
                if text[k] != "`":
                    k += 1
                    continue
                m = k
                while m < n and text[m] == "`":
                    m += 1
                if m - k == size:
                    close = k
                    break
                k = m
            if close < 0:
                buf.append(text[i:j])
                i = j
                continue
            content = text[j:close]
            if (
                len(content) >= 2
                and content[0] == " "
                and content[-1] == " "
                and content.strip(" ")
            ):
                content = content[1:-1]
            flush()
            nodes.append(_Node("<code>" + _escape(content) + "</code>"))
            i = close + size
        elif ch in "*_":
            j = i
            while j < n and text[j] == ch:
                j += 1
            before = text[i - 1] if i > 0 else " "
            after = text[j] if j < n else " "
            left = not _is_space(after) and (
                not _is_punct(after) or _is_space(before) or _is_punct(before)
            )
            right = not _is_space(before) and (
                not _is_punct(before) or _is_space(after) or _is_punct(after)
            )
            if ch == "*":
                can_open, can_close = left, right
            else:
                can_open = left and (not right or _is_punct(before))
                can_close = right and (not left or _is_punct(after))
            flush()
            node = _Node(text[i:j])
            nodes.append(node)
            delims.append(_Delim(node, ch, j - i, j - i, can_open, can_close))
            i = j
        else:
            buf.append(ch)
            i += 1
    flush()
    _process_emphasis(nodes, delims)
    return "".join(node.html for node in nodes)


def _index(nodes: list[_Node], node: _Node) -> int:
    return next(k for k, other in enumerate(nodes) if other is node)


def _process_emphasis(nodes: list[_Node], delims: list[_Delim]) -> None:
    """The algorithm of the CommonMark spec's appendix, "Processing emphasis"."""
    # For each kind of closer, the delimiter below which no opener for it is left.
    # Kept by identity: once that delimiter is removed, the search runs to the start.
    bottoms: dict[tuple[str, bool, int], _Delim | None] = {}
    c = 0
    while c < len(delims):
        closer = delims[c]
        if not closer.can_close:
            c += 1
            continue
        key = (closer.char, closer.can_open, closer.length % 3)
        bottom = bottoms.get(key)
        o = c - 1
        found = False
        while o >= 0 and delims[o] is not bottom:
            opener = delims[o]
            if opener.char == closer.char and opener.can_open:
                odd = (
                    (closer.can_open or opener.can_close)
                    and (opener.length + closer.length) % 3 == 0
                    and not (opener.length % 3 == 0 and closer.length % 3 == 0)
                )
                if not odd:
                    found = True
                    break
            o -= 1
        if not found:
            bottoms[key] = delims[c - 1] if c > 0 else None
            if not closer.can_open:
                del delims[c]
            else:
                c += 1
            continue
        opener = delims[o]
        use = 2 if opener.count >= 2 and closer.count >= 2 else 1
        tag = "strong" if use == 2 else "em"
        opener.count -= use
        closer.count -= use
        opener.node.html = opener.node.html[:-use]
        closer.node.html = closer.node.html[use:]
        nodes.insert(_index(nodes, opener.node) + 1, _Node(f"<{tag}>"))
        nodes.insert(_index(nodes, closer.node), _Node(f"</{tag}>"))
        # Delimiters between the pair can no longer match anything.
        del delims[o + 1 : c]
        c = o + 1
        if opener.count == 0:
            del delims[o]
            c -= 1
        if closer.count == 0:
            del delims[c]
