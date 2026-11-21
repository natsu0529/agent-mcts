"""Add an ISO 8601 duration to a datetime. See README.md for the spec."""

from datetime import datetime


def add_duration(start: datetime, duration: str) -> datetime:
    """Return `start` moved by the ISO 8601 `duration`."""
    raise NotImplementedError
