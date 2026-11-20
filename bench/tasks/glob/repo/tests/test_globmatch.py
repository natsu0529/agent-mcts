from globmatch import match


def test_literal():
    assert match("src/main.py", "src/main.py")
    assert not match("src/main.py", "src/main.pyc")


def test_star_stays_in_segment():
    assert match("src/*.py", "src/main.py")
    assert not match("src/*.py", "src/pkg/main.py")


def test_question_mark():
    assert match("file?.txt", "file1.txt")
    assert not match("file?.txt", "file.txt")


def test_double_star():
    assert match("src/**/main.py", "src/a/b/main.py")
