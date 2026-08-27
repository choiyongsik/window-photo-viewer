from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from core.metadata import (
    XMP_LABEL,
    XMP_RATING,
    MetadataError,
    populate,
    read_exif_summary,
    read_rating_label,
    sidecar_path,
    write_rating_label,
)
from core.models import Label, MediaItem, MediaKind
from tests.helpers import make_jpeg, scan_segment


def _make_jpeg_with_exif(path: Path) -> Path:
    im = Image.new("RGB", (32, 32), (0, 0, 0))
    exif = Image.Exif()
    exif[0x0112] = 8
    ifd = exif.get_ifd(0x8769)
    ifd[0x829A] = IFDRational(1, 250)   # ExposureTime
    ifd[0x829D] = IFDRational(28, 10)   # FNumber
    ifd[0x8827] = 400                   # ISO
    ifd[0x920A] = IFDRational(35, 1)    # FocalLength
    ifd[0x9003] = "2026:08:27 10:00:00" # DateTimeOriginal
    im.save(path, "JPEG", exif=exif.tobytes())
    return path


def test_jpeg_defaults_when_no_xmp(tmp_path: Path):
    p = make_jpeg(tmp_path / "a.jpg")
    assert read_rating_label(p, MediaKind.IMAGE) == (0, Label.NONE)


def test_jpeg_roundtrip_rating_and_label(tmp_path: Path):
    p = make_jpeg(tmp_path / "a.jpg")
    write_rating_label(p, MediaKind.IMAGE, 3, Label.RED)
    assert read_rating_label(p, MediaKind.IMAGE) == (3, Label.RED)

    write_rating_label(p, MediaKind.IMAGE, -1, Label.NONE)
    assert read_rating_label(p, MediaKind.IMAGE) == (-1, Label.NONE)


def test_jpeg_write_keeps_pixel_data_and_exif(tmp_path: Path):
    p = _make_jpeg_with_exif(tmp_path / "a.jpg")
    before = p.read_bytes()
    write_rating_label(p, MediaKind.IMAGE, 5, Label.GREEN)
    after = p.read_bytes()

    assert scan_segment(before) == scan_segment(after)
    with Image.open(p) as im:
        assert im.getexif()[0x0112] == 8
    assert not (tmp_path / "a.jpg.tmp").exists()


def test_jpeg_zero_rating_removes_tags(tmp_path: Path):
    import pyexiv2

    p = make_jpeg(tmp_path / "a.jpg")
    write_rating_label(p, MediaKind.IMAGE, 4, Label.BLUE)
    write_rating_label(p, MediaKind.IMAGE, 0, Label.NONE)
    with pyexiv2.ImageData(p.read_bytes()) as img:
        xmp = img.read_xmp()
    assert "Xmp.xmp.Rating" not in xmp
    assert "Xmp.xmp.Label" not in xmp


_RICH_XMP = (
    '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
    ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '  <rdf:Description rdf:about=""\n'
    '    xmlns:dc="http://purl.org/dc/elements/1.1/"\n'
    '    xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"\n'
    '    xmlns:stEvt="http://ns.adobe.com/xap/1.0/sType/ResourceEvent#"\n'
    '    xmlns:custom="http://example.com/ns/custom/1.0/">\n'
    "   <dc:subject>\n"
    "    <rdf:Bag>\n"
    "     <rdf:li>keyword1</rdf:li>\n"
    "     <rdf:li>keyword2</rdf:li>\n"
    "    </rdf:Bag>\n"
    "   </dc:subject>\n"
    "   <dc:title>\n"
    "    <rdf:Alt>\n"
    '     <rdf:li xml:lang="x-default">Test Title</rdf:li>\n'
    '     <rdf:li xml:lang="ko-KR">테스트 제목</rdf:li>\n'
    "    </rdf:Alt>\n"
    "   </dc:title>\n"
    "   <xmpMM:History>\n"
    "    <rdf:Seq>\n"
    '     <rdf:li rdf:parseType="Resource">\n'
    "      <stEvt:action>saved</stEvt:action>\n"
    "      <stEvt:instanceID>xmp.iid:1111</stEvt:instanceID>\n"
    "      <stEvt:when>2026-08-27T10:00:00+09:00</stEvt:when>\n"
    "     </rdf:li>\n"
    "    </rdf:Seq>\n"
    "   </xmpMM:History>\n"
    "   <custom:myTag>custom-value</custom:myTag>\n"
    "  </rdf:Description>\n"
    " </rdf:RDF>\n"
    "</x:xmpmeta>\n"
    '<?xpacket end="w"?>\n'
)


def test_jpeg_write_preserves_foreign_xmp(tmp_path: Path):
    """A JPEG carrying a real (Lightroom-shaped) XMP packet — a keyword bag,
    a lang-alt title with a non-ASCII value, a nested xmpMM:History struct,
    and an unregistered custom-namespace tag — must keep every key other
    than Rating/Label byte-for-byte, whether or not the write actually
    deletes a tag (i.e. through both the safe merge path and the
    clear_xmp()-based rebuild path)."""
    import pyexiv2

    p = make_jpeg(tmp_path / "a.jpg")
    with pyexiv2.ImageData(p.read_bytes()) as img:
        img.modify_raw_xmp(_RICH_XMP)
        seeded = img.get_bytes()
    p.write_bytes(seeded)

    with pyexiv2.ImageData(p.read_bytes()) as img:
        before = img.read_xmp()
    assert "Xmp.xmp.Rating" not in before
    assert "Xmp.xmp.Label" not in before
    assert "Xmp.dc.subject" in before  # sanity: seeding actually took

    def foreign(xmp: dict) -> dict:
        return {k: v for k, v in xmp.items() if k not in (XMP_RATING, XMP_LABEL)}

    # No existing Rating/Label to delete -> safe modify_xmp() merge path.
    write_rating_label(p, MediaKind.IMAGE, 3, Label.NONE)
    with pyexiv2.ImageData(p.read_bytes()) as img:
        after_rate = img.read_xmp()
    assert foreign(after_rate) == before
    assert after_rate[XMP_RATING] == "3"
    assert XMP_LABEL not in after_rate

    # Rating now exists and must be deleted -> clear_xmp() rebuild path.
    write_rating_label(p, MediaKind.IMAGE, 0, Label.NONE)
    with pyexiv2.ImageData(p.read_bytes()) as img:
        after_clear = img.read_xmp()
    assert foreign(after_clear) == before
    assert XMP_RATING not in after_clear
    assert XMP_LABEL not in after_clear


def test_jpeg_write_failure_raises_and_leaves_no_tmp(tmp_path: Path, monkeypatch):
    p = make_jpeg(tmp_path / "a.jpg")
    original = p.read_bytes()

    def boom(src, dst):
        raise PermissionError("locked")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(MetadataError):
        write_rating_label(p, MediaKind.IMAGE, 2, Label.NONE)
    assert p.read_bytes() == original
    assert not (tmp_path / "a.jpg.tmp").exists()


def test_corrupt_jpeg_reads_as_default_and_write_raises(tmp_path: Path):
    p = tmp_path / "bad.jpg"
    p.write_bytes(b"not a jpeg at all")
    assert read_rating_label(p, MediaKind.IMAGE) == (0, Label.NONE)
    with pytest.raises(MetadataError):
        write_rating_label(p, MediaKind.IMAGE, 1, Label.NONE)


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
def test_exiftool_reads_our_rating(tmp_path: Path):
    p = make_jpeg(tmp_path / "a.jpg")
    write_rating_label(p, MediaKind.IMAGE, 4, Label.YELLOW)
    out = subprocess.run(
        ["exiftool", "-s3", "-XMP:Rating", "-XMP:Label", str(p)],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert out == ["4", "Yellow"]


def test_video_sidecar_roundtrip(tmp_path: Path):
    v = tmp_path / "clip.mp4"
    v.write_bytes(b"\x00" * 16)
    assert sidecar_path(v) == tmp_path / "clip.xmp"
    assert read_rating_label(v, MediaKind.VIDEO) == (0, Label.NONE)

    write_rating_label(v, MediaKind.VIDEO, 2, Label.BLUE)
    assert sidecar_path(v).exists()
    assert read_rating_label(v, MediaKind.VIDEO) == (2, Label.BLUE)
    assert v.read_bytes() == b"\x00" * 16  # video itself untouched


def test_video_sidecar_zero_does_not_create_file(tmp_path: Path):
    v = tmp_path / "clip.mov"
    v.write_bytes(b"\x00")
    write_rating_label(v, MediaKind.VIDEO, 0, Label.NONE)
    assert not sidecar_path(v).exists()


def test_video_sidecar_reads_element_form(tmp_path: Path):
    v = tmp_path / "clip.mp4"
    v.write_bytes(b"\x00")
    sidecar_path(v).write_text(
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">'
        "<xmp:Rating>5</xmp:Rating><xmp:Label>Red</xmp:Label>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>",
        encoding="utf-8",
    )
    assert read_rating_label(v, MediaKind.VIDEO) == (5, Label.RED)


def test_read_exif_summary(tmp_path: Path):
    p = _make_jpeg_with_exif(tmp_path / "a.jpg")
    s = read_exif_summary(p)
    assert s is not None
    assert s.exposure_time == "1/250"
    assert s.f_number == "f/2.8"
    assert s.iso == 400
    assert s.focal_length == "35mm"
    assert s.date_time_original == "2026:08:27 10:00:00"
    assert s.orientation == 8


def test_read_exif_summary_without_exif_is_none_or_empty(tmp_path: Path):
    p = make_jpeg(tmp_path / "a.jpg")
    s = read_exif_summary(p)
    assert s is None or s.format() == ""


def test_populate_fills_item(tmp_path: Path):
    p = _make_jpeg_with_exif(tmp_path / "a.jpg")
    write_rating_label(p, MediaKind.IMAGE, 3, Label.RED)
    item = MediaItem(path=p, kind=MediaKind.IMAGE, mtime=0.0, size=1)
    populate(item)
    assert (item.rating, item.label) == (3, Label.RED)
    assert item.exif is not None and item.exif.iso == 400

    v = tmp_path / "clip.mp4"
    v.write_bytes(b"\x00")
    vitem = MediaItem(path=v, kind=MediaKind.VIDEO, mtime=0.0, size=1)
    populate(vitem)
    assert vitem.exif is None and vitem.rating == 0
