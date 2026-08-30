from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import patch

from app.core.scheduler import _seconds_until, run_weekly_maintenance, start_maintenance, stop_maintenance


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


def test_run_weekly_maintenance_calls_db_checkpoint(db):
    with patch("app.core.scheduler.get_database", return_value=db), patch(
        "app.core.scheduler.asyncio.sleep", side_effect=[None, asyncio.CancelledError()]
    ), patch.object(db, "maintenance_checkpoint_and_vacuum") as mock_maintenance:
        import contextlib

        with contextlib.suppress(asyncio.CancelledError):
            asyncio.run(run_weekly_maintenance())
    mock_maintenance.assert_called_once()


def test_start_stop_maintenance_lifecycle():
    async def _run():
        task = start_maintenance()
        await asyncio.sleep(0)
        await stop_maintenance(task)
        assert task.cancelled() or task.done()

    asyncio.run(_run())
