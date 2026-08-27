"""Test fixtures: synthesize JPEG/video files without checking binaries into git."""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image


def make_jpeg(
    path: Path,
    size: tuple[int, int] = (64, 48),
    color: tuple[int, int, int] = (200, 30, 30),
    orientation: int | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", size, color)
    exif = Image.Exif()
    if orientation is not None:
        exif[0x0112] = orientation
    im.save(path, "JPEG", quality=90, exif=exif.tobytes())
    return path


def make_video(path: Path, seconds: float = 2.0) -> Path:
    """Generate a tiny solid-color MP4 with the ffmpeg binary bundled by imageio-ffmpeg."""
    import imageio_ffmpeg

    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [
            ffmpeg, "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=blue:s=64x64:d={seconds}:r=10",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )
    return path


def scan_segment(data: bytes) -> bytes:
    """Bytes from the JPEG SOS marker (0xFFDA) to the end — the compressed pixel data."""
    idx = data.find(b"\xff\xda")
    if idx < 0:
        raise ValueError("no SOS marker")
    return data[idx:]
