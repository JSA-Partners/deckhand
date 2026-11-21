from __future__ import annotations

import pytest

from deckhand import brief


@pytest.mark.parametrize(
    ("seconds", "shown"),
    [
        (-5, "<1m"),
        (0, "<1m"),
        (59, "<1m"),
        (60, "1m"),
        (14 * 60 + 59, "14m"),
        (3600, "1h"),
        (2 * 3600 + 5 * 60, "2h 5m"),
        (86399, "23h 59m"),
        (86400, "1d"),
        (3 * 86400 + 7200, "3d"),
    ],
)
def test_age(seconds: float, shown: str):
    assert brief.age(seconds) == shown


def test_elapsed_from_a_record_time():
    assert brief.elapsed("1970-01-01T00:01:00.000Z", 90.5) == 30.5
