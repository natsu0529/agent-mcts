"""Next fire time of a cron expression. See README.md for the spec."""

from datetime import datetime


def next_run(expr: str, after: datetime) -> datetime:
    """Return the first time strictly after `after` at which `expr` fires."""
    raise NotImplementedError
