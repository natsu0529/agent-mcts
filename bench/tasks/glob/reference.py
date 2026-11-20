"""Reference solution, used only to check that the hidden tests are satisfiable."""


def _find_group(s, start):
    """Return (open, close, [alternatives]) of the first real brace group at/after start."""
    i = start
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth, j, parts, last = 0, i, [], i + 1
            while j < len(s):
                d = s[j]
                if d == "\\":
                    j += 2
                    continue
                if d == "{":
                    depth += 1
                elif d == "}":
                    depth -= 1
                    if depth == 0:
                        parts.append(s[last:j])
                        break
                elif d == "," and depth == 1:
                    parts.append(s[last:j])
                    last = j + 1
                j += 1
            else:
                j = None
            if j is not None and j < len(s) and len(parts) > 1:
                return i, j, parts
        i += 1
    return None


def _expand(s):
    found = _find_group(s, 0)
    if found is None:
        return [s]
    i, j, parts = found
    out = []
    for part in parts:
        out.extend(_expand(s[:i] + part + s[j + 1 :]))
    return out


def _tokens(seg):
    out, i = [], 0
    while i < len(seg):
        c = seg[i]
        if c == "\\":
            out.append(("lit", seg[i + 1] if i + 1 < len(seg) else "\\"))
            i += 2
        elif c == "*":
            out.append(("star",))
            i += 1
        elif c == "?":
            out.append(("any",))
            i += 1
        elif c == "[":
            j = i + 1
            negate = j < len(seg) and seg[j] == "!"
            if negate:
                j += 1
            members, first, closed = [], True, False
            while j < len(seg):
                d = seg[j]
                if d == "]" and not first:
                    closed = True
                    break
                if d == "\\" and j + 1 < len(seg):
                    members.append(("c", seg[j + 1]))
                    j += 2
                else:
                    members.append(("r", d))
                    j += 1
                first = False
            if not closed:
                out.append(("lit", "["))
                i += 1
                continue
            chars, k = set(), 0
            ranges = []
            while k < len(members):
                if k + 2 < len(members) and members[k + 1] == ("r", "-"):
                    ranges.append((members[k][1], members[k + 2][1]))
                    k += 3
                else:
                    chars.add(members[k][1])
                    k += 1
            out.append(("set", negate, chars, ranges))
            i = j + 1
        else:
            out.append(("lit", c))
            i += 1
    return out


def _one(tok, ch):
    if tok[0] == "lit":
        return tok[1] == ch
    if tok[0] == "any":
        return True
    _, negate, chars, ranges = tok
    inside = ch in chars or any(a <= ch <= b for a, b in ranges)
    return inside != negate


def _seg(pat, seg):
    toks = _tokens(pat)
    if seg.startswith(".") and not (toks and toks[0] == ("lit", ".")):
        return False

    def go(ti, si):
        if ti == len(toks):
            return si == len(seg)
        if toks[ti][0] == "star":
            return any(go(ti + 1, k) for k in range(si, len(seg) + 1))
        return si < len(seg) and _one(toks[ti], seg[si]) and go(ti + 1, si + 1)

    return go(0, 0)


def _segs(pats, segs):
    if not pats:
        return not segs
    if pats[0] == "**":
        if _segs(pats[1:], segs):
            return True
        return bool(segs) and not segs[0].startswith(".") and _segs(pats, segs[1:])
    return bool(segs) and _seg(pats[0], segs[0]) and _segs(pats[1:], segs[1:])


def match(pattern: str, path: str) -> bool:
    segs = path.split("/") if path else []
    for p in _expand(pattern):
        pats = p.split("/") if p else []
        if _segs(pats, segs):
            return True
    return False
