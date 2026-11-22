import pytest
from tablefmt import render


def test_readme_example():
    assert render([["notes.txt", 3], ["photo.png", 1250]], ["name", "KB"]) == (
        "name      |   KB\n----------+-----\nnotes.txt |    3\nphoto.png | 1250"
    )


def test_no_headers():
    assert render([["a", "bb"], ["ccc", "d"]]) == "a   | bb\nccc | d"


def test_center():
    assert render([["ab"], ["abcde"]], align="center") == " ab\nabcde"


def test_truncate():
    assert render([["abcdefgh", "x"]], max_width=5) == "abcd… | x"


def test_wrap():
    assert render([["one two three", "x"]], max_width=7, wrap=True) == ("one two | x\nthree   |")


def test_empty():
    assert render([]) == ""


def test_bad_align():
    with pytest.raises(ValueError):
        render([["a"]], align="middle")
