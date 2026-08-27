from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt

from core import metadata
from core.filters import Filter
from core.scanner import scan
from core.sorting import SortMode
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
def mtime_folder(tmp_path: Path) -> Path:
    """5 images, name order IMG_1..IMG_5, but with distinct mtimes set so that
    mtime order is the reverse of name order and no EXIF DateTimeOriginal is
    present (capture_desc falls back to mtime)."""
    folder = tmp_path / "mtime_folder"
    paths = [make_jpeg(folder / f"IMG_{i}.jpg", size=(80, 60)) for i in range(1, 6)]
    base = 1_700_000_000
    for i, p in enumerate(paths):
        t = base + i * 1000
        os.utime(p, (t, t))
    return folder


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

    assert win.folder_panel.prev_folder() is None   # already first
    qtbot.keyClick(win, Qt.Key.Key_PageUp)   # no-op
    assert win.folder == siblings / "A"

    win.open_folder(siblings / "C")
    qtbot.waitUntil(lambda: win.folder == siblings / "C", timeout=5000)
    assert win.folder_panel.next_folder() is None   # already last
    qtbot.keyClick(win, Qt.Key.Key_PageDown)   # no-op
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


def test_s_cycles_sort_mode_reorders_items_and_preserves_current(win, mtime_folder, qtbot):
    win.load_items(_items(mtime_folder), mtime_folder)
    assert [i.path.name for i in win.model.items()] == \
        ["IMG_1.jpg", "IMG_2.jpg", "IMG_3.jpg", "IMG_4.jpg", "IMG_5.jpg"]

    win._set_current(2)  # IMG_3.jpg
    current_path = win.current_item().path

    qtbot.keyClick(win, Qt.Key.Key_S)

    assert win.sort_mode is SortMode.CAPTURE_DESC
    assert win.settings.value("sort_mode") == "capture_desc"
    assert "sort: 촬영일↓" in win.header.text()
    # mtime is reversed of name order, and there is no EXIF date so capture
    # falls back to mtime -> items now appear newest-mtime first.
    assert [i.path.name for i in win.model.items()] == \
        ["IMG_5.jpg", "IMG_4.jpg", "IMG_3.jpg", "IMG_2.jpg", "IMG_1.jpg"]
    assert win.current_item().path == current_path  # current item preserved across resort

    qtbot.keyClick(win, Qt.Key.Key_S)
    assert win.sort_mode is SortMode.MTIME_DESC
    assert "sort: 수정시각↓" in win.header.text()

    qtbot.keyClick(win, Qt.Key.Key_S)
    assert win.sort_mode is SortMode.NAME_ASC
    assert "sort:" not in win.header.text()
    assert [i.path.name for i in win.model.items()] == \
        ["IMG_1.jpg", "IMG_2.jpg", "IMG_3.jpg", "IMG_4.jpg", "IMG_5.jpg"]
    assert win.current_item().path == current_path


def test_s_does_not_re_request_thumbnails_or_stop_video(win, mtime_folder, qtbot, monkeypatch):
    win.load_items(_items(mtime_folder), mtime_folder)
    qtbot.waitUntil(lambda: win.model.thumbnail(0) is not None, timeout=5000)
    pixmap_before = win.model.thumbnail(0)   # item that started at index 0 (IMG_1.jpg)

    thumb_starts: list[int] = []
    monkeypatch.setattr(win.thumb_pool, "start", lambda job: thumb_starts.append(1))
    stop_calls: list[int] = []
    monkeypatch.setattr(win.video, "stop", lambda: stop_calls.append(1))

    qtbot.keyClick(win, Qt.Key.Key_S)   # -> capture_desc, reverses order

    assert thumb_starts == []            # reorder must not re-request any thumbnail
    assert stop_calls == []              # and must not touch video playback
    # the thumbnail followed its item to the new index (now last, since order reversed)
    new_index = win.model.items().index(next(
        it for it in win.model.items() if it.path.name == "IMG_1.jpg"))
    assert win.model.thumbnail(new_index) is pixmap_before


def test_sort_mode_menu_group_reflects_current_mode(win, mtime_folder, qtbot):
    win.load_items(_items(mtime_folder), mtime_folder)
    actions = win._sort_action_group.actions()
    assert len(actions) == 3

    win.sort_mode = SortMode.MTIME_DESC
    checked = [a for a in actions if a.isChecked()]
    assert len(checked) == 1
    assert win._sort_actions[SortMode.MTIME_DESC].isChecked() is True

    win._sort_actions[SortMode.NAME_ASC].trigger()
    assert win.sort_mode is SortMode.NAME_ASC


def test_new_window_starts_in_saved_sort_mode(win, mtime_folder, qtbot):
    win.load_items(_items(mtime_folder), mtime_folder)
    qtbot.keyClick(win, Qt.Key.Key_S)   # -> capture_desc
    assert win.settings.value("sort_mode") == "capture_desc"

    second = MainWindow(thumb_cache=win.thumb_cache, settings=win.settings)
    qtbot.addWidget(second)
    assert second.sort_mode is SortMode.CAPTURE_DESC


def test_alt_shift_digit_filters_to_exact_rating(win, folder, qtbot):
    win.load_items(_items(folder), folder)          # ratings: 0,3,5,-1,2
    # offscreen delivers Key_3 with Shift held (not Key_Exclam), which is what the
    # event.key() branch of _digit_from_event exercises; the nativeVirtualKey()
    # fallback exists for real keyboards where Shift remaps the key.
    qtbot.keyClick(win, Qt.Key.Key_3, Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier)
    assert win.model.visible_indices() == [1]        # only the exact 3★ item
    assert "filter: ★3" in win.header.text()


def test_alt_shift_digit_is_distinct_from_alt_digit(win, folder, qtbot):
    win.load_items(_items(folder), folder)           # ratings: 0,3,5,-1,2
    qtbot.keyClick(win, Qt.Key.Key_2, Qt.KeyboardModifier.AltModifier)   # min_rating=2 -> [1,2,4]
    assert win.model.visible_indices() == [1, 2, 4]
    assert "★2+" in win.header.text()

    qtbot.keyClick(win, Qt.Key.Key_2, Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier)
    assert win.model.visible_indices() == [4]         # exact 2★ item only
    assert "filter: ★2" in win.header.text() and "★2+" not in win.header.text()


def test_f5_refreshes_when_file_added_and_preserves_current(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    win._set_current(2)  # IMG_3.jpg
    current_path = win.current_item().path
    old_count = len(win.model.items())

    # Isolate from the folder watcher so only the F5 key path can cause the reload
    # (make_jpeg below would otherwise also fire directoryChanged and refresh on its
    # own within the wait below, which would pass even if F5 did nothing).
    win._watch_timer.stop()
    watched = win._watcher.directories()
    if watched:
        win._watcher.removePaths(watched)

    make_jpeg(folder / "IMG_6.jpg", size=(80, 60))
    qtbot.keyClick(win, Qt.Key.Key_F5)

    qtbot.waitUntil(lambda: len(win.model.items()) == old_count + 1, timeout=5000)
    assert win.current_item().path == current_path


def test_f5_reloads_even_when_the_path_set_is_unchanged(win, folder, qtbot, monkeypatch):
    """F5 (and any other explicit re-open of the already-open folder) must always
    re-read from disk, unlike a watcher-triggered refresh -- otherwise externally
    edited ratings/labels (e.g. from Lightroom or exiftool) could never be picked up
    just because no file was added or removed."""
    win.load_items(_items(folder), folder)
    calls: list[int] = []
    real = win.load_items

    def wrapper(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(win, "load_items", wrapper)

    with qtbot.waitSignal(win.signals.scan_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_F5)

    assert calls == [1]
    assert "새로고침" in win.statusBar().currentMessage()


def test_f5_picks_up_an_externally_edited_rating(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    item = win.model.items()[1]   # IMG_2.jpg, rating 3 per the `folder` fixture
    assert item.path.name == "IMG_2.jpg" and item.rating == 3

    # Isolate from the folder watcher: the write below is a real tmp+rename that the
    # OS-level watcher would also notice, and a watcher-triggered refresh racing the
    # F5 one (both landing on the same waitSignal below) could -- if it arrives first
    # -- take the "path set unchanged" skip branch and leave the F5 refresh's result
    # unobserved by this test, even though F5 itself works correctly.
    win._watch_timer.stop()
    watched = win._watcher.directories()
    if watched:
        win._watcher.removePaths(watched)

    metadata.write_rating_label(item.path, item.kind, 5, metadata.Label.NONE)

    with qtbot.waitSignal(win.signals.scan_finished, timeout=5000):
        qtbot.keyClick(win, Qt.Key.Key_F5)

    updated = next(it for it in win.model.items() if it.path == item.path)
    assert updated.rating == 5


def test_watcher_triggered_refresh_skips_reload_when_the_path_set_is_unchanged(win, folder, qtbot, monkeypatch):
    """A watcher-triggered refresh (unlike F5) is expected to fire for our own XMP
    tmp+rename writes; when nothing was actually added or removed, it must not
    reload (that's what keeps a rating write from resetting scroll position)."""
    win.load_items(_items(folder), folder)
    calls: list[int] = []
    real = win.load_items

    def wrapper(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(win, "load_items", wrapper)

    with qtbot.waitSignal(win.signals.scan_finished, timeout=5000):
        win._watcher.directoryChanged.emit(str(folder))   # simulate an OS notification with nothing actually changed

    assert calls == []
    assert "변경 없음" in win.statusBar().currentMessage()


def test_folder_watcher_detects_new_file(win, folder, qtbot):
    win.load_items(_items(folder), folder)
    win._watch_timer.setInterval(100)
    old_count = len(win.model.items())

    make_jpeg(folder / "IMG_7.jpg", size=(80, 60))

    qtbot.waitUntil(lambda: len(win.model.items()) == old_count + 1, timeout=5000)


def test_own_metadata_write_does_not_trigger_a_reload(win, folder, qtbot, monkeypatch):
    win.load_items(_items(folder), folder)
    win._watch_timer.setInterval(50)
    calls: list[int] = []
    monkeypatch.setattr(win, "refresh_folder", lambda: calls.append(1))
    win.next_item()

    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        win.set_rating(3)

    qtbot.wait(400)
    assert calls == []


def test_filter_menu_group_reflects_current_filter(win, folder, qtbot):
    from core.filters import NO_FILTER

    win.load_items(_items(folder), folder)          # ratings: 0,3,5,-1,2
    actions = win._filter_action_group.actions()
    assert len(actions) == 12                        # 전체 + ★1~5 이상 + 정확히 ★1~5 + Reject
    assert [a for a in actions if a.isChecked()] == [win._filter_actions[NO_FILTER]]

    qtbot.keyClick(win, Qt.Key.Key_3, Qt.KeyboardModifier.AltModifier)
    assert [a for a in actions if a.isChecked()] == [win._filter_actions[Filter(min_rating=3)]]

    win._filter_actions[Filter(exact_rating=2)].trigger()
    assert win.model.filter() == Filter(exact_rating=2)
    assert win.model.visible_indices() == [4]
    assert "filter: ★2" in win.header.text()

    win._filter_actions[Filter(rejected_only=True)].trigger()
    assert win.model.visible_indices() == [3]

    win._filter_actions[NO_FILTER].trigger()
    assert win.model.filter() == NO_FILTER
    assert win.model.visible_indices() == [0, 1, 2, 3, 4]
    assert [a for a in actions if a.isChecked()] == [win._filter_actions[NO_FILTER]]
