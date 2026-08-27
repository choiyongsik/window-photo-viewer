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
def siblings(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    for name in ("A", "B", "C"):
        for i in range(2):
            make_jpeg(root / name / f"{name}_{i}.jpg", size=(80, 60))
    return root


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
    assert "Alt+0" in win.header.text()
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


def test_auto_advance_on_last_item_still_refreshes_header(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    win.auto_advance = True
    win.last_item()
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_4)                # next_item() is a no-op here
    assert win.current == 4
    assert "★★★★☆" in win.header.text()


def test_open_folder_remembers_last_folder(win, folder, qtbot):
    assert win.last_folder() is None
    with qtbot.waitSignal(win.signals.scan_finished, timeout=5000):
        win.open_folder(folder)
    qtbot.waitUntil(lambda: win.folder == folder, timeout=2000)
    assert win.last_folder() == folder
    assert win.current == 0


def test_second_open_wins_over_a_scan_still_in_flight(win, folder, tmp_path, qtbot):
    """scan_pool is one FIFO thread: A finishes after the user already asked for B, so
    A's items must not be adopted (and must not become B's `last_folder`)."""
    second = tmp_path / "second"
    for i in range(1, 4):
        make_jpeg(second / f"B_{i}.jpg", size=(60, 40))
    seen: list[tuple] = []
    win.signals.scan_finished.connect(
        lambda items, f: seen.append((f, win.folder, len(win.model.items())))
    )

    win.open_folder(folder)          # 5 items
    win.open_folder(second)          # 3 items, queued behind the first scan

    qtbot.waitUntil(lambda: len(seen) == 2, timeout=10000)
    assert seen[0][0] == folder and seen[0][1] is None and seen[0][2] == 0   # A ignored
    assert seen[1][0] == second
    assert win.folder == second
    assert [i.path.name for i in win.model.items()] == ["B_1.jpg", "B_2.jpg", "B_3.jpg"]
    assert win.last_folder() == second


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


def test_missing_file_before_current_keeps_current_item(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    qtbot.waitUntil(lambda: not win._pending_images, timeout=5000)  # initial preload settled
    win.next_item()
    win.next_item()
    qtbot.waitUntil(lambda: not win._pending_images, timeout=5000)  # preload around IMG_3 settled
    assert win.current == 2                          # IMG_3
    (folder / "IMG_1.jpg").unlink()
    win.image_cache.clear()                          # force a fresh decode attempt of the missing file
    win._request_image(0)                             # mirrors what the preloader would do for a left neighbor
    qtbot.waitUntil(lambda: len(win.model.items()) == 4, timeout=5000)
    assert win.current_item().path.name == "IMG_3.jpg"
    assert win.current == 1


def test_thumbnail_priority_uses_active_view(win, folder):
    win.load_items(_items(folder), folder)
    win.show_grid()
    assert win._active_view() is win.grid
    win.show_loupe()
    assert win._active_view() is win.filmstrip


def test_pagedown_opens_next_sibling_folder(win, siblings, qtbot):
    win.open_folder(siblings / "A")
    qtbot.waitUntil(lambda: win.folder == siblings / "A", timeout=5000)

    qtbot.keyClick(win, Qt.Key.Key_PageDown)
    qtbot.waitUntil(lambda: win.folder == siblings / "B", timeout=5000)
    assert len(win.model.items()) == 2
    assert win.folder_panel.current_folder() == siblings / "B"

    qtbot.keyClick(win, Qt.Key.Key_PageUp)
    qtbot.waitUntil(lambda: win.folder == siblings / "A", timeout=5000)

    qtbot.keyClick(win, Qt.Key.Key_PageUp)   # already first: no-op
    qtbot.wait(100)
    assert win.folder == siblings / "A"

    win.open_folder(siblings / "C")
    qtbot.waitUntil(lambda: win.folder == siblings / "C", timeout=5000)
    qtbot.keyClick(win, Qt.Key.Key_PageDown)   # already last: no-op
    qtbot.wait(100)
    assert win.folder == siblings / "C"


def test_folder_panel_click_opens_folder(win, siblings, qtbot):
    win.open_folder(siblings / "A")
    qtbot.waitUntil(lambda: win.folder == siblings / "A", timeout=5000)

    win.folder_panel.folder_activated.emit(siblings / "C")

    qtbot.waitUntil(lambda: win.folder == siblings / "C", timeout=5000)
    assert win.folder_panel.current_folder() == siblings / "C"


def test_ctrl_b_toggles_folder_panel_and_persists(win, qtbot):
    assert win.folder_panel_visible is True
    assert win.folder_panel.isVisible() is True

    win.toggle_folder_panel()

    assert win.folder_panel_visible is False
    assert win.folder_panel.isVisible() is False
    assert win.settings.value("folder_panel_visible", type=bool) is False

    second = MainWindow(thumb_cache=win.thumb_cache, settings=win.settings)
    qtbot.addWidget(second)
    assert second.folder_panel_visible is False
    assert second.folder_panel.isHidden() is True   # applied before the window is ever shown
