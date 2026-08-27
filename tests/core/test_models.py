from pathlib import Path

from core.models import ExifSummary, Label, MediaItem, MediaKind, kind_for


def test_kind_for_extensions_case_insensitive():
    assert kind_for(Path("a.JPG")) is MediaKind.IMAGE
    assert kind_for(Path("a.jpeg")) is MediaKind.IMAGE
    assert kind_for(Path("a.MOV")) is MediaKind.VIDEO
    assert kind_for(Path("a.mp4")) is MediaKind.VIDEO
    assert kind_for(Path("a.png")) is MediaKind.IMAGE
    assert kind_for(Path("a.PNG")) is MediaKind.IMAGE
    assert kind_for(Path("a.gif")) is None
    assert kind_for(Path("a.xmp")) is None


def test_label_from_xmp():
    assert Label.from_xmp("Red") is Label.RED
    assert Label.from_xmp("") is Label.NONE
    assert Label.from_xmp(None) is Label.NONE
    assert Label.from_xmp("Purple") is Label.NONE


def _item(rating: int = 0) -> MediaItem:
    return MediaItem(path=Path("x.jpg"), kind=MediaKind.IMAGE, mtime=0.0, size=1, rating=rating)


def test_stars_and_rejected():
    assert _item(3).stars() == "★★★☆☆"
    assert _item(0).stars() == "☆☆☆☆☆"
    assert _item(-1).stars() == "✕"
    assert _item(-1).is_rejected is True
    assert _item(2).is_rejected is False


def test_exif_summary_format_skips_none():
    s = ExifSummary(exposure_time="1/250", f_number="f/2.8", iso=400, focal_length="35mm", date_time_original=None)
    assert s.format() == "1/250  f/2.8  ISO 400  35mm"
    assert ExifSummary(None, None, None, None, None).format() == ""
    assert s.orientation == 1
