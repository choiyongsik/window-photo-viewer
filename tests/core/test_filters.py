from pathlib import Path

from core.filters import NO_FILTER, Filter
from core.models import MediaItem, MediaKind


def _items(*ratings: int) -> list[MediaItem]:
    return [MediaItem(path=Path(f"{i}.jpg"), kind=MediaKind.IMAGE, mtime=0, size=1, rating=r)
            for i, r in enumerate(ratings)]


def test_no_filter_returns_all_indices():
    assert NO_FILTER.apply(_items(0, 3, -1)) == [0, 1, 2]
    assert NO_FILTER.is_active is False
    assert NO_FILTER.describe() == ""


def test_min_rating_excludes_lower_and_rejected():
    f = Filter(min_rating=3)
    assert f.apply(_items(0, 3, 5, 2, -1)) == [1, 2]
    assert f.is_active is True
    assert f.describe() == "★3+"


def test_rejected_only():
    f = Filter(rejected_only=True)
    assert f.apply(_items(0, -1, 5, -1)) == [1, 3]
    assert f.describe() == "reject"


def test_rejected_only_wins_over_min_rating():
    f = Filter(min_rating=2, rejected_only=True)
    assert f.apply(_items(3, -1)) == [1]


def test_exact_rating_matches_only_that_rating():
    f = Filter(exact_rating=3)
    assert f.apply(_items(0, 3, 5, 3, -1)) == [1, 3]
    assert f.is_active is True
    assert f.describe() == "★3"


def test_exact_rating_wins_over_min_rating():
    f = Filter(exact_rating=3, min_rating=1)
    assert f.apply(_items(3, 5, 1)) == [0]


def test_rejected_only_wins_over_exact_rating():
    f = Filter(rejected_only=True, exact_rating=3)
    assert f.apply(_items(3, -1)) == [1]
    assert f.describe() == "reject"
