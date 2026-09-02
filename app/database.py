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

SCHEMA_VERSION = 22

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
    imdb_id TEXT,
    manual_override INTEGER NOT NULL DEFAULT 0,
    tags TEXT,
    watched_at TEXT,
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
    snoozed_until TEXT,
    check_interval_hours REAL,
    created_at TEXT NOT NULL,
    next_episode_air_date TEXT,
    poster_path TEXT,
    overview TEXT,
    watched_through_season INTEGER,
    watched_through_episode INTEGER,
    category TEXT NOT NULL DEFAULT 'watching',
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

CREATE TABLE IF NOT EXISTS notification_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracker_id INTEGER REFERENCES archive_tracker(id) ON DELETE SET NULL,
    tmdb_id INTEGER,
    media_type TEXT NOT NULL CHECK (media_type IN ('movie', 'tv')),
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    scope TEXT NOT NULL DEFAULT 'read_write'
);

CREATE TABLE IF NOT EXISTS storage_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    used_bytes INTEGER NOT NULL,
    total_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS viewers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS viewer_watched_items (
    viewer_id INTEGER NOT NULL REFERENCES viewers(id) ON DELETE CASCADE,
    media_item_id INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
    watched_at TEXT NOT NULL,
    PRIMARY KEY (viewer_id, media_item_id)
);

CREATE TABLE IF NOT EXISTS tv_shows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tmdb_id INTEGER NOT NULL UNIQUE,
    title TEXT NOT NULL,
    imdb_id TEXT,
    poster_path TEXT,
    overview TEXT,
    genres TEXT,
    status TEXT NOT NULL DEFAULT 'watching' CHECK (status IN ('watching', 'running', 'season_done', 'cancelled', 'ended')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS universes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    media_type TEXT NOT NULL CHECK (media_type IN ('movie', 'tv')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS universe_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    universe_id INTEGER NOT NULL REFERENCES universes(id) ON DELETE CASCADE,
    tmdb_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    poster_path TEXT,
    added_at TEXT NOT NULL,
    UNIQUE (universe_id, tmdb_id)
);

CREATE INDEX IF NOT EXISTS idx_media_items_tmdb_id ON media_items(tmdb_id);
CREATE INDEX IF NOT EXISTS idx_media_items_final_path ON media_items(final_path);
CREATE INDEX IF NOT EXISTS idx_media_items_media_type ON media_items(media_type);
CREATE INDEX IF NOT EXISTS idx_operation_log_created_at ON operation_log(created_at);
CREATE INDEX IF NOT EXISTS idx_storage_snapshots_label_created ON storage_snapshots(label, created_at);
CREATE INDEX IF NOT EXISTS idx_universe_members_universe_id ON universe_members(universe_id);
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
        for json_field in ("metadata", "tags"):
            if json_field in fields and not isinstance(fields[json_field], (str, type(None))):
                fields[json_field] = json.dumps(fields[json_field])
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        self.execute_query(
            f"UPDATE media_items SET {set_clause} WHERE id = ?",
            (*fields.values(), item_id),
        )

    def delete_media_item(self, item_id: int) -> None:
        self.execute_query("DELETE FROM media_items WHERE id = ?", (item_id,))

    def count_media_items_for_tmdb(self, tmdb_id: int, media_type: str) -> int:
        """Used after a delete to tell whether a title has fully left the
        archive (0) or is still partially owned (e.g. other TV episodes
        remain) -- see library_browse.py's _delete_target."""
        row = self.fetch_one(
            "SELECT COUNT(*) AS n FROM media_items WHERE tmdb_id = ? AND media_type = ?",
            (tmdb_id, media_type),
        )
        return row["n"]

    def get_media_item_by_final_path(self, path: str) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM media_items WHERE final_path = ?", (path,))

    def list_all_tags(self) -> list[str]:
        """Distinct tag values across the whole library, for the gallery
        tag-filter dropdown -- computed in Python rather than SQL since
        tags is a JSON array column, not a normalized table (a personal
        library's tag set is small; no need for a join table)."""
        tags: set[str] = set()
        for row in self.fetch_all("SELECT tags FROM media_items WHERE tags IS NOT NULL"):
            try:
                values = json.loads(row["tags"])
            except json.JSONDecodeError:
                continue
            if isinstance(values, list):
                tags.update(str(v) for v in values)
        return sorted(tags)

    def record_storage_snapshot(self, label: str, used_bytes: int, total_bytes: int) -> None:
        """At most one row per label per calendar day (UTC) -- the storage
        forecast only needs a daily resolution, and this is called on every
        /api/status/storage request (each Settings tab load), so without
        the dedup a heavily-refreshed dashboard would flood the table."""
        today = _now()[:10]
        existing = self.fetch_one(
            "SELECT id FROM storage_snapshots WHERE label = ? AND substr(created_at, 1, 10) = ?",
            (label, today),
        )
        if existing:
            self.execute_query(
                "UPDATE storage_snapshots SET used_bytes = ?, total_bytes = ?, created_at = ? WHERE id = ?",
                (used_bytes, total_bytes, _now(), existing["id"]),
            )
        else:
            self.execute_query(
                "INSERT INTO storage_snapshots (label, used_bytes, total_bytes, created_at) VALUES (?, ?, ?, ?)",
                (label, used_bytes, total_bytes, _now()),
            )

    def list_storage_snapshots(self, label: str, since_days: int = 90) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
        return self.fetch_all(
            "SELECT * FROM storage_snapshots WHERE label = ? AND created_at >= ? ORDER BY created_at ASC",
            (label, cutoff),
        )

    def list_storage_snapshots_in_range(self, since: str, until: str) -> list[dict[str, Any]]:
        """All snapshot rows (any label) within [since, until], ordered so
        the Reports page can take the first/last row per label as that
        label's start/end usage for the period -- no config dependency
        (unlike get_storage_status, labels here are whatever's already been
        recorded, not re-derived from configured paths)."""
        return self.fetch_all(
            "SELECT * FROM storage_snapshots WHERE created_at >= ? AND created_at <= ? "
            "ORDER BY label ASC, created_at ASC",
            (since, until),
        )

    def search_media_items(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Cross-type (movie + tv) title *and* overview search for the
        header's global search box -- unlike list_media_items (full table,
        client-side filtered per gallery tab), this runs server-side so a
        match can be found and jumped to without first loading every item
        into the page. The overview text lives inside the `metadata` JSON
        blob, not its own column, so a plain LIKE on that column is used as
        a cheap prefilter (over-fetches -- it can also hit unrelated JSON
        keys like poster_path) and the real title-or-overview check happens
        in Python on the prefiltered rows, avoiding any dependency on
        SQLite's optional JSON1 extension being compiled in.
        """
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        rows = self.fetch_all(
            "SELECT * FROM media_items WHERE title LIKE ? ESCAPE '\\' OR metadata LIKE ? ESCAPE '\\' "
            "ORDER BY (watched = 0) DESC, title ASC LIMIT ?",
            (like, like, limit * 3),
        )
        q = query.strip().lower()

        def _matches(row: dict) -> bool:
            if q in (row["title"] or "").lower():
                return True
            try:
                meta = json.loads(row["metadata"]) if row.get("metadata") else {}
            except json.JSONDecodeError:
                meta = {}
            return q in (meta.get("overview") or "").lower()

        return [r for r in rows if _matches(r)][:limit]

    def list_unmatched_media_items(self, retry_cooldown_hours: float = 6.0, limit: int = 1) -> list[dict[str, Any]]:
        """Auto-adopted items with no TMDB match yet, for the metadata
        backfill background task. Never-attempted items come first; a
        previously-failed lookup is only retried after `retry_cooldown_hours`
        so a title with no real TMDB match doesn't get re-searched on every
        backfill cycle. manual_override rows are excluded permanently -- the
        user has already given this item a title on purpose, so the backfill
        must never overwrite it once the cooldown lapses."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=retry_cooldown_hours)).isoformat()
        return self.fetch_all(
            "SELECT * FROM media_items "
            "WHERE tmdb_id IS NULL AND manual_override = 0 "
            "AND (match_attempted_at IS NULL OR match_attempted_at < ?) "
            "ORDER BY (match_attempted_at IS NULL) DESC, created_at ASC "
            "LIMIT ?",
            (cutoff, limit),
        )

    def list_items_missing_vote_average(self, retry_cooldown_hours: float = 6.0, limit: int = 1) -> list[dict[str, Any]]:
        """Already-matched items (tmdb_id set) whose metadata predates the
        vote_average field, so it was never captured. Same cooldown pattern
        as list_unmatched_media_items, reusing match_attempted_at, so this
        self-heals an older library without a manual "Refresh Metadata"
        click and without re-querying a row every backfill cycle once it's
        been attempted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=retry_cooldown_hours)).isoformat()
        return self.fetch_all(
            "SELECT * FROM media_items "
            "WHERE tmdb_id IS NOT NULL AND json_extract(metadata, '$.vote_average') IS NULL "
            "AND (match_attempted_at IS NULL OR match_attempted_at < ?) "
            "ORDER BY (match_attempted_at IS NULL) DESC, created_at ASC "
            "LIMIT ?",
            (cutoff, limit),
        )

    def count_unmatched_media_items(self, media_type: str | None = None) -> int:
        if media_type:
            row = self.fetch_one(
                "SELECT COUNT(*) AS n FROM media_items WHERE tmdb_id IS NULL AND manual_override = 0 AND media_type = ?",
                (media_type,),
            )
        else:
            row = self.fetch_one("SELECT COUNT(*) AS n FROM media_items WHERE tmdb_id IS NULL AND manual_override = 0")
        return row["n"]

    def count_failed_match_items(self, media_type: str | None = None) -> int:
        """Auto-adopted items that have been searched at least once with no
        TMDB match found (as opposed to ones still waiting their first try)."""
        if media_type:
            row = self.fetch_one(
                "SELECT COUNT(*) AS n FROM media_items "
                "WHERE tmdb_id IS NULL AND manual_override = 0 AND match_attempted_at IS NOT NULL AND media_type = ?",
                (media_type,),
            )
        else:
            row = self.fetch_one(
                "SELECT COUNT(*) AS n FROM media_items "
                "WHERE tmdb_id IS NULL AND manual_override = 0 AND match_attempted_at IS NOT NULL"
            )
        return row["n"]

    def reset_failed_match_attempts(self, media_type: str | None = None) -> int:
        """Clears match_attempted_at for every previously-failed (searched,
        no TMDB match found) item so the next backfill cycle retries them
        immediately instead of waiting out the retry cooldown -- for "TMDB
        was down when this last ran" or "I just fixed the file name".
        Returns the number of rows reset."""
        query = "UPDATE media_items SET match_attempted_at = NULL WHERE tmdb_id IS NULL AND manual_override = 0 AND match_attempted_at IS NOT NULL"
        params: tuple = ()
        if media_type:
            query += " AND media_type = ?"
            params = (media_type,)
        with self.connect() as conn:
            cur = conn.execute(query, params)
            return cur.rowcount

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

    def update_tracker(self, tracker_id: int, **fields: Any) -> None:
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        self.execute_query(
            f"UPDATE archive_tracker SET {set_clause} WHERE id = ?",
            (*fields.values(), tracker_id),
        )

    # ---- universes / universe_members CRUD ----

    def create_universe(self, name: str, media_type: str) -> int:
        return self.execute_query(
            "INSERT INTO universes (name, media_type, created_at) VALUES (?, ?, ?)",
            (name, media_type, _now()),
        )

    def list_universes(self, media_type: str | None = None) -> list[dict[str, Any]]:
        if media_type:
            return self.fetch_all(
                "SELECT * FROM universes WHERE media_type = ? ORDER BY created_at DESC", (media_type,)
            )
        return self.fetch_all("SELECT * FROM universes ORDER BY created_at DESC")

    def get_universe(self, universe_id: int) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM universes WHERE id = ?", (universe_id,))

    def delete_universe(self, universe_id: int) -> None:
        # universe_members rows cascade via their own FK (ON DELETE CASCADE,
        # honored since connect() turns PRAGMA foreign_keys on).
        self.execute_query("DELETE FROM universes WHERE id = ?", (universe_id,))

    def add_universe_member(
        self, universe_id: int, tmdb_id: int, title: str, poster_path: str | None = None
    ) -> int | None:
        return self.execute_query(
            "INSERT OR IGNORE INTO universe_members (universe_id, tmdb_id, title, poster_path, added_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (universe_id, tmdb_id, title, poster_path, _now()),
        )

    def list_universe_members(self, universe_id: int) -> list[dict[str, Any]]:
        return self.fetch_all(
            "SELECT * FROM universe_members WHERE universe_id = ? ORDER BY added_at ASC", (universe_id,)
        )

    def remove_universe_member(self, universe_id: int, member_id: int) -> None:
        self.execute_query(
            "DELETE FROM universe_members WHERE id = ? AND universe_id = ?", (member_id, universe_id)
        )

    def list_all_universe_members(self) -> list[dict[str, Any]]:
        """Every universe_members row across every universe, for the Reports
        page's period-scoped "titles added to a universe" metric -- avoids
        an N+1 loop over list_universes() + list_universe_members() per
        universe (homelab-scale, but no reason not to do it in one query)."""
        return self.fetch_all("SELECT * FROM universe_members ORDER BY added_at ASC")

    def list_universe_member_tmdb_ids(self, media_type: str) -> set[int]:
        """Every tmdb_id already a member of any universe of this type --
        used to dedup suggestions (a title shouldn't be re-suggested for a
        second universe once it already belongs to one) and to filter the
        standalone tracked-titles list (a title moves out of "standalone"
        once it joins a universe)."""
        rows = self.fetch_all(
            "SELECT um.tmdb_id FROM universe_members um "
            "JOIN universes u ON u.id = um.universe_id WHERE u.media_type = ?",
            (media_type,),
        )
        return {r["tmdb_id"] for r in rows}

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

    # ---- notification_history CRUD ----

    def log_notification(self, tracker_id: int | None, tmdb_id: int | None, media_type: str, title: str, message: str) -> int:
        return self.execute_query(
            "INSERT INTO notification_history (tracker_id, tmdb_id, media_type, title, message, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tracker_id, tmdb_id, media_type, title, message, _now()),
        )

    def list_notification_history(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.fetch_all(
            "SELECT * FROM notification_history ORDER BY created_at DESC LIMIT ?", (limit,)
        )

    def list_notification_history_in_range(self, since: str, until: str) -> list[dict[str, Any]]:
        """notification_history rows fired within [since, until] (both
        inclusive ISO date/datetime strings) -- for the Reports page's
        tracker-activity section (how many new seasons/episodes/releases
        were signaled during the period, distinct from whether they were
        ever actually archived)."""
        return self.fetch_all(
            "SELECT * FROM notification_history WHERE created_at >= ? AND created_at <= ? ORDER BY created_at ASC",
            (since, until),
        )

    def get_operation(self, operation_id: int) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM operation_log WHERE id = ?", (operation_id,))

    def list_operations(
        self,
        operation_type: str | None = None,
        limit: int = 100,
        status: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        """since/until are inclusive ISO date/datetime strings (e.g. "2026-01-01"
        or a full created_at timestamp) -- plain string comparison works
        because created_at is always stored as an ISO 8601 string, which
        sorts lexicographically the same as chronologically."""
        clauses = []
        params: list[Any] = []
        if operation_type:
            clauses.append("operation_type = ?")
            params.append(operation_type)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if since:
            clauses.append("created_at >= ?")
            params.append(since)
        if until:
            clauses.append("created_at <= ?")
            params.append(until)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        return self.fetch_all(f"SELECT * FROM operation_log {where} ORDER BY created_at DESC LIMIT ?", tuple(params))

    # ---- API tokens ----

    def create_api_token(self, name: str, token: str, scope: str = "read_write") -> int:
        return self.execute_query(
            "INSERT INTO api_tokens (name, token, created_at, scope) VALUES (?, ?, ?, ?)",
            (name, token, _now(), scope),
        )

    def list_api_tokens(self) -> list[dict[str, Any]]:
        return self.fetch_all("SELECT * FROM api_tokens ORDER BY created_at DESC")

    def get_api_token_by_value(self, token: str) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM api_tokens WHERE token = ?", (token,))

    def touch_api_token(self, token_id: int) -> None:
        self.execute_query("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (_now(), token_id))

    def delete_api_token(self, token_id: int) -> None:
        self.execute_query("DELETE FROM api_tokens WHERE id = ?", (token_id,))

    # ---- viewers (per-viewer watch state) ----

    def create_viewer(self, name: str) -> int:
        return self.execute_query("INSERT INTO viewers (name, created_at) VALUES (?, ?)", (name, _now()))

    def list_viewers(self) -> list[dict[str, Any]]:
        return self.fetch_all("SELECT * FROM viewers ORDER BY name ASC")

    def get_viewer(self, viewer_id: int) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM viewers WHERE id = ?", (viewer_id,))

    def delete_viewer(self, viewer_id: int) -> None:
        # viewer_watched_items rows cascade via their own FK (ON DELETE
        # CASCADE, honored since connect() turns PRAGMA foreign_keys on).
        self.execute_query("DELETE FROM viewers WHERE id = ?", (viewer_id,))

    def set_viewer_watched(self, viewer_id: int, media_item_id: int, watched: bool) -> None:
        if watched:
            self.execute_query(
                "INSERT OR IGNORE INTO viewer_watched_items (viewer_id, media_item_id, watched_at) VALUES (?, ?, ?)",
                (viewer_id, media_item_id, _now()),
            )
        else:
            self.execute_query(
                "DELETE FROM viewer_watched_items WHERE viewer_id = ? AND media_item_id = ?",
                (viewer_id, media_item_id),
            )

    def list_viewer_watched_ids(self, viewer_id: int) -> set[int]:
        rows = self.fetch_all("SELECT media_item_id FROM viewer_watched_items WHERE viewer_id = ?", (viewer_id,))
        return {r["media_item_id"] for r in rows}

    def count_viewer_watched_in_range(self, since: str, until: str) -> dict[int, int]:
        """viewer_id -> count of items that viewer marked watched within
        [since, until] (both inclusive ISO date/datetime strings) -- for the
        Reports page's per-viewer watch-activity breakdown. Reads
        viewer_watched_items.watched_at directly rather than media_items'
        own (global, single-value) watched_at, since two viewers can each
        have their own watched_at for the same item."""
        rows = self.fetch_all(
            "SELECT viewer_id, COUNT(*) AS n FROM viewer_watched_items "
            "WHERE watched_at >= ? AND watched_at <= ? GROUP BY viewer_id",
            (since, until),
        )
        return {r["viewer_id"]: r["n"] for r in rows}

    def sum_viewer_watch_seconds_in_range(self, since: str, until: str) -> dict[int, float]:
        """viewer_id -> total duration_seconds of items that viewer marked
        watched within [since, until], for the Reports page's watch-time
        column -- sibling of count_viewer_watched_in_range. duration_seconds
        only exists in a row's metadata once that file has been probed via
        ffprobe (see media_probe.probe_file, cached on first detail-pane/
        file-info open), so this is a best-effort figure: items never
        opened in the detail pane contribute 0, same "best-effort ffprobe"
        trade-off the resolution/HDR badges already accept. Summed in
        Python rather than via SQLite's json_extract to avoid depending on
        the optional JSON1 extension being compiled in (see
        search_media_items)."""
        rows = self.fetch_all(
            "SELECT vwi.viewer_id AS viewer_id, mi.metadata AS metadata FROM viewer_watched_items vwi "
            "JOIN media_items mi ON mi.id = vwi.media_item_id "
            "WHERE vwi.watched_at >= ? AND vwi.watched_at <= ?",
            (since, until),
        )
        totals: dict[int, float] = {}
        for row in rows:
            try:
                meta = json.loads(row["metadata"]) if row["metadata"] else {}
            except json.JSONDecodeError:
                meta = {}
            seconds = meta.get("duration_seconds")
            if seconds:
                totals[row["viewer_id"]] = totals.get(row["viewer_id"], 0.0) + float(seconds)
        return totals

    def is_viewer_watched(self, viewer_id: int, media_item_id: int) -> bool:
        row = self.fetch_one(
            "SELECT 1 FROM viewer_watched_items WHERE viewer_id = ? AND media_item_id = ?",
            (viewer_id, media_item_id),
        )
        return row is not None

    # ---- tv_shows (persists a show's identity + status independently of
    # media_items, so deleting every episode file doesn't drop the show
    # from the TV tab -- see TvShowStatus in models.py) ----

    def sync_tv_show(
        self, tmdb_id: int, title: str, imdb_id: str | None = None,
        poster_path: str | None = None, overview: str | None = None, genres: list[str] | None = None,
    ) -> None:
        """Upserts everything except `status`, which is only ever set
        explicitly (see set_tv_show_status) -- a re-sync (every /api/library/tv
        load) must never silently reset a status the user chose. Called for
        every tmdb-matched show still on disk, before it could ever be
        deleted, so the row -- and its status -- outlives the last episode file.
        """
        existing = self.fetch_one("SELECT id FROM tv_shows WHERE tmdb_id = ?", (tmdb_id,))
        genres_json = json.dumps(genres or [])
        if existing:
            self.execute_query(
                "UPDATE tv_shows SET title = ?, imdb_id = COALESCE(?, imdb_id), poster_path = COALESCE(?, poster_path), "
                "overview = COALESCE(NULLIF(?, ''), overview), genres = ?, updated_at = ? WHERE id = ?",
                (title, imdb_id, poster_path, overview or "", genres_json, _now(), existing["id"]),
            )
        else:
            self.execute_query(
                "INSERT INTO tv_shows (tmdb_id, title, imdb_id, poster_path, overview, genres, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'watching', ?, ?)",
                (tmdb_id, title, imdb_id, poster_path, overview or "", genres_json, _now(), _now()),
            )

    def get_tv_show(self, tmdb_id: int) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM tv_shows WHERE tmdb_id = ?", (tmdb_id,))

    def list_tv_shows(self) -> list[dict[str, Any]]:
        return self.fetch_all("SELECT * FROM tv_shows ORDER BY title ASC")

    def set_tv_show_status(self, tmdb_id: int, status: str) -> None:
        self.execute_query(
            "UPDATE tv_shows SET status = ?, updated_at = ? WHERE tmdb_id = ?",
            (status, _now(), tmdb_id),
        )

    # ---- maintenance ----

    def maintenance_checkpoint_and_vacuum(self) -> None:
        """WAL grows unbounded between checkpoints under sustained write
        activity, and VACUUM reclaims space left by deleted rows (delete-file,
        rematches). Both are safe to run online; neither needs a connection
        held open afterward, so a plain short-lived connect() is fine."""
        with self.connect() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")


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


def _migration_v5(conn: sqlite3.Connection) -> None:
    """Add media_items.imdb_id -- resolved lazily (via TMDB external_ids)
    the first time a ratings lookup is requested for that item, or set
    directly when the user manually matches via an IMDb id in the detail
    pane. Cached here so repeat ratings lookups don't need another TMDB
    round trip just to re-derive the same imdb_id."""
    conn.execute("ALTER TABLE media_items ADD COLUMN imdb_id TEXT")


def _migration_v6(conn: sqlite3.Connection) -> None:
    """Index the columns the gallery/browse/backfill/history queries filter
    or join on. None of these existed before -- every lookup by tmdb_id,
    final_path, or media_type was a full table scan, fine at a few hundred
    rows but not as a personal library grows into the thousands."""
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_media_items_tmdb_id ON media_items(tmdb_id);
        CREATE INDEX IF NOT EXISTS idx_media_items_final_path ON media_items(final_path);
        CREATE INDEX IF NOT EXISTS idx_media_items_media_type ON media_items(media_type);
        CREATE INDEX IF NOT EXISTS idx_operation_log_created_at ON operation_log(created_at);
        """
    )


def _migration_v8(conn: sqlite3.Connection) -> None:
    """Add archive_tracker.snoozed_until (temporarily suppress a title's
    checks after "remind me later", distinct from muted's permanent
    silence) and check_interval_hours (an optional per-title override of the
    global daily tracker.cron_time, for a title that changes often enough --
    or rarely enough -- to warrant its own cadence). Both nullable, no
    rebuild needed."""
    conn.executescript(
        """
        ALTER TABLE archive_tracker ADD COLUMN snoozed_until TEXT;
        ALTER TABLE archive_tracker ADD COLUMN check_interval_hours REAL;
        """
    )


def _migration_v7(conn: sqlite3.Connection) -> None:
    """Add media_items.manual_override -- set when the user gives an item a
    custom title/year with no TMDB match (see /api/library/{id}/override).
    Without this flag the metadata backfill would eventually re-search and
    overwrite the manually-chosen title once its match_attempted_at cooldown
    lapses. Plain nullable-with-default column add, no rebuild needed."""
    conn.execute("ALTER TABLE media_items ADD COLUMN manual_override INTEGER NOT NULL DEFAULT 0")


def _migration_v9(conn: sqlite3.Connection) -> None:
    """Add notification_history -- a permanent record of tracker
    notifications actually surfaced to the user (Notifications tab / a
    webhook digest), independent of archive_tracker.pending_notification
    (which is transient and gets cleared on acknowledge/snooze). A new
    table rather than reusing operation_log, whose operation_type CHECK
    would need a table rebuild (like v2) to add a new allowed value."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS notification_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tracker_id INTEGER REFERENCES archive_tracker(id) ON DELETE SET NULL,
            tmdb_id INTEGER,
            media_type TEXT NOT NULL CHECK (media_type IN ('movie', 'tv')),
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )


def _migration_v10(conn: sqlite3.Connection) -> None:
    """Add api_tokens -- named, individually-revocable API tokens (Settings
    > API Tokens), alongside the original single shared server.api_token in
    config.yaml (kept for backward compatibility, still checked first by
    require_api_token()). A new table, not a rework of the config-based
    token, so existing single-token deploys keep working unchanged."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS api_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            token TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            last_used_at TEXT
        );
        """
    )


def _migration_v11(conn: sqlite3.Connection) -> None:
    """Add media_items.tags -- free-form user labels (JSON array of strings)
    for the Movies/TV galleries' tag filter, distinct from TMDB genres
    (which come from the API/scraper, not the user) and from
    library.py's existing manual_override (a title correction, not a
    label). Plain nullable column add, no rebuild needed."""
    conn.execute("ALTER TABLE media_items ADD COLUMN tags TEXT")


def _migration_v12(conn: sqlite3.Connection) -> None:
    """Add storage_snapshots -- one row per configured media path per day
    it's checked, for the Settings storage card's days-to-full forecast.
    Recorded opportunistically by GET /api/status/storage itself (at most
    once per label per day) rather than a new scheduler task, since the
    dashboard already polls that endpoint on every Settings tab load --
    no extra background thread needed for a number that only needs to
    change daily."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS storage_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            label TEXT NOT NULL,
            used_bytes INTEGER NOT NULL,
            total_bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_storage_snapshots_label_created ON storage_snapshots(label, created_at);
        """
    )


def _migration_v13(conn: sqlite3.Connection) -> None:
    """Add api_tokens.scope -- 'read_write' (default, matches every token
    created before this migration) or 'read_only', enforced in
    dependencies.py's require_api_token() against the request method.
    Plain column add with a default, no rebuild needed."""
    conn.execute("ALTER TABLE api_tokens ADD COLUMN scope TEXT NOT NULL DEFAULT 'read_write'")


def _migration_v14(conn: sqlite3.Connection) -> None:
    """Add viewers + viewer_watched_items -- lightweight per-viewer watch
    state for a household sharing one LAN dashboard with no login system
    (see dependencies.require_api_token's own "no login system" note).
    Named profiles, no password -- deliberately not real accounts, just
    enough to let "has Alex watched this" differ from "has Sam watched
    this" without a real multi-user auth system. The existing
    media_items.watched stays as the single global flag every other
    feature (filters, Continue Watching, CSV export, badges) already reads;
    this is an additive, opt-in layer on top, not a replacement."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS viewers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS viewer_watched_items (
            viewer_id INTEGER NOT NULL REFERENCES viewers(id) ON DELETE CASCADE,
            media_item_id INTEGER NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
            watched_at TEXT NOT NULL,
            PRIMARY KEY (viewer_id, media_item_id)
        );
        """
    )


def _migration_v15(conn: sqlite3.Connection) -> None:
    """Add tv_shows -- a show's identity (title/poster/overview/genres) and
    user-set status (watching/running/season_done/cancelled/ended), kept
    independently of media_items so a show stays visible in the TV tab even
    after every one of its episode files (and their media_items rows) has
    been deleted from disk. Synced from GET /api/library/tv on every load
    (see sync_tv_show), so any show the user has ever browsed to is
    registered here before it could be deleted."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tv_shows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tmdb_id INTEGER NOT NULL UNIQUE,
            title TEXT NOT NULL,
            imdb_id TEXT,
            poster_path TEXT,
            overview TEXT,
            genres TEXT,
            status TEXT NOT NULL DEFAULT 'watching' CHECK (status IN ('watching', 'running', 'season_done', 'cancelled', 'ended')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )


def _migration_v16(conn: sqlite3.Connection) -> None:
    """Add archive_tracker.next_episode_air_date -- informational only,
    populated from TVmaze when enabled (see tracker.check_tv_show); doesn't
    change the existing season-count-based pending_notification trigger.
    Plain nullable column add, no rebuild needed."""
    conn.execute("ALTER TABLE archive_tracker ADD COLUMN next_episode_air_date TEXT")


def _migration_v17(conn: sqlite3.Connection) -> None:
    """Add universes + universe_members -- named franchise/shared-universe
    groupings (e.g. "Vampire Diaries", "MCU") spanning multiple tracked
    titles, for the dedicated Tracker page. A member's live tracker state
    (pending_notification, latest season, etc.) is looked up from
    archive_tracker at read time rather than duplicated here, so it can
    never drift out of sync with the tracker row it refers to."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS universes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            media_type TEXT NOT NULL CHECK (media_type IN ('movie', 'tv')),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS universe_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            universe_id INTEGER NOT NULL REFERENCES universes(id) ON DELETE CASCADE,
            tmdb_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            poster_path TEXT,
            added_at TEXT NOT NULL,
            UNIQUE (universe_id, tmdb_id)
        );
        CREATE INDEX IF NOT EXISTS idx_universe_members_universe_id ON universe_members(universe_id);
        """
    )


def _migration_v18(conn: sqlite3.Connection) -> None:
    """Add archive_tracker.poster_path/overview -- lets the Tracker page
    render a poster gallery (like Movies/TV) instead of plain text rows,
    same as universe_members.poster_path already does for universe cards.
    Plain nullable column adds, no rebuild needed."""
    conn.execute("ALTER TABLE archive_tracker ADD COLUMN poster_path TEXT")
    conn.execute("ALTER TABLE archive_tracker ADD COLUMN overview TEXT")


def _migration_v19(conn: sqlite3.Connection) -> None:
    """Add media_items.watched_at -- timestamp of when an item was last
    marked watched, set by set_watched/set_watched_batch (library.py) and
    cleared back to NULL on unwatch. The global `watched` flag itself has
    never carried a timestamp; this is purely additive for the Reports
    page's "watch activity in period" (count of items marked watched
    within a date range), which watched=1 alone can't answer. Plain
    nullable column add, no rebuild needed."""
    conn.execute("ALTER TABLE media_items ADD COLUMN watched_at TEXT")


def _migration_v20(conn: sqlite3.Connection) -> None:
    """Add archive_tracker.watched_through_season/episode -- lets a tracked
    (not-yet-archived) TV show record "watched up through S02E05" progress
    even though no media_items rows/files exist for it yet. Distinct from
    media_items.watched, which is per-episode and requires an actual
    archived file; this is a single progress marker on the tracker row
    itself. Plain nullable column adds, no rebuild needed."""
    conn.execute("ALTER TABLE archive_tracker ADD COLUMN watched_through_season INTEGER")
    conn.execute("ALTER TABLE archive_tracker ADD COLUMN watched_through_episode INTEGER")


def _migration_v21(conn: sqlite3.Connection) -> None:
    """Add archive_tracker.category -- distinguishes why a title is tracked:
    'watching' (owned, watching for a new season/sequel -- the prior,
    implicit default for every row), 'interested' (a recommendation/wishlist
    entry, not owned), or 'watched' (was owned, its file(s) were deleted --
    an auto-populated history bucket, see library_browse.py's _delete_target).
    No CHECK constraint (would need the table-rebuild pattern _migration_v2
    used) -- validated at the API boundary via a Pydantic Literal instead."""
    conn.execute("ALTER TABLE archive_tracker ADD COLUMN category TEXT NOT NULL DEFAULT 'watching'")


def _migration_v22(conn: sqlite3.Connection) -> None:
    """Backfill media_items.archived_at for rows adopted from a pre-existing
    library (app/core/library_adopt.py) before it started stamping archived_at
    itself -- those rows were inserted with archived_at left NULL, which made
    them permanently invisible to growth reporting (reports.py, status.py's
    monthly chart) since both filter/bucket on archived_at. created_at is set
    on every row (create_media_item's own default) and is the closest
    available proxy for "date this file entered the app"."""
    conn.execute(
        "UPDATE media_items SET archived_at = created_at "
        "WHERE archived_at IS NULL AND created_at IS NOT NULL"
    )


_MIGRATIONS = {
    1: _migration_v1,
    2: _migration_v2,
    3: _migration_v3,
    4: _migration_v4,
    5: _migration_v5,
    6: _migration_v6,
    7: _migration_v7,
    8: _migration_v8,
    9: _migration_v9,
    10: _migration_v10,
    11: _migration_v11,
    12: _migration_v12,
    13: _migration_v13,
    14: _migration_v14,
    15: _migration_v15,
    16: _migration_v16,
    17: _migration_v17,
    18: _migration_v18,
    19: _migration_v19,
    20: _migration_v20,
    21: _migration_v21,
    22: _migration_v22,
}
