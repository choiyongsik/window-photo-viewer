from pathlib import Path

from core.models import ExifSummary, MediaItem, MediaKind
from core.sorting import SortMode, capture_time, sort_items


def _exif(date_time_original: str | None) -> ExifSummary:
    return ExifSummary(
        exposure_time=None, f_number=None, iso=None, focal_length=None,
        date_time_original=date_time_original,
    )


def _item(name: str, mtime: float, dto: str | None = None, kind: MediaKind = MediaKind.IMAGE) -> MediaItem:
    exif = _exif(dto) if kind is MediaKind.IMAGE else None
    return MediaItem(path=Path(name), kind=kind, mtime=mtime, size=1, exif=exif)


def test_describe():
    assert SortMode.NAME_ASC.describe() == "파일명↑"
    assert SortMode.CAPTURE_DESC.describe() == "촬영일↓"
    assert SortMode.MTIME_DESC.describe() == "수정시각↓"


def test_next_cycles():
    assert SortMode.NAME_ASC.next() is SortMode.CAPTURE_DESC
    assert SortMode.CAPTURE_DESC.next() is SortMode.MTIME_DESC
    assert SortMode.MTIME_DESC.next() is SortMode.NAME_ASC


def test_from_value():
    assert SortMode.from_value("capture_desc") is SortMode.CAPTURE_DESC
    assert SortMode.from_value("mtime_desc") is SortMode.MTIME_DESC
    assert SortMode.from_value("name_asc") is SortMode.NAME_ASC
    assert SortMode.from_value("bogus") is SortMode.NAME_ASC
    assert SortMode.from_value(None) is SortMode.NAME_ASC


def test_capture_time_parses_colon_format():
    item = _item("a.jpg", mtime=100.0, dto="2026:08:27 10:00:00")
    import datetime
    expected = datetime.datetime(2026, 8, 27, 10, 0, 0).timestamp()
    assert capture_time(item) == expected


def test_capture_time_parses_dash_format():
    item = _item("a.jpg", mtime=100.0, dto="2026-08-27 10:00:00")
    import datetime
    expected = datetime.datetime(2026, 8, 27, 10, 0, 0).timestamp()
    assert capture_time(item) == expected


def test_capture_time_falls_back_to_mtime_on_missing_exif():
    item = _item("a.jpg", mtime=123.0, dto=None)
    assert capture_time(item) == 123.0


def test_capture_time_falls_back_to_mtime_on_parse_failure():
    item = _item("a.jpg", mtime=123.0, dto="not-a-date")
    assert capture_time(item) == 123.0


def test_capture_time_video_has_no_exif_falls_back_to_mtime():
    item = _item("clip.mp4", mtime=456.0, kind=MediaKind.VIDEO)
    assert capture_time(item) == 456.0


def test_sort_items_name_asc():
    items = [_item("IMG_10.jpg", mtime=1), _item("IMG_2.jpg", mtime=2), _item("IMG_1.jpg", mtime=3)]
    result = sort_items(items, SortMode.NAME_ASC)
    assert [i.path.name for i in result] == ["IMG_1.jpg", "IMG_2.jpg", "IMG_10.jpg"]


def test_sort_items_does_not_mutate_input():
    items = [_item("b.jpg", mtime=1), _item("a.jpg", mtime=2)]
    original_order = list(items)
    sort_items(items, SortMode.NAME_ASC)
    assert items == original_order


def test_sort_items_capture_desc_with_tie_break():
    items = [
        _item("b.jpg", mtime=1, dto="2026:08:27 10:00:00"),
        _item("a.jpg", mtime=2, dto="2026:08:27 10:00:00"),  # tie -> natural name asc
        _item("c.jpg", mtime=3, dto="2026:08:28 10:00:00"),  # later capture -> first
    ]
    result = sort_items(items, SortMode.CAPTURE_DESC)
    assert [i.path.name for i in result] == ["c.jpg", "a.jpg", "b.jpg"]


def test_sort_items_mtime_desc_with_tie_break():
    items = [
        _item("b.jpg", mtime=5),
        _item("a.jpg", mtime=5),  # tie -> natural name asc
        _item("c.jpg", mtime=10),
    ]
    result = sort_items(items, SortMode.MTIME_DESC)
    assert [i.path.name for i in result] == ["c.jpg", "a.jpg", "b.jpg"]


def test_sort_items_capture_desc_video_sorts_by_mtime():
    items = [
        _item("v.mp4", mtime=10, kind=MediaKind.VIDEO),
        _item("p.jpg", mtime=5, dto="2026:08:27 10:00:00"),
    ]
    result = sort_items(items, SortMode.CAPTURE_DESC)
    # v.mp4's capture_time falls back to mtime=10, which is > p.jpg's parsed capture time (below mtime=5 baseline year 2026)
    import datetime
    p_capture = datetime.datetime(2026, 8, 27, 10, 0, 0).timestamp()
    assert p_capture > 10  # sanity: p.jpg's real capture time is far larger than v.mp4's mtime fallback
    assert [i.path.name for i in result] == ["p.jpg", "v.mp4"]
