from pathlib import Path

import pytest

from core.models import MediaKind
from core.scanner import natural_key, scan
from tests.helpers import make_jpeg


def test_natural_key_orders_numbers_numerically():
    names = ["IMG_10.jpg", "IMG_2.jpg", "IMG_1.jpg", "img_3.JPG"]
    assert sorted(names, key=natural_key) == ["IMG_1.jpg", "IMG_2.jpg", "img_3.JPG", "IMG_10.jpg"]


def test_scan_filters_extensions_and_sorts(tmp_path: Path):
    make_jpeg(tmp_path / "IMG_10.jpg")
    make_jpeg(tmp_path / "IMG_2.JPEG")
    (tmp_path / "clip.MP4").write_bytes(b"\x00" * 10)
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "IMG_2.xmp").write_text("<x/>")
    (tmp_path / "sub").mkdir()
    make_jpeg(tmp_path / "sub" / "nested.jpg")  # not recursive

    items = scan(tmp_path)

    assert [i.path.name for i in items] == ["clip.MP4", "IMG_2.JPEG", "IMG_10.jpg"]
    assert [i.kind for i in items] == [MediaKind.VIDEO, MediaKind.IMAGE, MediaKind.IMAGE]
    assert all(i.rating == 0 and i.exif is None for i in items)
    assert items[1].size == (tmp_path / "IMG_2.JPEG").stat().st_size
    assert items[1].mtime == (tmp_path / "IMG_2.JPEG").stat().st_mtime


def test_scan_skips_hidden_and_tmp_files(tmp_path: Path):
    make_jpeg(tmp_path / ".hidden.jpg")
    make_jpeg(tmp_path / "keep.jpg")
    (tmp_path / "keep.jpg.tmp").write_bytes(b"partial")
    assert [i.path.name for i in scan(tmp_path)] == ["keep.jpg"]


def test_scan_empty_folder(tmp_path: Path):
    assert scan(tmp_path) == []


def test_scan_errors(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        scan(tmp_path / "nope")
    f = make_jpeg(tmp_path / "a.jpg")
    with pytest.raises(NotADirectoryError):
        scan(f)
