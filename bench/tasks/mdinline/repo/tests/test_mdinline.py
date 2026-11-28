import pytest
from mdinline import render


def test_emphasis():
    assert render("*Hello* **world**") == "<em>Hello</em> <strong>world</strong>"


def test_code_span():
    assert render("use `a*b` here") == "use <code>a*b</code> here"


def test_html_is_escaped():
    assert render("a < b & c") == "a &lt; b &amp; c"


def test_newline_is_an_error():
    with pytest.raises(ValueError):
        render("one\ntwo")
