import pytest
from freebusy import free_slots

WEEK = ("mon", "tue", "wed", "thu", "fri")


def test_one_attendee():
    alice = {"tz": "UTC", "hours": {d: ["09:00-17:00"] for d in WEEK}}
    assert free_slots([alice], "2025-06-06T00:00Z", "2025-06-09T00:00Z", 30) == [
        ("2025-06-06T09:00:00Z", "2025-06-06T17:00:00Z")
    ]


def test_two_time_zones():
    ny = {"tz": "America/New_York", "hours": {d: ["09:00-17:00"] for d in WEEK}}
    berlin = {"tz": "Europe/Berlin", "hours": {d: ["09:00-17:00"] for d in WEEK}}
    assert free_slots([ny, berlin], "2025-06-02T00:00Z", "2025-06-03T00:00Z", 30) == [
        ("2025-06-02T13:00:00Z", "2025-06-02T15:00:00Z")
    ]


def test_busy_and_min_minutes():
    tokyo = {
        "tz": "Asia/Tokyo",
        "hours": {"mon": ["09:00-12:00"]},
        "busy": [["2025-06-02T09:30:00+09:00", "2025-06-02T10:45:00+09:00"]],
    }
    assert free_slots([tokyo], "2025-06-01T00:00Z", "2025-06-03T00:00Z", 60) == [
        ("2025-06-02T01:45:00Z", "2025-06-02T03:00:00Z")
    ]


def test_window_must_not_be_empty():
    alice = {"tz": "UTC", "hours": {"mon": ["09:00-17:00"]}}
    with pytest.raises(ValueError):
        free_slots([alice], "2025-06-02T10:00Z", "2025-06-02T10:00Z", 30)
