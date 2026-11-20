from datetime import datetime

import pytest
from cronnext import next_run


def test_every_minute():
    assert next_run("* * * * *", datetime(2025, 3, 10, 12, 0, 30)) == datetime(2025, 3, 10, 12, 1)


def test_daily_at_time():
    assert next_run("30 9 * * *", datetime(2025, 3, 10, 10, 0)) == datetime(2025, 3, 11, 9, 30)


def test_step():
    assert next_run("*/15 * * * *", datetime(2025, 3, 10, 12, 16)) == datetime(2025, 3, 10, 12, 30)


def test_wrong_field_count():
    with pytest.raises(ValueError):
        next_run("* * * *", datetime(2025, 3, 10))
