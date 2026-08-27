from pathlib import Path

from PIL import Image

from tests.helpers import make_jpeg, make_video, scan_segment


def test_make_jpeg_creates_readable_jpeg(tmp_path: Path):
    p = make_jpeg(tmp_path / "a.jpg", size=(64, 48))
    with Image.open(p) as im:
        assert im.format == "JPEG"
        assert im.size == (64, 48)


def test_make_jpeg_writes_orientation(tmp_path: Path):
    p = make_jpeg(tmp_path / "a.jpg", orientation=6)
    with Image.open(p) as im:
        assert im.getexif()[0x0112] == 6


def test_scan_segment_starts_at_sos_marker(tmp_path: Path):
    data = make_jpeg(tmp_path / "a.jpg").read_bytes()
    seg = scan_segment(data)
    assert seg[:2] == b"\xff\xda"
    assert data.endswith(seg)


def test_make_video_creates_file(tmp_path: Path):
    p = make_video(tmp_path / "clip.mp4", seconds=1.0)
    assert p.exists() and p.stat().st_size > 0
