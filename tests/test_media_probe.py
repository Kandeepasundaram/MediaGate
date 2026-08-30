from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from app.core import media_probe


def test_probe_file_returns_none_when_ffprobe_not_on_path(monkeypatch, tmp_path):
    monkeypatch.setattr(media_probe.shutil, "which", lambda name: None)
    assert media_probe.probe_file(tmp_path / "movie.mkv") is None
    assert media_probe.ffprobe_available() is False


def test_probe_file_parses_duration_and_streams(monkeypatch, tmp_path):
    monkeypatch.setattr(media_probe.shutil, "which", lambda name: "/usr/bin/ffprobe")

    payload = {
        "format": {"duration": "5432.1", "bit_rate": "4500000", "format_name": "matroska,webm"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }
    fake_proc = MagicMock(stdout=json.dumps(payload))
    monkeypatch.setattr(media_probe.subprocess, "run", lambda *a, **kw: fake_proc)

    result = media_probe.probe_file(tmp_path / "movie.mkv")
    assert result.duration_seconds == 5432.1
    assert result.width == 1920
    assert result.height == 1080
    assert result.video_codec == "h264"
    assert result.audio_codec == "aac"
    assert result.bitrate == 4500000
    assert result.container == "matroska,webm"


def test_probe_file_returns_none_on_subprocess_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(media_probe.shutil, "which", lambda name: "/usr/bin/ffprobe")

    def exploding_run(*a, **kw):
        raise subprocess.CalledProcessError(1, "ffprobe")

    monkeypatch.setattr(media_probe.subprocess, "run", exploding_run)
    assert media_probe.probe_file(tmp_path / "movie.mkv") is None


def test_probe_file_returns_none_on_malformed_json(monkeypatch, tmp_path):
    monkeypatch.setattr(media_probe.shutil, "which", lambda name: "/usr/bin/ffprobe")
    fake_proc = MagicMock(stdout="not json")
    monkeypatch.setattr(media_probe.subprocess, "run", lambda *a, **kw: fake_proc)

    assert media_probe.probe_file(tmp_path / "movie.mkv") is None


def test_probe_file_handles_missing_duration_and_streams(monkeypatch, tmp_path):
    monkeypatch.setattr(media_probe.shutil, "which", lambda name: "/usr/bin/ffprobe")
    fake_proc = MagicMock(stdout=json.dumps({"format": {}, "streams": []}))
    monkeypatch.setattr(media_probe.subprocess, "run", lambda *a, **kw: fake_proc)

    result = media_probe.probe_file(tmp_path / "movie.mkv")
    assert result.duration_seconds is None
    assert result.video_codec is None
    assert result.audio_codec is None
