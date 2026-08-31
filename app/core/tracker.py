"""Detects new TV seasons and movie sequels/releases for tracked titles."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import requests

from app.core.tmdb_client import TMDBClient
from app.database import Database

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def maybe_auto_track(
    db: Database,
    auto_track_enabled: bool,
    tmdb_id: int | None,
    media_type: str,
    title: str,
    season: int | None = None,
) -> None:
    """Adds a freshly-archived title to the tracker automatically, when
    tracker.auto_track_new is on -- opt-in, since not every archived title
    is something the user wants season/sequel alerts for. For TV,
    current_season_archived only ever moves forward: archiving an old
    episode out of order shouldn't make the tracker think the show
    regressed and re-flag a season the user already has.
    """
    if not auto_track_enabled or tmdb_id is None:
        return
    fields: dict = {}
    if media_type == "tv" and season is not None:
        existing = db.get_tracker(tmdb_id, media_type)
        current_max = existing["current_season_archived"] if existing else None
        if current_max is None or season > current_max:
            fields["current_season_archived"] = season
    db.upsert_tracker(tmdb_id=tmdb_id, media_type=media_type, title=title, **fields)


def _is_due(row: dict, now: datetime) -> bool:
    """Whether a scheduled check_for_updates run should touch this title --
    "Check Now" in the UI bypasses this entirely and always checks. A
    snooze always wins over a custom interval (snoozing is a deliberate,
    time-boxed "not now" from the user)."""
    if row["snoozed_until"]:
        if now < datetime.fromisoformat(row["snoozed_until"]):
            return False
    if row["check_interval_hours"] and row["last_checked"]:
        next_due = datetime.fromisoformat(row["last_checked"]) + timedelta(hours=row["check_interval_hours"])
        if now < next_due:
            return False
    return True


def check_tv_show(db: Database, tmdb: TMDBClient, tracker_row: dict) -> bool:
    """Returns True if this check just flipped pending_notification on (as
    opposed to it already being pending, or staying not-pending) -- used by
    check_for_updates to fire a webhook only on the transition, not on every
    check of an already-pending title."""
    details = tmdb.get_tv_details(tracker_row["tmdb_id"])
    latest_known_season = None
    if details:
        latest_known_season = details.raw.get("number_of_seasons")

    current = tracker_row["current_season_archived"] or 0
    pending = bool(latest_known_season and latest_known_season > current)
    newly_pending = pending and not tracker_row["pending_notification"]

    db.upsert_tracker(
        tmdb_id=tracker_row["tmdb_id"],
        media_type="tv",
        title=tracker_row["title"],
        latest_known_season=latest_known_season,
        last_checked=_now(),
        pending_notification=1 if pending else tracker_row["pending_notification"],
    )
    if pending:
        logger.info("New season detected for %s: season %s", tracker_row["title"], latest_known_season)
    return newly_pending


def check_movie_collection(db: Database, tmdb: TMDBClient, tracker_row: dict) -> bool:
    details = tmdb.get_movie_details(tracker_row["tmdb_id"])
    collection_id = details.raw.get("belongs_to_collection", {}).get("id") if details and details.raw else None

    status = tracker_row["movie_release_status"]
    pending = False
    if collection_id:
        collection = tmdb.get_collection_movies(collection_id)
        known_ids = {tracker_row["tmdb_id"]}
        new_entries = [m for m in collection if m.tmdb_id not in known_ids]
        if new_entries:
            status = f"{len(new_entries)} related title(s) found in collection"
            pending = True

    newly_pending = pending and not tracker_row["pending_notification"]

    db.upsert_tracker(
        tmdb_id=tracker_row["tmdb_id"],
        media_type="movie",
        title=tracker_row["title"],
        movie_release_status=status,
        last_checked=_now(),
        pending_notification=1 if pending else tracker_row["pending_notification"],
    )
    if pending:
        logger.info("Sequel/related release detected for %s", tracker_row["title"])
    return newly_pending


def _message_for(row: dict) -> str:
    return (
        f"New season available for {row['title']}"
        if row["media_type"] == "tv"
        else f"{row['title']}: {row.get('movie_release_status') or 'new release detected'}"
    )


def _digest_message(rows: list[dict]) -> str:
    return "; ".join(_message_for(row) for row in rows)


def _fire_webhook(webhook_url: str, rows: list[dict]) -> None:
    """One POST per check_for_updates run, not one per newly-pending title --
    a run that flips five titles at once (e.g. after being offline a while)
    should send one digest, not fire the webhook five times back to back."""
    if len(rows) == 1:
        row = rows[0]
        payload = {"title": row["title"], "media_type": row["media_type"], "message": _message_for(row)}
    else:
        payload = {"count": len(rows), "titles": [row["title"] for row in rows], "message": _digest_message(rows)}
    try:
        requests.post(webhook_url, json=payload, timeout=10)
    except requests.RequestException as exc:
        logger.warning("Tracker webhook POST to %s failed: %s", webhook_url, exc)


def _send_discord(discord_webhook_url: str, rows: list[dict]) -> None:
    try:
        requests.post(discord_webhook_url, json={"content": _digest_message(rows)}, timeout=10)
    except requests.RequestException as exc:
        logger.warning("Discord webhook POST failed: %s", exc)


def _send_telegram(bot_token: str, chat_id: str, rows: list[dict]) -> None:
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": _digest_message(rows)},
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.warning("Telegram sendMessage failed: %s", exc)


def _send_pushover(api_token: str, user_key: str, rows: list[dict]) -> None:
    try:
        requests.post(
            "https://api.pushover.net/1/messages.json",
            data={
                "token": api_token,
                "user": user_key,
                "title": "Media Manager",
                "message": _digest_message(rows),
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.warning("Pushover message POST failed: %s", exc)


def check_for_updates(
    db: Database,
    tmdb: TMDBClient,
    webhook_url: str | None = None,
    discord_webhook_url: str | None = None,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
    pushover_api_token: str | None = None,
    pushover_user_key: str | None = None,
) -> int:
    """Main tracker entry point. Returns the number of items now pending notification."""
    tracked = db.list_tracked()
    now = datetime.now(timezone.utc)
    newly_pending_rows: list[dict] = []
    for row in tracked:
        if not _is_due(row, now):
            continue
        try:
            if row["media_type"] == "tv":
                newly_pending = check_tv_show(db, tmdb, row)
            else:
                newly_pending = check_movie_collection(db, tmdb, row)
            db.log_operation(operation_type="tracker_check", status="success", details={"tmdb_id": row["tmdb_id"]})
            if newly_pending and not row["muted"]:
                newly_pending_rows.append(row)
                db.log_notification(row["id"], row["tmdb_id"], row["media_type"], row["title"], _message_for(row))
        except Exception as exc:  # noqa: BLE001 - one bad title shouldn't abort the whole run
            logger.error("Tracker check failed for tmdb_id=%s: %s", row["tmdb_id"], exc)
            db.log_operation(
                operation_type="tracker_check",
                status="failed",
                details={"tmdb_id": row["tmdb_id"]},
                error_message=str(exc),
            )

    if newly_pending_rows:
        if webhook_url:
            _fire_webhook(webhook_url, newly_pending_rows)
        if discord_webhook_url:
            _send_discord(discord_webhook_url, newly_pending_rows)
        if telegram_bot_token and telegram_chat_id:
            _send_telegram(telegram_bot_token, telegram_chat_id, newly_pending_rows)
        if pushover_api_token and pushover_user_key:
            _send_pushover(pushover_api_token, pushover_user_key, newly_pending_rows)

    return len(db.list_pending_notifications())
