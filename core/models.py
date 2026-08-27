from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

IMAGE_EXTENSIONS: frozenset[str] = frozenset({".jpg", ".jpeg"})
VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".mov"})


class MediaKind(Enum):
    IMAGE = "image"
    VIDEO = "video"


class Label(Enum):
    NONE = ""
    RED = "Red"
    YELLOW = "Yellow"
    GREEN = "Green"
    BLUE = "Blue"

    @classmethod
    def from_xmp(cls, value: str | None) -> "Label":
        try:
            return cls(value or "")
        except ValueError:
            return cls.NONE


def kind_for(path: Path) -> MediaKind | None:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return MediaKind.IMAGE
    if ext in VIDEO_EXTENSIONS:
        return MediaKind.VIDEO
    return None


@dataclass(frozen=True)
class ExifSummary:
    exposure_time: str | None
    f_number: str | None
    iso: int | None
    focal_length: str | None
    date_time_original: str | None
    orientation: int = 1

    def format(self) -> str:
        parts = [
            self.exposure_time,
            self.f_number,
            f"ISO {self.iso}" if self.iso is not None else None,
            self.focal_length,
        ]
        return "  ".join(p for p in parts if p)


@dataclass
class MediaItem:
    path: Path
    kind: MediaKind
    mtime: float
    size: int
    rating: int = 0
    label: Label = Label.NONE
    exif: ExifSummary | None = None
    write_error: str | None = None

    @property
    def is_rejected(self) -> bool:
        return self.rating == -1

    def stars(self) -> str:
        if self.is_rejected:
            return "✕"
        return "★" * self.rating + "☆" * (5 - self.rating)
