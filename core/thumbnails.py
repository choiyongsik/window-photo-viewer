from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from PIL import Image, ImageOps

from core.models import MediaItem, MediaKind

THUMB_SIZE = 256
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ThumbnailError(Exception):
    pass


def default_cache_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / "WindowPhotoViewer" / "thumbs"


def make_image_thumbnail(src: Path, dst: Path, size: int = THUMB_SIZE) -> None:
    try:
        with Image.open(src) as im:
            # JPEG DCT-domain downscale: decodes at 1/2, 1/4, 1/8 — far cheaper than full decode.
            im.draft("RGB", (size * 2, size * 2))
            im = ImageOps.exif_transpose(im) or im
            im.thumbnail((size, size), Image.Resampling.LANCZOS)
            im.convert("RGB").save(dst, "JPEG", quality=85)
    except Exception as exc:
        raise ThumbnailError(f"{src.name}: {exc}") from exc


def make_video_thumbnail(src: Path, dst: Path, size: int = THUMB_SIZE) -> None:
    import imageio_ffmpeg

    try:
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise ThumbnailError(f"{src.name}: ffmpeg unavailable: {exc}") from exc

    frame = dst.with_name(dst.stem + ".frame.jpg")
    try:
        for seek in ("1", "0"):  # 1s in; fall back to first frame for very short clips
            frame.unlink(missing_ok=True)
            proc = subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error", "-ss", seek, "-i", str(src),
                 "-frames:v", "1", "-q:v", "3", str(frame)],
                capture_output=True, creationflags=_NO_WINDOW,
            )
            if proc.returncode == 0 and frame.exists() and frame.stat().st_size > 0:
                make_image_thumbnail(frame, dst, size)
                return
        raise ThumbnailError(f"{src.name}: ffmpeg produced no frame")
    finally:
        frame.unlink(missing_ok=True)


class ThumbnailCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir

    def cache_path(self, item: MediaItem) -> Path:
        key = hashlib.sha1(f"{item.path}|{item.mtime}|{item.size}".encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key}.jpg"

    def get_or_create(self, item: MediaItem) -> Path:
        dst = self.cache_path(item)
        if dst.exists():
            return dst
        part = dst.with_name(dst.stem + ".part.jpg")
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            if item.kind is MediaKind.VIDEO:
                make_video_thumbnail(item.path, part)
            else:
                make_image_thumbnail(item.path, part)
            os.replace(part, dst)
        except ThumbnailError:
            part.unlink(missing_ok=True)
            raise
        except OSError as exc:
            part.unlink(missing_ok=True)
            raise ThumbnailError(f"{item.path.name}: {exc}") from exc
        return dst
