# globmatch

Implement `match(pattern: str, path: str) -> bool` in `globmatch.py`.

It reports whether a `/`-separated path matches a glob pattern. The whole path must
match, and matching is case sensitive. Do not use `fnmatch`, `glob`, or `pathlib`'s
matching; implement the rules below.

Paths passed in never start or end with `/` and never contain an empty segment.

## Rules

`/` always separates segments, in the pattern and in the path. Nothing except `**`
(below) ever matches a `/`.

Within one segment:

- `*` matches any run of characters, including none.
- `?` matches exactly one character.
- `[abc]` matches one character from the set. `[a-z]` is a range. `[!abc]` matches one
  character that is **not** in the set.
  - A `]` directly after `[` or `[!` is a literal member of the set: `[]a]` matches `]`
    or `a`.
  - A `-` first or last in the set is a literal `-`.
  - A `[` with no closing `]` in the same segment is a literal `[`.
- `\` makes the next character literal (`\*` matches a `*`). This also works inside
  `[...]`. A `\` at the very end of the pattern matches a literal `\`.
- Any other character matches itself.

Across segments:

- A segment that is exactly `**` matches **zero or more** whole segments. So `a/**/b`
  matches `a/b`, `a/x/b` and `a/x/y/b`; `a/**` matches `a`, `a/b` and `a/b/c`; `**/a`
  matches `a` and `x/a`.
- `**` that is not a whole segment (`a**b`) behaves like `*`.

Hidden files:

- A path segment that starts with `.` is matched only by a pattern segment that starts
  with a literal `.` (written as `.` or `\.`). `*`, `?` and `[...]` never match that
  leading dot, so `*.txt` does not match `.txt`, and `*` does not match `.git`.
- `**` never matches a segment that starts with `.`.

Braces:

- `{a,b,c}` matches any one of the comma-separated alternatives. Alternatives may be
  empty (`a{,b}` matches `a` and `ab`), may contain wildcards and `/`, and may nest
  (`{a,b{c,d}}` matches `a`, `bc`, `bd`).
- Braces are expanded before everything else, so the result of an expansion is treated
  as an ordinary pattern: `{a,**}/b` behaves like `a/b` or `**/b`.
- A brace group with no top-level comma (`{a}`), or a `{` with no matching `}`, is not
  special: the braces are literal characters.
- `\{`, `\}` and `\,` are literal and do not open, close or split a group.

An empty pattern matches only the empty path.

## Running the tests

    python -m pytest -q
