import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def transcode_audio_to_mp3(source_path: Path, timeout_seconds: int = 45) -> Path | None:
    if source_path.suffix.lower() == ".mp3":
        return source_path
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.warning("audio_transcode_skipped_ffmpeg_missing", extra={"path": str(source_path)})
        return None
    target_path = source_path.with_suffix(".mp3")
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(source_path),
                "-vn",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "64k",
                str(target_path),
            ],
            check=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        logger.exception("audio_transcode_failed", extra={"path": str(source_path)})
        return None
    if not target_path.exists() or target_path.stat().st_size == 0:
        logger.warning("audio_transcode_empty_output", extra={"path": str(source_path)})
        return None
    return target_path
