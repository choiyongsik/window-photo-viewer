from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image

from core.models import MediaItem, MediaKind
from core.thumbnails import (
    THUMB_SIZE,
    ThumbnailCache,
    ThumbnailError,
    default_cache_dir,
    make_image_thumbnail,
    make_video_thumbnail,
)
from tests.helpers import make_jpeg, make_video


def _item(path: Path, kind: MediaKind = MediaKind.IMAGE) -> MediaItem:
    st = path.stat()
    return MediaItem(path=path, kind=kind, mtime=st.st_mtime, size=st.st_size)


def test_default_cache_dir_uses_localappdata(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_cache_dir() == tmp_path / "WindowPhotoViewer" / "thumbs"


def test_image_thumbnail_longest_side_256(tmp_path: Path):
    src = make_jpeg(tmp_path / "a.jpg", size=(1200, 600))
    dst = tmp_path / "t.jpg"
    make_image_thumbnail(src, dst)
    with Image.open(dst) as im:
        assert im.size == (256, 128)
        assert im.format == "JPEG"


def test_image_thumbnail_applies_exif_orientation(tmp_path: Path):
    src = make_jpeg(tmp_path / "a.jpg", size=(400, 200), orientation=6)
    dst = tmp_path / "t.jpg"
    make_image_thumbnail(src, dst)
    with Image.open(dst) as im:
        assert im.size == (128, 256)


def test_image_thumbnail_does_not_upscale(tmp_path: Path):
    src = make_jpeg(tmp_path / "a.jpg", size=(64, 48))
    dst = tmp_path / "t.jpg"
    make_image_thumbnail(src, dst)
    with Image.open(dst) as im:
        assert im.size == (64, 48)


def test_video_thumbnail_extracts_frame(tmp_path: Path):
    src = make_video(tmp_path / "clip.mp4", seconds=2.0)
    dst = tmp_path / "t.jpg"
    make_video_thumbnail(src, dst)
    with Image.open(dst) as im:
        assert im.format == "JPEG"
        assert max(im.size) <= THUMB_SIZE
        assert im.getpixel((im.width // 2, im.height // 2))[2] > 150  # blue clip


def test_video_thumbnail_short_clip_falls_back_to_start(tmp_path: Path):
    src = make_video(tmp_path / "short.mp4", seconds=0.3)
    dst = tmp_path / "t.jpg"
    make_video_thumbnail(src, dst)
    assert dst.exists()


def test_video_thumbnail_error_on_garbage(tmp_path: Path):
    src = tmp_path / "bad.mp4"
    src.write_bytes(b"\x00" * 100)
    with pytest.raises(ThumbnailError):
        make_video_thumbnail(src, tmp_path / "t.jpg")


def test_cache_key_depends_on_path_mtime_size(tmp_path: Path):
    cache = ThumbnailCache(tmp_path / "cache")
    a = make_jpeg(tmp_path / "a.jpg")
    item = _item(a)
    p1 = cache.cache_path(item)
    assert p1.parent == tmp_path / "cache" and p1.suffix == ".jpg"

    item2 = MediaItem(path=a, kind=MediaKind.IMAGE, mtime=item.mtime + 1, size=item.size)
    assert cache.cache_path(item2) != p1


def test_cache_hit_does_not_regenerate(tmp_path: Path):
    cache = ThumbnailCache(tmp_path / "cache")
    item = _item(make_jpeg(tmp_path / "a.jpg", size=(500, 500)))
    p = cache.get_or_create(item)
    assert p.exists()
    first_mtime_ns = p.stat().st_mtime_ns
    p2 = cache.get_or_create(item)
    assert p2 == p and p2.stat().st_mtime_ns == first_mtime_ns


def test_cache_handles_video_and_corrupt_image(tmp_path: Path):
    cache = ThumbnailCache(tmp_path / "cache")
    v = _item(make_video(tmp_path / "clip.mp4", seconds=1.0), MediaKind.VIDEO)
    assert cache.get_or_create(v).exists()

    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"nope")
    with pytest.raises(ThumbnailError):
        cache.get_or_create(_item(bad))
    assert not any(tmp_path.joinpath("cache").glob("*.part.jpg"))
