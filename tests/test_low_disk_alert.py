from __future__ import annotations

from unittest.mock import patch

from app.config_loader import NotificationsConfig
from app.core import low_disk_alert
from app.core.low_disk_alert import check_low_disk


class _FakeConfig:
    def __init__(self, **kwargs):
        self.notifications = NotificationsConfig(**kwargs)


def setup_function(_):
    low_disk_alert._last_alerted.clear()


def test_check_low_disk_noop_when_disabled():
    config = _FakeConfig(low_disk_alert_enabled=False)
    with patch("app.core.low_disk_alert.post_discord") as mock_discord:
        fired = check_low_disk(config, "Movies", free_bytes=0)
    assert fired is False
    mock_discord.assert_not_called()


def test_check_low_disk_noop_when_above_threshold():
    config = _FakeConfig(
        low_disk_alert_enabled=True, low_disk_threshold_gb=10, discord_webhook_url="https://discord/x"
    )
    with patch("app.core.low_disk_alert.post_discord") as mock_discord:
        fired = check_low_disk(config, "Movies", free_bytes=20 * 1024**3)
    assert fired is False
    mock_discord.assert_not_called()


def test_check_low_disk_fires_when_below_threshold():
    config = _FakeConfig(
        low_disk_alert_enabled=True, low_disk_threshold_gb=10, discord_webhook_url="https://discord/x"
    )
    with patch("app.core.low_disk_alert.post_discord") as mock_discord:
        fired = check_low_disk(config, "Movies", free_bytes=5 * 1024**3)
    assert fired is True
    mock_discord.assert_called_once()
    assert "Movies" in mock_discord.call_args.args[1]


def test_check_low_disk_fires_through_all_configured_channels():
    config = _FakeConfig(
        low_disk_alert_enabled=True, low_disk_threshold_gb=10,
        discord_webhook_url="https://discord/x",
        telegram_bot_token="tok", telegram_chat_id="chat",
        pushover_api_token="papi", pushover_user_key="puser",
    )
    with patch("app.core.low_disk_alert.post_discord") as mock_discord, \
         patch("app.core.low_disk_alert.post_telegram") as mock_telegram, \
         patch("app.core.low_disk_alert.post_pushover") as mock_pushover:
        check_low_disk(config, "Movies", free_bytes=1 * 1024**3)
    mock_discord.assert_called_once()
    mock_telegram.assert_called_once()
    mock_pushover.assert_called_once()


def test_check_low_disk_respects_daily_cooldown():
    config = _FakeConfig(
        low_disk_alert_enabled=True, low_disk_threshold_gb=10, discord_webhook_url="https://discord/x"
    )
    with patch("app.core.low_disk_alert.post_discord") as mock_discord:
        first = check_low_disk(config, "Movies", free_bytes=1 * 1024**3)
        second = check_low_disk(config, "Movies", free_bytes=1 * 1024**3)
    assert first is True
    assert second is False
    mock_discord.assert_called_once()


def test_check_low_disk_alerts_again_after_recovery():
    config = _FakeConfig(
        low_disk_alert_enabled=True, low_disk_threshold_gb=10, discord_webhook_url="https://discord/x"
    )
    with patch("app.core.low_disk_alert.post_discord") as mock_discord:
        check_low_disk(config, "Movies", free_bytes=1 * 1024**3)  # fires
        check_low_disk(config, "Movies", free_bytes=20 * 1024**3)  # recovers, clears cooldown
        fired_again = check_low_disk(config, "Movies", free_bytes=1 * 1024**3)  # drops again
    assert fired_again is True
    assert mock_discord.call_count == 2


def test_check_low_disk_labels_tracked_independently():
    config = _FakeConfig(
        low_disk_alert_enabled=True, low_disk_threshold_gb=10, discord_webhook_url="https://discord/x"
    )
    with patch("app.core.low_disk_alert.post_discord") as mock_discord:
        check_low_disk(config, "Movies", free_bytes=1 * 1024**3)
        fired_for_tv = check_low_disk(config, "TV", free_bytes=1 * 1024**3)
    assert fired_for_tv is True
    assert mock_discord.call_count == 2
