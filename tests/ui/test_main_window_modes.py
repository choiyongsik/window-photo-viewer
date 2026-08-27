from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt

from core import metadata
from core.filters import Filter
from core.scanner import scan
from core.thumbnails import ThumbnailCache
from tests.helpers import make_jpeg
from ui.main_window import MainWindow


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    for i, rating in enumerate([0, 3, 5, -1, 2], start=1):
        p = make_jpeg(tmp_path / f"IMG_{i}.jpg", size=(120, 80))
        if rating:
            metadata.write_rating_label(p, metadata.MediaKind.IMAGE, rating, metadata.Label.NONE)
    return tmp_path


def _items(folder: Path):
    items = scan(folder)
    for it in items:
        metadata.populate(it)
    return items


@pytest.fixture
def win(qtbot, tmp_path: Path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    w = MainWindow(thumb_cache=ThumbnailCache(tmp_path / "cache"), settings=settings)
    w.suppress_dialogs = True
    qtbot.addWidget(w)
    w.resize(1000, 700)
    w.show()
    qtbot.waitExposed(w)
    return w


def test_grid_and_loupe_switch(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    assert win.is_grid is False
    qtbot.keyClick(win, Qt.Key.Key_G)
    assert win.is_grid is True and win.mode_stack.currentWidget() is win.grid
    qtbot.keyClick(win, Qt.Key.Key_E)
    assert win.is_grid is False


def test_grid_double_click_opens_loupe_on_item(win, folder):
    win.load_items(_items(folder), folder)
    win.show_grid()
    win.grid.row_double_clicked.emit(3)
    assert win.is_grid is False and win.current == 3


def test_filter_keys(win, folder, qtbot):
    win.load_items(_items(folder), folder)          # ratings: 0,3,5,-1,2
    qtbot.keyClick(win, Qt.Key.Key_3, Qt.KeyboardModifier.AltModifier)
    assert win.model.visible_indices() == [1, 2]
    assert win.current == 1                          # current was hidden → first visible
    assert "★3+" in win.header.text() and "1/2" in win.header.text()
    qtbot.keyClick(win, Qt.Key.Key_Right)
    qtbot.keyClick(win, Qt.Key.Key_Right)
    assert win.current == 2                          # stays within the filtered list
    qtbot.keyClick(win, Qt.Key.Key_X, Qt.KeyboardModifier.AltModifier)
    assert win.model.visible_indices() == [3] and win.current == 3
    qtbot.keyClick(win, Qt.Key.Key_0, Qt.KeyboardModifier.AltModifier)
    assert win.model.visible_indices() == [0, 1, 2, 3, 4]
    assert win.current == 3                          # clearing keeps current


def test_rating_change_drops_item_out_of_filter_and_advances(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    win.set_filter(Filter(min_rating=3))             # visible [1, 2], current 1
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_1)            # IMG_2 → 1 star, no longer matches
    assert win.model.visible_indices() == [2]
    assert win.current == 2


def test_filter_with_no_matches_shows_empty_state(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    win.set_filter(Filter(min_rating=5))
    win.set_rating(4)                                # only 5★ item drops out
    assert win.model.visible_indices() == []
    assert win.current == -1
    assert "filter" in win.header.text() or "Ctrl+O" in win.header.text()
    win.clear_filter()
    assert win.current == 0


def test_fullscreen_toggle(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    qtbot.keyClick(win, Qt.Key.Key_F)
    assert win.isFullScreen()
    qtbot.keyClick(win, Qt.Key.Key_Escape)
    assert not win.isFullScreen()
    qtbot.keyClick(win, Qt.Key.Key_F11)
    assert win.isFullScreen()
    qtbot.keyClick(win, Qt.Key.Key_F11)
    assert not win.isFullScreen()


def test_auto_advance_setting(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    assert win.auto_advance is False
    win.auto_advance = True
    assert win.settings.value("auto_advance", type=bool) is True
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_2)
    assert win.current == 1
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_0)            # clearing never advances
    assert win.current == 1


def test_open_folder_remembers_last_folder(win, folder, qtbot):
    assert win.last_folder() is None
    with qtbot.waitSignal(win.signals.scan_finished, timeout=5000):
        win.open_folder(folder)
    qtbot.waitUntil(lambda: win.folder == folder, timeout=2000)
    assert win.last_folder() == folder
    assert win.current == 0


def test_missing_file_is_removed_when_shown(win, folder, qtbot):
    items = _items(folder)
    win.load_items(items, folder)
    qtbot.waitUntil(lambda: 1 in win.image_cache and not win._pending_images, timeout=5000)  # preload settled
    (folder / "IMG_2.jpg").unlink()
    win.image_cache.clear()                          # force a fresh decode attempt of the missing file
    win.next_item()                                  # tries to show IMG_2 → load fails
    qtbot.waitUntil(lambda: len(win.model.items()) == 4, timeout=5000)
    assert [i.path.name for i in win.model.items()] == ["IMG_1.jpg", "IMG_3.jpg", "IMG_4.jpg", "IMG_5.jpg"]
    assert win.current_item().path.name == "IMG_3.jpg"
    assert "IMG_2.jpg" in win.statusBar().currentMessage()


def test_thumbnail_priority_uses_active_view(win, folder):
    win.load_items(_items(folder), folder)
    win.show_grid()
    assert win._active_view() is win.grid
    win.show_loupe()
    assert win._active_view() is win.filmstrip
