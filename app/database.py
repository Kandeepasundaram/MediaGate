"""SQLite access layer: schema, migrations, and CRUD helpers.

Concurrency note: SQLite has no real connection pool. We open WAL mode so
readers don't block writers, and open a short-lived connection per call via
a context manager rather than holding one connection open across requests.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS media_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_path TEXT NOT NULL,
    title TEXT NOT NULL,
    year INTEGER,
    tmdb_id INTEGER,
    media_type TEXT NOT NULL CHECK (media_type IN ('movie', 'tv')),
    season_number INTEGER,
    episode_number INTEGER,
    final_path TEXT,
    archived_at TEXT,
    watched INTEGER NOT NULL DEFAULT 0,
    metadata TEXT,
    match_attempted_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS archive_tracker (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id INTEGER NOT NULL,
    media_type TEXT NOT NULL CHECK (media_type IN ('movie', 'tv')),
    title TEXT NOT NULL,
    current_season_archived INTEGER,
    latest_known_season INTEGER,
    movie_release_status TEXT,
    last_checked TEXT,
    pending_notification INTEGER NOT NULL DEFAULT 0,
    notification_sent_at TEXT,
    muted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (tmdb_id, media_type)
);

CREATE TABLE IF NOT EXISTS operation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_type TEXT NOT NULL CHECK (operation_type IN ('archive', 'rename', 'purge', 'tracker_check', 'delete')),
    media_id INTEGER REFERENCES media_items(id) ON DELETE SET NULL,
    details TEXT,
    status TEXT NOT NULL CHECK (status IN ('success', 'failed', 'pending')),
    error_message TEXT,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(_SCHEMA)
            row = conn.execute("SELECT version FROM schema_meta").fetchone()
            if row is None:
                conn.execute("INSERT INTO schema_meta (version) VALUES (?)", (SCHEMA_VERSION,))
        self.migrate()

    def migrate(self) -> None:
        """Apply forward-only migrations based on schema_meta.version."""
        with self.connect() as conn:
            current = conn.execute("SELECT version FROM schema_meta").fetchone()["version"]
            for target_version, migration in _MIGRATIONS.items():
                if target_version > current:
                    migration(conn)
                    conn.execute("UPDATE schema_meta SET version = ?", (target_version,))
                    current = target_version

    def execute_query(self, query: str, params: tuple = ()) -> int | None:
        """Run INSERT/UPDATE/DELETE, return lastrowid."""
        with self.connect() as conn:
            cur = conn.execute(query, params)
            return cur.lastrowid

    def fetch_all(self, query: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def fetch_one(self, query: str, params: tuple = ()) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(query, params).fetchone()
            return dict(row) if row else None

    # ---- media_items CRUD ----

    def create_media_item(self, **fields: Any) -> int:
        fields.setdefault("watched", 0)
        fields.setdefault("created_at", _now())
        if "metadata" in fields and not isinstance(fields["metadata"], str):
            fields["metadata"] = json.dumps(fields["metadata"])
        cols = ", ".join(fields)
        placeholders = ", ".join(["?"] * len(fields))
        return self.execute_query(
            f"INSERT INTO media_items ({cols}) VALUES ({placeholders})",
            tuple(fields.values()),
        )

    def get_media_item(self, item_id: int) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM media_items WHERE id = ?", (item_id,))

    def list_known_paths(self) -> set[str]:
        """Every path already tracked as a media item's source or archived
        copy, used to keep a rescan from re-surfacing already-handled files."""
        rows = self.fetch_all(
            "SELECT original_path, final_path FROM media_items "
            "WHERE original_path IS NOT NULL OR final_path IS NOT NULL"
        )
        paths: set[str] = set()
        for row in rows:
            if row["original_path"]:
                paths.add(str(Path(row["original_path"]).resolve()))
            if row["final_path"]:
                paths.add(str(Path(row["final_path"]).resolve()))
        return paths

    def list_media_items(self, media_type: str | None = None) -> list[dict[str, Any]]:
        if media_type:
            return self.fetch_all("SELECT * FROM media_items WHERE media_type = ? ORDER BY created_at DESC", (media_type,))
        return self.fetch_all("SELECT * FROM media_items ORDER BY created_at DESC")

    def update_media_item(self, item_id: int, **fields: Any) -> None:
        if not fields:
            return
        if "metadata" in fields and not isinstance(fields["metadata"], str):
            fields["metadata"] = json.dumps(fields["metadata"])
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        self.execute_query(
            f"UPDATE media_items SET {set_clause} WHERE id = ?",
            (*fields.values(), item_id),
        )

    def delete_media_item(self, item_id: int) -> None:
        self.execute_query("DELETE FROM media_items WHERE id = ?", (item_id,))

    def get_media_item_by_final_path(self, path: str) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM media_items WHERE final_path = ?", (path,))

    def list_unmatched_media_items(self, retry_cooldown_hours: float = 6.0, limit: int = 1) -> list[dict[str, Any]]:
        """Auto-adopted items with no TMDB match yet, for the metadata
        backfill background task. Never-attempted items come first; a
        previously-failed lookup is only retried after `retry_cooldown_hours`
        so a title with no real TMDB match doesn't get re-searched on every
        backfill cycle."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=retry_cooldown_hours)).isoformat()
        return self.fetch_all(
            "SELECT * FROM media_items "
            "WHERE tmdb_id IS NULL AND (match_attempted_at IS NULL OR match_attempted_at < ?) "
            "ORDER BY (match_attempted_at IS NULL) DESC, created_at ASC "
            "LIMIT ?",
            (cutoff, limit),
        )

    def count_unmatched_media_items(self, media_type: str | None = None) -> int:
        if media_type:
            row = self.fetch_one(
                "SELECT COUNT(*) AS n FROM media_items WHERE tmdb_id IS NULL AND media_type = ?", (media_type,)
            )
        else:
            row = self.fetch_one("SELECT COUNT(*) AS n FROM media_items WHERE tmdb_id IS NULL")
        return row["n"]

    def count_failed_match_items(self, media_type: str | None = None) -> int:
        """Auto-adopted items that have been searched at least once with no
        TMDB match found (as opposed to ones still waiting their first try)."""
        if media_type:
            row = self.fetch_one(
                "SELECT COUNT(*) AS n FROM media_items "
                "WHERE tmdb_id IS NULL AND match_attempted_at IS NOT NULL AND media_type = ?",
                (media_type,),
            )
        else:
            row = self.fetch_one(
                "SELECT COUNT(*) AS n FROM media_items WHERE tmdb_id IS NULL AND match_attempted_at IS NOT NULL"
            )
        return row["n"]

    # ---- archive_tracker CRUD ----

    def upsert_tracker(self, tmdb_id: int, media_type: str, title: str, **fields: Any) -> None:
        existing = self.fetch_one(
            "SELECT id FROM archive_tracker WHERE tmdb_id = ? AND media_type = ?",
            (tmdb_id, media_type),
        )
        if existing:
            if fields:
                set_clause = ", ".join(f"{k} = ?" for k in fields)
                self.execute_query(
                    f"UPDATE archive_tracker SET {set_clause} WHERE id = ?",
                    (*fields.values(), existing["id"]),
                )
        else:
            fields.setdefault("pending_notification", 0)
            fields.setdefault("created_at", _now())
            cols = ", ".join(["tmdb_id", "media_type", "title", *fields])
            placeholders = ", ".join(["?"] * (3 + len(fields)))
            self.execute_query(
                f"INSERT INTO archive_tracker ({cols}) VALUES ({placeholders})",
                (tmdb_id, media_type, title, *fields.values()),
            )

    def get_tracker(self, tmdb_id: int, media_type: str) -> dict[str, Any] | None:
        return self.fetch_one(
            "SELECT * FROM archive_tracker WHERE tmdb_id = ? AND media_type = ?",
            (tmdb_id, media_type),
        )

    def list_pending_notifications(self) -> list[dict[str, Any]]:
        """Muted titles are excluded -- they still get checked/tracked, just
        never surface in the Notifications tab or fire a browser notification."""
        return self.fetch_all("SELECT * FROM archive_tracker WHERE pending_notification = 1 AND muted = 0")

    def list_tracked(self) -> list[dict[str, Any]]:
        return self.fetch_all("SELECT * FROM archive_tracker ORDER BY created_at DESC")

    def acknowledge_notification(self, tracker_id: int) -> None:
        self.execute_query(
            "UPDATE archive_tracker SET pending_notification = 0, notification_sent_at = ? WHERE id = ?",
            (_now(), tracker_id),
        )

    def set_tracker_muted(self, tracker_id: int, muted: bool) -> None:
        self.execute_query("UPDATE archive_tracker SET muted = ? WHERE id = ?", (1 if muted else 0, tracker_id))

    def get_tracker_by_id(self, tracker_id: int) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM archive_tracker WHERE id = ?", (tracker_id,))

    # ---- operation_log CRUD ----

    def log_operation(
        self,
        operation_type: str,
        status: str,
        media_id: int | None = None,
        details: dict | None = None,
        error_message: str | None = None,
    ) -> int:
        return self.execute_query(
            "INSERT INTO operation_log (operation_type, media_id, details, status, error_message, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                operation_type,
                media_id,
                json.dumps(details) if details is not None else None,
                status,
                error_message,
                _now(),
            ),
        )

    def get_operation(self, operation_id: int) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM operation_log WHERE id = ?", (operation_id,))

    def list_operations(self, operation_type: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if operation_type:
            return self.fetch_all(
                "SELECT * FROM operation_log WHERE operation_type = ? ORDER BY created_at DESC LIMIT ?",
                (operation_type, limit),
            )
        return self.fetch_all("SELECT * FROM operation_log ORDER BY created_at DESC LIMIT ?", (limit,))


def _migration_v1(conn: sqlite3.Connection) -> None:
    """Baseline schema — no-op, _SCHEMA already creates v1 tables."""


def _migration_v2(conn: sqlite3.Connection) -> None:
    """Add 'delete' to operation_log.operation_type's allowed values, and
    ON DELETE SET NULL on media_id so deleting a media_items row (the new
    manual-cleanup delete feature) doesn't fail a foreign key check against
    its own history entries.

    SQLite can't ALTER a CHECK/FK constraint in place, so rebuild the table.
    """
    conn.executescript(
        """
        CREATE TABLE operation_log_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_type TEXT NOT NULL CHECK (operation_type IN ('archive', 'rename', 'purge', 'tracker_check', 'delete')),
            media_id INTEGER REFERENCES media_items(id) ON DELETE SET NULL,
            details TEXT,
            status TEXT NOT NULL CHECK (status IN ('success', 'failed', 'pending')),
            error_message TEXT,
            created_at TEXT NOT NULL
        );
        INSERT INTO operation_log_v2 SELECT * FROM operation_log;
        DROP TABLE operation_log;
        ALTER TABLE operation_log_v2 RENAME TO operation_log;
        """
    )


def _migration_v3(conn: sqlite3.Connection) -> None:
    """Add media_items.match_attempted_at, for the background TMDB metadata
    backfill (auto-adopted library-browser files start with tmdb_id NULL and
    no metadata; this tracks when a match was last tried so failed lookups
    get retried on a cooldown instead of every backfill cycle). A plain
    nullable column add -- unlike v2, this one doesn't need a table rebuild.
    """
    conn.execute("ALTER TABLE media_items ADD COLUMN match_attempted_at TEXT")


def _migration_v4(conn: sqlite3.Connection) -> None:
    """Add archive_tracker.muted, for per-title notification snoozing.
    Plain nullable-with-default column add, no rebuild needed."""
    conn.execute("ALTER TABLE archive_tracker ADD COLUMN muted INTEGER NOT NULL DEFAULT 0")


_MIGRATIONS = {
    1: _migration_v1,
    2: _migration_v2,
    3: _migration_v3,
    4: _migration_v4,
}
