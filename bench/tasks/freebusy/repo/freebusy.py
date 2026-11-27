"""Free meeting slots across time zones. See README.md for the spec."""


def free_slots(
    attendees: list[dict], start: str, end: str, min_minutes: int
) -> list[tuple[str, str]]:
    """Return the periods in [start, end) in which everyone works and nobody is busy."""
    raise NotImplementedError
