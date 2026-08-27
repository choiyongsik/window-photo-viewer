from __future__ import annotations

import datetime
from enum import Enum

from core.models import MediaItem
from core.scanner import natural_key

_DESCRIPTIONS = {
    "name_asc": "파일명↑",
    "capture_desc": "촬영일↓",
    "mtime_desc": "수정시각↓",
}

_DATE_FORMATS = ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")


class SortMode(Enum):
    NAME_ASC = "name_asc"          # 파일명 자연 정렬 ↑ (current behavior)
    CAPTURE_DESC = "capture_desc"  # 촬영일시 ↓ (EXIF DateTimeOriginal, fallback mtime)
    MTIME_DESC = "mtime_desc"      # 수정시각 ↓

    def describe(self) -> str:
        return _DESCRIPTIONS[self.value]

    def next(self) -> "SortMode":
        order = [SortMode.NAME_ASC, SortMode.CAPTURE_DESC, SortMode.MTIME_DESC]
        return order[(order.index(self) + 1) % len(order)]

    @classmethod
    def from_value(cls, value: str | None) -> "SortMode":
        try:
            return cls(value) if value is not None else cls.NAME_ASC
        except ValueError:
            return cls.NAME_ASC


def capture_time(item: MediaItem) -> float:
    dto = item.exif.date_time_original if item.exif else None
    if dto:
        for fmt in _DATE_FORMATS:
            try:
                return datetime.datetime.strptime(dto, fmt).timestamp()
            except (ValueError, OverflowError, OSError):
                # .timestamp() on a naive datetime calls the platform mktime(), which
                # raises OSError on Windows for dates outside its representable range
                # (e.g. pre-1970 in a UTC+ timezone, or far-future dates) -- treat that
                # exactly like a normal parse failure and fall back to mtime.
                continue
    return item.mtime


def sort_items(items: list[MediaItem], mode: SortMode) -> list[MediaItem]:
    if mode is SortMode.NAME_ASC:
        return sorted(items, key=lambda i: natural_key(i.path.name))
    if mode is SortMode.CAPTURE_DESC:
        return sorted(items, key=lambda i: (-capture_time(i), natural_key(i.path.name)))
    return sorted(items, key=lambda i: (-i.mtime, natural_key(i.path.name)))
