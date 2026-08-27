from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from core.filters import Filter
from core.models import MediaItem, MediaKind
from ui.media_list_model import MediaListModel


def _items(*ratings: int) -> list[MediaItem]:
    return [MediaItem(path=Path(f"IMG_{i}.jpg"), kind=MediaKind.IMAGE, mtime=0, size=1, rating=r)
            for i, r in enumerate(ratings)]


def test_rows_and_roles():
    m = MediaListModel()
    m.set_items(_items(0, 3))
    assert m.rowCount() == 2
    idx = m.index(1)
    assert m.data(idx, Qt.ItemDataRole.DisplayRole) == "IMG_1.jpg"
    assert m.data(idx, MediaListModel.ItemRole).rating == 3
    assert m.data(idx, MediaListModel.IndexRole) == 1
    assert m.data(idx, Qt.ItemDataRole.DecorationRole) is None
    assert m.data(idx, MediaListModel.FailedRole) is False


def test_filter_maps_rows_to_item_indices():
    m = MediaListModel()
    m.set_items(_items(0, 3, 5))
    m.set_filter(Filter(min_rating=3))
    assert m.rowCount() == 2
    assert m.visible_indices() == [1, 2]
    assert m.item_index_at_row(0) == 1
    assert m.item_at_row(1).rating == 5
    assert m.row_for_item_index(0) == -1
    assert m.row_for_item_index(2) == 1


def test_refresh_filter_after_rating_change():
    m = MediaListModel()
    m.set_items(_items(0, 3))
    m.set_filter(Filter(min_rating=3))
    m.items()[0].rating = 4
    m.refresh_filter()
    assert m.visible_indices() == [0, 1]


def test_thumbnail_storage_survives_filter_change_and_emits_datachanged(qtbot):
    m = MediaListModel()
    m.set_items(_items(0, 3))
    pm = QPixmap(4, 4)
    with qtbot.waitSignal(m.dataChanged, timeout=1000) as blocker:
        m.set_thumbnail(1, pm)
    assert blocker.args[0].row() == 1
    m.set_filter(Filter(min_rating=3))
    assert m.thumbnail(1) is pm
    assert m.data(m.index(0), Qt.ItemDataRole.DecorationRole) is pm


def test_thumbnail_failed_and_request_tracking():
    m = MediaListModel()
    m.set_items(_items(0))
    assert m.has_thumbnail_request(0) is False
    m.mark_requested(0)
    assert m.has_thumbnail_request(0) is True
    m.set_thumbnail_failed(0)
    assert m.data(m.index(0), MediaListModel.FailedRole) is True
    m.set_items(_items(1))
    assert m.has_thumbnail_request(0) is False  # reset clears tracking


def test_set_thumbnail_for_hidden_item_does_not_emit(qtbot):
    m = MediaListModel()
    m.set_items(_items(0, 3))
    m.set_filter(Filter(min_rating=3))
    with qtbot.assertNotEmitted(m.dataChanged):
        m.set_thumbnail(0, QPixmap(2, 2))
    assert m.thumbnail(0) is not None


def test_reorder_permutes_thumbnail_failed_and_requested_state_by_identity(qtbot):
    m = MediaListModel()
    items = _items(0, 3, 5)          # indices 0, 1, 2
    m.set_items(items)
    pm = QPixmap(4, 4)
    m.set_thumbnail(1, pm)           # items[1] has a thumbnail
    m.mark_requested(1)
    m.set_thumbnail_failed(2)        # items[2] failed

    reordered = [items[2], items[0], items[1]]   # new order: old-2, old-0, old-1
    m.reorder(reordered)

    assert m.items() == reordered
    assert m.thumbnail(2) is pm                  # followed items[1] to its new index (2)
    assert m.has_thumbnail_request(2) is True
    assert m.data(m.index(2), MediaListModel.FailedRole) is False
    assert m.data(m.index(0), MediaListModel.FailedRole) is True  # followed items[2] to index 0
    assert m.visible_indices() == [0, 1, 2]      # no filter active


def test_reorder_keeps_the_active_filter():
    m = MediaListModel()
    items = _items(0, 3, 5)
    m.set_items(items)
    m.set_filter(Filter(min_rating=3))
    assert m.visible_indices() == [1, 2]

    reordered = [items[2], items[1], items[0]]
    m.reorder(reordered)

    assert m.filter() == Filter(min_rating=3)
    assert m.visible_indices() == [0, 1]         # ratings 5 and 3, now at indices 0 and 1
