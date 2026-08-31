"""Watches incoming (and in-place archive) folders for new video files and
flags them so the dashboard can prompt "N new file(s) -- Scan Library"
instead of the user needing to remember to click Scan on their own.

Never auto-archives or auto-scans anything itself -- TMDB matching still
needs a user's approval via the existing preview/confirm flow (or the
Radarr/Sonarr webhook path for already-organized imports). This only
shortens the gap between "a file landed on disk" and "the dashboard
noticed", the same problem the webhook receiver solves for Radarr/Sonarr's
own imports, but for anything dropped into the watched folders by any
other means (manual copy, a different download client, etc).
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from app.core.scanner import VIDEO_EXTENSIONS

logger = logging.getLogger(__name__)


class NewFileTracker:
    """Thread-safe set of newly-seen video file paths, drained by whatever
    "handles" them (a scan). Pure bookkeeping, no filesystem or OS-watcher
    code -- kept separate from the Observer/handler below so it's
    unit-testable without spinning up a real filesystem watch.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._paths: set[str] = set()

    def add(self, path: Path) -> None:
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            return
        with self._lock:
            self._paths.add(str(path))

    def count(self) -> int:
        with self._lock:
            return len(self._paths)

    def clear(self) -> int:
        """Drains and returns however many were pending -- called once a
        scan has actually run, so the badge doesn't keep counting files the
        user has already been shown."""
        with self._lock:
            n = len(self._paths)
            self._paths.clear()
            return n


class _Handler(FileSystemEventHandler):
    def __init__(self, tracker: NewFileTracker) -> None:
        self._tracker = tracker

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._tracker.add(Path(event.src_path))

    def on_moved(self, event) -> None:
        if not event.is_directory:
            self._tracker.add(Path(event.dest_path))


def start_watching(paths: list[Path], tracker: NewFileTracker) -> Observer:
    """Starts one Observer watching every path in `paths` recursively.
    A missing directory is skipped with a warning rather than raising -- a
    not-yet-created incoming folder shouldn't crash the whole app, and the
    permissions-check panel already exists to diagnose path problems.
    """
    observer = Observer()
    handler = _Handler(tracker)
    watched_any = False
    for path in paths:
        if not path.exists():
            logger.warning("Skipping filesystem watch for missing directory: %s", path)
            continue
        observer.schedule(handler, str(path), recursive=True)
        watched_any = True
    if watched_any:
        observer.start()
    return observer


def stop_watching(observer: Observer) -> None:
    """Safe to call even when start_watching() found no valid directory to
    watch and never actually started the observer thread -- Thread.join()
    raises if called before start(), so that case is skipped rather than
    letting a "nothing to watch" outcome blow up shutdown."""
    observer.stop()
    if observer.ident is not None:
        observer.join(timeout=5)
