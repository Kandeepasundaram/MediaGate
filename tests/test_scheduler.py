from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from app.core.scheduler import _seconds_until


def test_seconds_until_later_today():
    fake_now = datetime(2026, 1, 1, 5, 0, 0)
    with patch("app.core.scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        seconds = _seconds_until("06:00")
    assert seconds == 3600


def test_seconds_until_wraps_to_tomorrow_if_already_passed():
    fake_now = datetime(2026, 1, 1, 7, 0, 0)
    with patch("app.core.scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        seconds = _seconds_until("06:00")
    assert seconds == 23 * 3600


def test_seconds_until_invalid_format_defaults_to_six_am():
    fake_now = datetime(2026, 1, 1, 5, 0, 0)
    with patch("app.core.scheduler.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        seconds = _seconds_until("not-a-time")
    assert seconds == 3600
