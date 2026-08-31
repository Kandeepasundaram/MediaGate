"""Best-effort video file inspection via ffprobe (part of the ffmpeg
package). Optional -- if ffprobe isn't on PATH (e.g. a deploy that hasn't
picked up the image update yet), probe_file() just returns None and the
detail pane shows file name/size only, no duration/codec/resolution.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class MediaProbeResult:
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    bitrate: int | None = None
    container: str | None = None
    hdr: bool = False
    audio_channels: int | None = None


_HDR_TRANSFER_FUNCTIONS = {"smpte2084", "arib-std-b67"}  # PQ (HDR10/HDR10+/DV) and HLG


def _is_hdr(video_stream: dict) -> bool:
    return video_stream.get("color_transfer") in _HDR_TRANSFER_FUNCTIONS


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def resolution_bucket(height: int | None) -> str | None:
    """Coarse resolution label for filtering -- exact pixel dimensions
    aren't useful as a filter facet, but "4K vs 1080p vs 720p vs SD" is.
    None (never probed, or ffprobe unavailable) is its own bucket in the UI
    rather than being lumped in with SD."""
    if height is None:
        return None
    if height >= 2000:
        return "4K"
    if height >= 1000:
        return "1080p"
    if height >= 700:
        return "720p"
    return "SD"


def probe_file(path: Path) -> MediaProbeResult | None:
    if not ffprobe_available():
        return None
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        data = json.loads(proc.stdout)
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
        logger.warning("ffprobe failed for %s: %s", path, exc)
        return None

    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    return MediaProbeResult(
        duration_seconds=float(fmt["duration"]) if fmt.get("duration") else None,
        width=video_stream.get("width") if video_stream else None,
        height=video_stream.get("height") if video_stream else None,
        video_codec=video_stream.get("codec_name") if video_stream else None,
        audio_codec=audio_stream.get("codec_name") if audio_stream else None,
        bitrate=int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
        container=fmt.get("format_name"),
        hdr=_is_hdr(video_stream) if video_stream else False,
        audio_channels=audio_stream.get("channels") if audio_stream else None,
    )
