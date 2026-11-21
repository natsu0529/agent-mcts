"""Reference solution for the semver task. Mirrors node-semver 7.x with default options."""

import re

_NUM = r"0|[1-9]\d*"
_PREID = r"(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
_PRE = rf"(?:-({_PREID}(?:\.{_PREID})*))"
_BUILD = r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)"
_XID = rf"(?:{_NUM}|[xX*])"
_PARTIAL = rf"v?({_XID})(?:\.({_XID})(?:\.({_XID}){_PRE}?{_BUILD}?)?)?"

_FULL_RE = re.compile(rf"^v?({_NUM})\.({_NUM})\.({_NUM}){_PRE}?{_BUILD}?$")
_XRANGE_RE = re.compile(rf"^(<=|>=|<|>|=)?{_PARTIAL}$")
_TILDE_RE = re.compile(rf"^~{_PARTIAL}$")
_CARET_RE = re.compile(rf"^\^{_PARTIAL}$")
_HYPHEN_RE = re.compile(rf"^\s*({_PARTIAL})\s+-\s+({_PARTIAL})\s*$")
_COMPARATOR_RE = re.compile(rf"^(<=|>=|<|>|=)?v?({_NUM})\.({_NUM})\.({_NUM}){_PRE}?{_BUILD}?$")


def _key(parts, pre):
    ids = tuple((0, int(i), "") if i.isdigit() else (1, 0, i) for i in pre)
    return tuple(parts), ids


def _cmp(a, b):
    (pa, ia), (pb, ib) = a, b
    if pa != pb:
        return -1 if pa < pb else 1
    if not ia or not ib:
        if ia == ib:
            return 0
        return 1 if not ia else -1
    if ia == ib:
        return 0
    return -1 if ia < ib else 1


def _version(text):
    m = _FULL_RE.match(text.strip())
    if not m:
        raise ValueError(f"invalid version: {text!r}")
    pre = m.group(4).split(".") if m.group(4) else []
    return _key([int(m.group(1)), int(m.group(2)), int(m.group(3))], pre), bool(pre)


def _isx(part):
    return part is None or part in ("x", "X", "*")


def _tilde(M, m, p, pr):
    if _isx(M):
        return ""
    if _isx(m):
        return f">={M}.0.0 <{int(M) + 1}.0.0-0"
    if _isx(p):
        return f">={M}.{m}.0 <{M}.{int(m) + 1}.0-0"
    if pr:
        return f">={M}.{m}.{p}-{pr} <{M}.{int(m) + 1}.0-0"
    return f">={M}.{m}.{p} <{M}.{int(m) + 1}.0-0"


def _caret(M, m, p, pr):
    if _isx(M):
        return ""
    if _isx(m):
        return f">={M}.0.0 <{int(M) + 1}.0.0-0"
    if _isx(p):
        if M == "0":
            return f">={M}.{m}.0 <{M}.{int(m) + 1}.0-0"
        return f">={M}.{m}.0 <{int(M) + 1}.0.0-0"
    low = f">={M}.{m}.{p}" + (f"-{pr}" if pr else "")
    if M == "0":
        if m == "0":
            return f"{low} <{M}.{m}.{int(p) + 1}-0"
        return f"{low} <{M}.{int(m) + 1}.0-0"
    return f"{low} <{int(M) + 1}.0.0-0"


def _xrange(op, M, m, p, pr):
    xM = _isx(M)
    xm = xM or _isx(m)
    xp = xm or _isx(p)
    if op == "=" and xp:
        op = ""
    if xM:
        return "<0.0.0-0" if op in (">", "<") else ""
    if op and xp:
        if xm:
            m = 0
        p = 0
        suffix = ""
        if op == ">":
            op = ">="
            if xm:
                M, m, p = int(M) + 1, 0, 0
            else:
                m, p = int(m) + 1, 0
        elif op == "<=":
            op = "<"
            if xm:
                M = int(M) + 1
            else:
                m = int(m) + 1
        if op == "<":
            suffix = "-0"
        return f"{op}{M}.{m}.{p}{suffix}"
    if xm:
        return f">={M}.0.0 <{int(M) + 1}.0.0-0"
    if xp:
        return f">={M}.{m}.0 <{M}.{int(m) + 1}.0-0"
    return (op or "") + f"{M}.{m}.{p}" + (f"-{pr}" if pr else "")


def _hyphen(f, t):
    fM, fm, fp, fpr = f
    tM, tm, tp, tpr = t
    if _isx(fM):
        low = ""
    elif _isx(fm):
        low = f">={fM}.0.0"
    elif _isx(fp):
        low = f">={fM}.{fm}.0"
    else:
        low = f">={fM}.{fm}.{fp}" + (f"-{fpr}" if fpr else "")
    if _isx(tM):
        high = ""
    elif _isx(tm):
        high = f"<{int(tM) + 1}.0.0-0"
    elif _isx(tp):
        high = f"<{tM}.{int(tm) + 1}.0-0"
    else:
        high = f"<={tM}.{tm}.{tp}" + (f"-{tpr}" if tpr else "")
    return f"{low} {high}".strip()


def _expand(token):
    for regex, fn in ((_TILDE_RE, _tilde), (_CARET_RE, _caret)):
        m = regex.match(token)
        if m:
            return fn(*m.groups())
    m = _XRANGE_RE.match(token)
    if m:
        return _xrange(*m.groups())
    raise ValueError(f"invalid comparator: {token!r}")


def _comparators(text):
    text = text.strip()
    m = _HYPHEN_RE.match(text)
    if m:
        g = m.groups()
        text = _hyphen(g[1:5], g[6:10])
    text = re.sub(r"(<=|>=|<|>|=)\s+", r"\1", text)
    text = re.sub(r"([~^])\s+", r"\1", text)
    out = []
    for token in text.split():
        for comp in _expand(token).split():
            cm = _COMPARATOR_RE.match(comp)
            if not cm:
                raise ValueError(f"invalid comparator: {comp!r}")
            pre = cm.group(5).split(".") if cm.group(5) else []
            parts = [int(cm.group(2)), int(cm.group(3)), int(cm.group(4))]
            out.append((cm.group(1) or "=", _key(parts, pre), bool(pre)))
    return out


def _test(op, a, b):
    c = _cmp(a, b)
    return {
        "=": c == 0,
        "<": c < 0,
        "<=": c <= 0,
        ">": c > 0,
        ">=": c >= 0,
    }[op]


def satisfies(version: str, range: str) -> bool:
    key, has_pre = _version(version)
    sets = [_comparators(part) for part in range.split("||")]
    for comps in sets:
        if not all(_test(op, key, ck) for op, ck, _ in comps):
            continue
        if has_pre and not any(cpre and ck[0] == key[0] for _, ck, cpre in comps):
            continue
        return True
    return False
