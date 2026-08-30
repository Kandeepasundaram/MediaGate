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

from app.config_loader import AppConfig

logger = logging.getLogger(__name__)

_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def _backups_dir(config: AppConfig) -> Path:
    return config.database_path.parent / "backups"


def run_backup(config: AppConfig) -> Path:
    """Copies the current DB file and config.yaml into a fresh timestamped
    subdirectory of backups/. Best-effort per file -- a missing DB (fresh
    install) or config file shouldn't abort the whole backup."""
    dest_dir = _backups_dir(config) / datetime.now(timezone.utc).strftime(_TIMESTAMP_FORMAT)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for source in (config.database_path, config.config_path):
        if source.exists():
            shutil.copy2(source, dest_dir / source.name)
    logger.info("Backup written to %s", dest_dir)
    return dest_dir


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
