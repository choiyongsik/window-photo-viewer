from pathlib import Path

from PySide6.QtCore import Qt

from core.models import MediaItem, MediaKind
from ui.media_list_model import MediaListModel
from ui.thumb_views import Filmstrip, GridView


def _model(n: int) -> MediaListModel:
    m = MediaListModel()
    m.set_items([MediaItem(Path(f"{i}.jpg"), MediaKind.IMAGE, 0, 1) for i in range(n)])
    return m


def test_filmstrip_is_single_row_and_tracks_current(qtbot):
    m = _model(5)
    view = Filmstrip()
    view.setModel(m)
    qtbot.addWidget(view)
    view.resize(600, 130)
    view.show()
    assert view.isWrapping() is False
    assert view.focusPolicy() == Qt.FocusPolicy.NoFocus
    view.set_current_row(3)
    assert view.current_row() == 3
    assert view.current_row() == view.currentIndex().row()


def test_click_emits_row_activated_and_double_click(qtbot):
    m = _model(4)
    view = GridView()
    view.setModel(m)
    qtbot.addWidget(view)
    view.resize(500, 500)
    view.show()
    qtbot.waitExposed(view)
    assert view.isWrapping() is True
    target = view.visualRect(m.index(2)).center()
    with qtbot.waitSignal(view.row_activated, timeout=1000) as blocker:
        qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=target)
    assert blocker.args == [2]
    with qtbot.waitSignal(view.row_double_clicked, timeout=1000) as blocker:
        qtbot.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, pos=target)
    assert blocker.args == [2]


def test_visible_rows_subset_after_scroll(qtbot):
    m = _model(60)
    view = Filmstrip()
    view.setModel(m)
    qtbot.addWidget(view)
    view.resize(400, 130)
    view.show()
    qtbot.waitExposed(view)
    rows = view.visible_rows()
    assert rows and rows[0] == 0 and len(rows) < 60
    view.set_current_row(59)
    assert 59 in view.visible_rows()


def test_set_current_row_out_of_range_is_ignored(qtbot):
    m = _model(2)
    view = Filmstrip()
    view.setModel(m)
    qtbot.addWidget(view)
    view.set_current_row(5)
    assert view.current_row() == -1
