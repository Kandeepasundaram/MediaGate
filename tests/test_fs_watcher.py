from __future__ import annotations

import time
from unittest.mock import MagicMock

from app.core.fs_watcher import NewFileTracker, _Handler, start_watching, stop_watching


def test_new_file_tracker_add_filters_by_video_extension(tmp_path):
    tracker = NewFileTracker()
    tracker.add(tmp_path / "movie.mkv")
    tracker.add(tmp_path / "notes.txt")
    assert tracker.count() == 1


def test_new_file_tracker_clear_drains_and_returns_count(tmp_path):
    tracker = NewFileTracker()
    tracker.add(tmp_path / "a.mkv")
    tracker.add(tmp_path / "b.mp4")
    assert tracker.clear() == 2
    assert tracker.count() == 0


def test_new_file_tracker_dedupes_same_path(tmp_path):
    tracker = NewFileTracker()
    tracker.add(tmp_path / "a.mkv")
    tracker.add(tmp_path / "a.mkv")
    assert tracker.count() == 1


def test_handler_on_created_adds_to_tracker(tmp_path):
    tracker = NewFileTracker()
    handler = _Handler(tracker)
    event = MagicMock(is_directory=False, src_path=str(tmp_path / "movie.mkv"))
    handler.on_created(event)
    assert tracker.count() == 1


def test_handler_on_created_ignores_directory_events(tmp_path):
    tracker = NewFileTracker()
    handler = _Handler(tracker)
    event = MagicMock(is_directory=True, src_path=str(tmp_path))
    handler.on_created(event)
    assert tracker.count() == 0


def test_handler_on_moved_adds_dest_path(tmp_path):
    tracker = NewFileTracker()
    handler = _Handler(tracker)
    event = MagicMock(is_directory=False, dest_path=str(tmp_path / "renamed.mkv"))
    handler.on_moved(event)
    assert tracker.count() == 1


def test_start_watching_skips_missing_directory(tmp_path):
    tracker = NewFileTracker()
    observer = start_watching([tmp_path / "does_not_exist"], tracker)
    try:
        assert not observer.is_alive()
    finally:
        stop_watching(observer)


def test_start_watching_starts_observer_for_existing_directory(tmp_path):
    tracker = NewFileTracker()
    observer = start_watching([tmp_path], tracker)
    try:
        assert observer.is_alive()
    finally:
        stop_watching(observer)


def test_start_watching_detects_new_file(tmp_path):
    tracker = NewFileTracker()
    observer = start_watching([tmp_path], tracker)
    try:
        (tmp_path / "new_movie.mkv").write_bytes(b"data")
        deadline = time.time() + 5
        while time.time() < deadline and tracker.count() == 0:
            time.sleep(0.1)
        assert tracker.count() == 1
    finally:
        stop_watching(observer)
