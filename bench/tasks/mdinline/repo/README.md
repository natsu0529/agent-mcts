# mdinline

Implement `render(text: str) -> str` in `mdinline.py`.

It renders one line of Markdown inline text as HTML, supporting three constructs from
[CommonMark 0.31.2](https://spec.commonmark.org/0.31.2/): backslash escapes, code spans,
and emphasis (`<em>`) and strong emphasis (`<strong>`). Everything else, including links,
images, autolinks, raw HTML, entity references and strikethrough, is plain text. The
output must be exactly what CommonMark gives for the inline content of a paragraph with
only these three constructs enabled. Use only the standard library.

```python
render("*Hello* **world**") == "<em>Hello</em> <strong>world</strong>"
render("use `a*b` here") == "use <code>a*b</code> here"
render("snake_case_name") == "snake_case_name"
render("**bold *and* italic**") == "<strong>bold <em>and</em> italic</strong>"
```

## Input and output

- `text` is a single line: raise `ValueError` if it contains `\n` or `\r`. Leading and
  trailing whitespace is kept as it is.
- In all text, including the content of code spans, `&`, `<`, `>` and `"` are written as
  `&amp;`, `&lt;`, `&gt;` and `&quot;`. No other character is changed.

## Characters

- **Whitespace:** tab (U+0009), form feed (U+000C), or any character in the Unicode
  category `Zs` (space, U+00A0 no-break space, U+3000 ideographic space, ...).
- **Punctuation:** an ASCII punctuation character (``!"#$%&'()*+,-./:;<=>?@[\]^_`{|}~``)
  or any character in a Unicode `P` (punctuation) or `S` (symbol) category.
- The start and the end of the line count as whitespace.

## Backslash escapes

A backslash followed by an ASCII punctuation character stands for that character as plain
text: it never starts or ends a code span or emphasis. `\*not emphasis\*` renders as
`*not emphasis*`. A backslash before any other character, or at the end of the line, is
a literal backslash. Inside code spans backslashes are always literal.

## Code spans

- A **backtick string** is a maximal run of one or more backticks.
- A code span starts with a backtick string and ends with the next backtick string of
  exactly the same length. ``` ``a ` b`` ``` is a code span containing ``a ` b``.
- If no later backtick string has the same length, the opening backtick string is plain
  text, and scanning continues right after it.
- The content is the text between the two backtick strings, taken literally. If it both
  begins and ends with a space and is not made only of spaces, one space is removed from
  each end. It is rendered as `<code>content</code>`.
- Code spans bind tighter than emphasis: `*a `*` b*` is
  `<em>a <code>*</code> b</em>`. But an opening backtick string that was escaped
  (``\` ``) is not a backtick string.

## Emphasis

A **delimiter run** is a maximal run of `*` characters, or a maximal run of `_`
characters, that is not escaped and not inside a code span. Let *before* be the character
just before the run and *after* the character just after it, in the original text.

- A run is **left-flanking** if *after* is not whitespace, and either *after* is not
  punctuation or *before* is whitespace or punctuation.
- A run is **right-flanking** if *before* is not whitespace, and either *before* is not
  punctuation or *after* is whitespace or punctuation.
- A `*` run can **open** emphasis if it is left-flanking, and can **close** emphasis if it
  is right-flanking.
- A `_` run can **open** emphasis if it is left-flanking and either not right-flanking
  or *before* is punctuation. It can **close** emphasis if it is right-flanking and
  either not left-flanking or *after* is punctuation. So `_` does not emphasize inside a
  word: `snake_case_name` stays as it is.

An opener and a closer can match only if they use the same character. When either one can
both open and close, they cannot match if the sum of the lengths of their two runs is a
multiple of 3, unless both lengths are multiples of 3. (So `*foo**bar*` is
`<em>foo**bar</em>`.)

Matching goes from left to right over the closers. For each run that can close, look back
for the nearest earlier run that can open and can match it. If there is one, take 2
characters from each run when both have at least 2 left (strong emphasis), otherwise 1
(emphasis), and wrap the text between them. Delimiter runs between the two can no longer
match anything and stay as plain text. A run with characters left over keeps matching:
`***a***` is `<em><strong>a</strong></em>`, and `**a*` is `*<em>a</em>`. Characters of a
run that are never matched are plain text.

These rules are CommonMark's rules 1 to 17 for emphasis, and the matching procedure is
the one in the appendix of the spec, "Processing emphasis and strong emphasis". When in
doubt, the spec decides.

## Running the tests

    python -m pytest -q
