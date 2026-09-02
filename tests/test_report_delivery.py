from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from app.config_loader import NotificationsConfig
from app.core.report_delivery import (
    build_digest_message,
    deliver_report,
    previous_complete_period,
)
from app.models import (
    ReportGrowthOut,
    ReportSummaryOut,
    ReportTrackerActivityOut,
    ReportWatchActivityOut,
    StatsInsightsResponse,
)


def _summary(**overrides) -> ReportSummaryOut:
    defaults = dict(
        start_date="2026-02-01",
        end_date="2026-02-28",
        growth=ReportGrowthOut(movies_added=2, tv_episodes_added=5, total_size_bytes_added=1024),
        watch_activity=ReportWatchActivityOut(movies_watched=1, tv_episodes_watched=3),
        tracker_activity=ReportTrackerActivityOut(notifications_sent=1),
        insights=StatsInsightsResponse(top_genres=[], resolution_breakdown=[], growth_by_month=[]),
    )
    defaults.update(overrides)
    return ReportSummaryOut(**defaults)


def test_previous_complete_period_monthly_gives_prior_calendar_month():
    start, end, label = previous_complete_period("monthly", date(2026, 3, 15))
    assert (start, end, label) == (date(2026, 2, 1), date(2026, 2, 28), "2026-02")


def test_previous_complete_period_monthly_handles_year_rollover():
    start, end, label = previous_complete_period("monthly", date(2026, 1, 10))
    assert (start, end, label) == (date(2025, 12, 1), date(2025, 12, 31), "2025-12")


def test_previous_complete_period_quarterly_gives_prior_quarter():
    start, end, label = previous_complete_period("quarterly", date(2026, 4, 1))
    assert (start, end, label) == (date(2026, 1, 1), date(2026, 3, 31), "2026-Q1")


def test_previous_complete_period_quarterly_handles_year_rollover():
    start, end, label = previous_complete_period("quarterly", date(2026, 1, 1))
    assert (start, end, label) == (date(2025, 10, 1), date(2025, 12, 31), "2025-Q4")


def test_previous_complete_period_weekly_gives_last_full_week():
    # 2026-03-09 is a Monday -- the last full Mon-Sun week before it is
    # 2026-03-02 (Mon) through 2026-03-08 (Sun).
    start, end, label = previous_complete_period("weekly", date(2026, 3, 9))
    assert (start, end) == (date(2026, 3, 2), date(2026, 3, 8))
    assert label.startswith("2026-W")


def test_previous_complete_period_unknown_frequency_falls_back_to_monthly():
    start, end, label = previous_complete_period("bogus", date(2026, 3, 15))
    assert (start, end, label) == (date(2026, 2, 1), date(2026, 2, 28), "2026-02")


def test_build_digest_message_includes_growth_and_watch_counts():
    message = build_digest_message(_summary())
    assert "2026-02-01 to 2026-02-28" in message
    assert "+2 movie(s)" in message
    assert "+5 TV episode(s)" in message
    assert "watched 1 movie(s), 3 episode(s)" in message


def test_deliver_report_fires_discord_when_configured():
    with patch("app.core.report_delivery.post_discord") as mock_discord:
        config = MagicMock(notifications=NotificationsConfig(discord_webhook_url="https://discord.example/hook"))
        sent = deliver_report(config, _summary())
    assert sent is True
    mock_discord.assert_called_once()


def test_deliver_report_returns_false_when_no_channel_configured():
    config = MagicMock(notifications=NotificationsConfig())
    assert deliver_report(config, _summary()) is False


def test_deliver_report_fires_generic_webhook_with_full_summary_payload():
    with patch("app.core.report_delivery.requests.post") as mock_post:
        config = MagicMock(notifications=NotificationsConfig(webhook_url="https://example.com/hook"))
        sent = deliver_report(config, _summary())
    assert sent is True
    mock_post.assert_called_once()
    assert mock_post.call_args.args[0] == "https://example.com/hook"
    assert mock_post.call_args.kwargs["json"]["start_date"] == "2026-02-01"
