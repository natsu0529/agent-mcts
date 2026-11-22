# ruff: noqa: RUF001  (full-width letters and digits are the point)
"""Build hidden/test_hidden_tablefmt.py from the cases below; expected values come from
the fixed library in reference/.

    python gen_hidden.py

Outputs are compared after NFC normalization, so a fix that composes decomposed
characters (as macOS file names often are) is scored the same as one that keeps them.
"""

import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "reference"))

from tablefmt import render  # noqa: E402

NFD_DOC = unicodedata.normalize("NFD", "ドキュメント.txt")
NFD_PHOTO = unicodedata.normalize("NFD", "がぞう.png")
NFD_CAFE = unicodedata.normalize("NFD", "café")
NFD_DO = unicodedata.normalize("NFD", "ドドド")
NFD_GA = unicodedata.normalize("NFD", "がががが")

ISSUE = [
    ["レポート_2024.pdf", 12, "2024-11-02"],
    ["notes.txt", 3, "2024-11-20"],
    ["議事録.docx", 48, "2024-10-15"],
]

# (id, rows, headers, kwargs). Ids starting with "ascii" pass on the unfixed library.
CASES = [
    # -- behaviour that must not change ------------------------------------------------
    ("ascii_readme", [["notes.txt", 3], ["photo.png", 1250]], ["name", "KB"], {}),
    ("ascii_no_headers", [["a", "bb"], ["ccc", "d"]], None, {}),
    ("ascii_numbers_right", [[1, 2.5], [300, 4]], ["count", "ratio_long"], {}),
    ("ascii_mixed_left", [["a"], [1]], ["x"], {}),
    ("ascii_bool_left", [[True], [False]], ["flag"], {}),
    ("ascii_none_and_ints", [[None], [7], [None], [42]], ["n"], {}),
    ("ascii_center_odd", [["ab"], ["abcde"]], None, {"align": "center"}),
    (
        "ascii_align_list",
        [["a", "b", "c"], ["dddd", "eeee", "ffff"]],
        None,
        {"align": ["right", "center", "left"]},
    ),
    ("ascii_ragged", [["a"], ["b", "c", "d"]], ["h1", "h2"], {}),
    ("ascii_truncate", [["abcdefgh", "x"]], None, {"max_width": 5}),
    ("ascii_truncate_fits", [["abcde", "x"]], None, {"max_width": 5}),
    ("ascii_wrap", [["one two three", "x"]], None, {"max_width": 7, "wrap": True}),
    ("ascii_wrap_long_word", [["abcdefghijkl"]], ["w"], {"max_width": 5, "wrap": True}),
    (
        "ascii_wrap_heights",
        [["aa bb cc", "dd ee"]],
        ["left", "right"],
        {"max_width": 5, "wrap": True},
    ),
    ("ascii_headers_only", [], ["name", "size"], {}),
    ("ascii_floats", [[0.5], [12.25]], ["v"], {}),
    ("ascii_wide_header_center", [["a"], ["bc"]], ["header"], {"align": "center"}),
    ("ascii_latin_accents", [["café", 1], ["naïve", 22]], ["word", "n"], {}),
    ("ascii_cyrillic", [["Привет", "x"], ["мир", "y"]], ["слово", "z"], {}),
    ("ascii_truncate_header", [["a"]], ["long header"], {"max_width": 6}),
    ("ascii_wrap_header", [["a", "b"]], ["long header", "h"], {"max_width": 6, "wrap": True}),
    ("ascii_max_width_too_small", [["a"]], None, {"max_width": 1}),
    ("ascii_align_wrong_length", [["a", "b"]], None, {"align": ["left"]}),
    # -- the reported problem: wide characters ------------------------------------------
    ("wide_issue_example", ISSUE, ["name", "KB", "modified"], {}),
    ("wide_left", [["日本語"], ["abc"]], ["name"], {}),
    ("wide_right", [["日本語"], ["abc"]], ["name"], {"align": "right"}),
    ("wide_center_even", [["日本"], ["abcdef"]], None, {"align": "center"}),
    ("wide_center_odd", [["日本"], ["abcde"]], None, {"align": "center"}),
    ("wide_header", [["a", 1], ["bb", 22]], ["名前", "数"], {}),
    ("wide_header_numeric_column", [[1], [22]], ["件数合計"], {}),
    ("wide_fullwidth_letters", [["ＡＢＣ", "x"], ["ABC", "y"]], None, {}),
    ("wide_fullwidth_digits", [["１２３", "x"], ["123", "y"]], None, {}),
    ("wide_fullwidth_space", [["全角　空白", "x"], ["a", "y"]], None, {}),
    ("wide_hangul", [["한국어", "x"], ["ko", "y"]], ["lang", "z"], {}),
    ("wide_chinese", [["中文文件.txt", 5], ["file.txt", 10]], ["name", "KB"], {}),
    ("wide_two_columns", [["東京", "大阪府"], ["Nagoya", "福岡"]], ["都市", "city"], {}),
    ("wide_middle_column", [["a", "日本語", "b"], ["c", "d", "e"]], ["x", "y", "z"], {}),
    ("wide_brackets", [["「重要」メモ.txt", "x"], ["memo.txt", "y"]], None, {}),
    ("wide_right_three_rows", [["日"], ["日本"], ["日本語"]], None, {"align": "right"}),
    ("wide_center_header", [["abcdef"]], ["表"], {"align": "center"}),
    ("wide_none_cells", [["日本", None], [None, "語"]], ["a", "b"], {}),
    ("wide_ragged", [["日本語"], ["a", "本"]], ["x", "y"], {}),
    ("wide_mixed_numbers", [["一", 1], ["二十", 20]], ["漢数字", "n"], {}),
    # -- narrow characters that are not ASCII --------------------------------------------
    ("narrow_halfwidth_kana", [["ﾃｽﾄ", "x"], ["テスト", "y"]], None, {}),
    ("narrow_halfwidth_only", [["ﾃﾞｰﾀ", "x"], ["data", "y"]], None, {}),
    ("narrow_half_and_full", [["ｶﾅカナ", "x"], ["abcdef", "y"]], None, {}),
    # -- decomposed (NFD) text, as macOS gives file names ----------------------------------
    ("nfd_document", [[NFD_DOC, 4], ["notes.txt", 3]], ["name", "KB"], {}),
    ("nfd_photo", [[NFD_PHOTO, "x"], ["がぞう.png", "y"]], None, {}),
    ("nfd_latin", [[NFD_CAFE, "x"], ["cafe", "y"]], None, {}),
    ("nfd_truncate", [[NFD_DOC, "x"]], None, {"max_width": 5}),
    ("nfd_truncate_keeps_marks", [[NFD_GA, "x"]], None, {"max_width": 5}),
    ("nfd_wrap_split", [[NFD_DO, "x"]], None, {"max_width": 4, "wrap": True}),
    # -- max_width with wide characters ---------------------------------------------------
    ("trunc_wide_even", [["日本語のファイル名.txt", "x"]], None, {"max_width": 8}),
    ("trunc_wide_odd", [["日本語のファイル名.txt", "x"]], None, {"max_width": 7}),
    ("trunc_fewer_chars_than_width", [["日本語", "x"]], None, {"max_width": 4}),
    ("trunc_exact_fit", [["日本語", "x"]], None, {"max_width": 6}),
    ("trunc_fits_no_ellipsis", [["日本", "x"], ["a", "y"]], None, {"max_width": 4}),
    ("trunc_mixed", [["report_報告書_v2.pdf", "x"]], None, {"max_width": 12}),
    ("trunc_halfwidth", [["ﾃｽﾄﾌｧｲﾙ", "x"]], None, {"max_width": 5}),
    ("trunc_header", [["a"]], ["とても長い見出し"], {"max_width": 7}),
    ("trunc_right", [["日本語のテキスト"], ["abc"]], None, {"max_width": 6, "align": "right"}),
    (
        "trunc_issue_names",
        [["レポート_2024年度_最終版.pdf", "ok"]],
        ["name", "status"],
        {"max_width": 12},
    ),
    ("trunc_many_rows", [["東京都千代田区"], ["大阪市"], ["札幌"]], ["address"], {"max_width": 9}),
    # -- wrap with wide characters ---------------------------------------------------------
    ("wrap_no_spaces", [["日本語のテキストを折り返す", "x"]], None, {"max_width": 6, "wrap": True}),
    ("wrap_no_spaces_odd", [["日本語のテキスト", "x"]], None, {"max_width": 5, "wrap": True}),
    ("wrap_words", [["東京 大阪 名古屋 福岡", "x"]], None, {"max_width": 8, "wrap": True}),
    ("wrap_mixed_word", [["abc日本語def", "x"]], None, {"max_width": 4, "wrap": True}),
    ("wrap_header", [["a", "b"]], ["長い見出しです", "h"], {"max_width": 6, "wrap": True}),
    (
        "wrap_two_columns",
        [["今日は 晴れ", "明日は 雨 でしょう"]],
        ["今日", "明日"],
        {"max_width": 6, "wrap": True},
    ),
    (
        "wrap_center",
        [["日本語 テキスト"], ["abc"]],
        None,
        {"max_width": 8, "wrap": True, "align": "center"},
    ),
    ("wrap_fits", [["日本 語", "x"]], None, {"max_width": 5, "wrap": True}),
]


def expected(rows, headers, kwargs):
    try:
        return render(rows, headers, **kwargs)
    except ValueError:
        return ValueError


def literal(value) -> str:
    """repr(), with combining marks escaped so the file shows what the test holds."""
    text = repr(value)
    return "".join(f"\\u{ord(c):04x}" if unicodedata.combining(c) else c for c in text)


def main() -> None:
    ids = [c[0] for c in CASES]
    assert len(ids) == len(set(ids)), "duplicate case id"
    out = [
        "# ruff: noqa: RUF001, E501",
        '"""Hidden tests for tablefmt. Generated by gen_hidden.py; do not edit."""',
        "",
        "import unicodedata",
        "",
        "import pytest",
        "from tablefmt import render",
        "",
        "",
        "def nfc(text):",
        '    return unicodedata.normalize("NFC", text)',
        "",
        "",
        "CASES = [",
    ]
    for case_id, rows, headers, kwargs in CASES:
        want = expected(rows, headers, kwargs)
        want_src = "ValueError" if want is ValueError else literal(want)
        out.append(
            f"    pytest.param({literal(rows)}, {literal(headers)}, {literal(kwargs)}, "
            f"{want_src}, id={case_id!r}),"
        )
    out += [
        "]",
        "",
        "",
        '@pytest.mark.parametrize(("rows", "headers", "kwargs", "want"), CASES)',
        "def test_render(rows, headers, kwargs, want):",
        "    if want is ValueError:",
        "        with pytest.raises(ValueError):",
        "            render(rows, headers, **kwargs)",
        "    else:",
        "        assert nfc(render(rows, headers, **kwargs)) == nfc(want)",
        "",
    ]
    (HERE / "hidden" / "test_hidden_tablefmt.py").write_text("\n".join(out), encoding="utf-8")
    print(f"{len(CASES)} cases")


if __name__ == "__main__":
    main()
