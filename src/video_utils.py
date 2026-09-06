import json
import subprocess
import shutil
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class VideoInfo:
    def __init__(
        self,
        duration: float,
        width: int,
        height: int,
        has_audio: bool
    ):
        self.duration = duration
        self.width = width
        self.height = height
        self.has_audio = has_audio

    @property
    def is_vertical(self) -> bool:
        return self.height > self.width

    def is_short(self, max_shorts_duration: int = 180) -> bool:
        """YouTube Shorts are vertical (<= 1:1 or 9:16) and <= 3 minutes (180s)."""
        return self.is_vertical and self.duration <= max_shorts_duration

    def __repr__(self):
        return (
            f"<VideoInfo duration={self.duration:.1f}s, resolution={self.width}x{self.height}, "
            f"has_audio={self.has_audio}, is_vertical={self.is_vertical}>"
        )


def inspect_video(video_path: Path) -> VideoInfo:
    """Uses ffprobe to extract video duration, dimensions, and audio stream existence."""
    if not shutil.which("ffprobe"):
        logger.warning("ffprobe not found on system PATH. Assuming valid video with audio.")
        return VideoInfo(duration=60.0, width=1080, height=1920, has_audio=True)

    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "stream=codec_type,width,height:format=duration",
        "-of", "json",
        str(video_path)
    ]

    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        data = json.loads(result.stdout)
        
        streams = data.get("streams", [])
        format_info = data.get("format", {})

        duration = float(format_info.get("duration", 0.0))
        width = 0
        height = 0
        has_audio = False

        for stream in streams:
            codec_type = stream.get("codec_type")
            if codec_type == "video" and not width:
                width = int(stream.get("width", 0))
                height = int(stream.get("height", 0))
            elif codec_type == "audio":
                has_audio = True

        return VideoInfo(
            duration=duration,
            width=width,
            height=height,
            has_audio=has_audio
        )
    except Exception as e:
        logger.error(f"Failed to probe video {video_path}: {e}")
        # Default fallback
        return VideoInfo(duration=60.0, width=1080, height=1920, has_audio=True)
