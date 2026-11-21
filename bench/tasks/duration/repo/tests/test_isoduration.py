from datetime import datetime

import pytest
from isoduration import add_duration


def test_days():
    assert add_duration(datetime(2025, 3, 10), "P3D") == datetime(2025, 3, 13)


def test_time():
    assert add_duration(datetime(2025, 3, 10, 8), "PT1H30M") == datetime(2025, 3, 10, 9, 30)


def test_negative():
    assert add_duration(datetime(2025, 3, 10), "-P1D") == datetime(2025, 3, 9)


def test_invalid():
    with pytest.raises(ValueError):
        add_duration(datetime(2025, 3, 10), "P")
