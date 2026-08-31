"""Periodic backup of config.yaml + the SQLite database to a local
`backups/` folder next to the database -- the /config volume already
persists across container recreates, but a bad settings save or a
corrupted DB write has no recovery path without a copy taken before it
happened.
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from app.config_loader import AppConfig

logger = logging.getLogger(__name__)

_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_WEBDAV_TIMEOUT_SECONDS = 30


def _backups_dir(config: AppConfig) -> Path:
    return config.database_path.parent / "backups"


def run_backup(config: AppConfig) -> Path:
    """Copies the current DB file and config.yaml into a fresh timestamped
    subdirectory of backups/. Best-effort per file -- a missing DB (fresh
    install) or config file shouldn't abort the whole backup. Also pushes
    the same files over WebDAV when configured (see upload_to_webdav) --
    local backups alone don't survive the host itself dying, which is the
    whole point of a *remote* copy."""
    dest_dir = _backups_dir(config) / datetime.now(timezone.utc).strftime(_TIMESTAMP_FORMAT)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for source in (config.database_path, config.config_path):
        if source.exists():
            shutil.copy2(source, dest_dir / source.name)
    logger.info("Backup written to %s", dest_dir)

    if config.backup.webdav_url:
        upload_to_webdav(config, dest_dir)

    return dest_dir


def _webdav_auth(config: AppConfig) -> tuple[str, str] | None:
    if config.backup.webdav_username or config.backup.webdav_password:
        return (config.backup.webdav_username, config.backup.webdav_password)
    return None


def _webdav_mkcol(url: str, auth: tuple[str, str] | None) -> None:
    """Best-effort MKCOL -- WebDAV requires each parent collection to exist
    before you can PUT into it. A 405 (already exists) or 409 (parent
    already being created concurrently) is expected and not an error;
    anything else is logged but still doesn't abort the backup."""
    try:
        resp = requests.request("MKCOL", url, auth=auth, timeout=_WEBDAV_TIMEOUT_SECONDS)
        if resp.status_code not in (201, 405, 409):
            logger.warning("WebDAV MKCOL %s returned %s", url, resp.status_code)
    except requests.RequestException as exc:
        logger.warning("WebDAV MKCOL %s failed: %s", url, exc)


def upload_to_webdav(config: AppConfig, dest_dir: Path) -> bool:
    """Uploads every file in a completed local backup directory to
    {webdav_url}/{webdav_remote_path}/{dest_dir.name}/ -- mirrors the local
    backups/<timestamp>/ layout on the remote side. Best-effort: a failed
    upload is logged, not raised, so a flaky remote never breaks the local
    backup that already succeeded. Returns True only if every file made it.
    """
    base = config.backup.webdav_url.rstrip("/")
    remote_root = config.backup.webdav_remote_path.strip("/")
    auth = _webdav_auth(config)

    _webdav_mkcol(f"{base}/{remote_root}", auth)
    remote_dir_url = f"{base}/{remote_root}/{dest_dir.name}"
    _webdav_mkcol(remote_dir_url, auth)

    all_ok = True
    for file in dest_dir.iterdir():
        if not file.is_file():
            continue
        try:
            with file.open("rb") as fh:
                resp = requests.put(f"{remote_dir_url}/{file.name}", data=fh, auth=auth, timeout=_WEBDAV_TIMEOUT_SECONDS)
            if resp.status_code not in (200, 201, 204):
                logger.warning("WebDAV PUT %s returned %s", file.name, resp.status_code)
                all_ok = False
        except requests.RequestException as exc:
            logger.warning("WebDAV PUT %s failed: %s", file.name, exc)
            all_ok = False

    if all_ok:
        logger.info("Backup %s pushed to WebDAV at %s", dest_dir.name, remote_dir_url)
    return all_ok


def prune_old_backups(config: AppConfig, retention_days: int) -> int:
    """Removes backup subdirectories older than `retention_days`, keyed off
    the timestamp in their own folder name rather than filesystem mtime
    (which a volume migration or restore can reset). Returns how many were
    removed."""
    backups_dir = _backups_dir(config)
    if not backups_dir.exists():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed = 0
    for entry in backups_dir.iterdir():
        if not entry.is_dir():
            continue
        try:
            stamp = datetime.strptime(entry.name, _TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
        except ValueError:
            continue  # not one of our backup folders -- leave it alone
        if stamp < cutoff:
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    return removed
