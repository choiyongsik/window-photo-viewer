from __future__ import annotations

from dataclasses import dataclass

from core.models import MediaItem


@dataclass(frozen=True)
class Filter:
    min_rating: int | None = None
    rejected_only: bool = False

    @property
    def is_active(self) -> bool:
        return self.rejected_only or self.min_rating is not None

    def matches(self, item: MediaItem) -> bool:
        if self.rejected_only:
            return item.is_rejected
        if self.min_rating is not None:
            return item.rating >= self.min_rating
        return True

    def apply(self, items: list[MediaItem]) -> list[int]:
        return [i for i, item in enumerate(items) if self.matches(item)]

    def describe(self) -> str:
        if self.rejected_only:
            return "reject"
        if self.min_rating is not None:
            return f"★{self.min_rating}+"
        return ""


NO_FILTER = Filter()
