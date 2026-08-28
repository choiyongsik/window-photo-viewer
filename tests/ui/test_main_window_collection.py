"""MainWindow: root-wide "★ rated photos" collection view."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence

from core.metadata import write_rating_label
from core.models import Label, MediaKind
from core.thumbnails import ThumbnailCache
from tests.helpers import make_jpeg
from ui import workers
from ui.folder_panel import RATED_NODE_TEXT
from ui.main_window import COLLECTING_TEXT, NO_RATED_TEXT, MainWindow


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """root/ IMG_1(★3)  IMG_2  A/ IMG_1(★1)  A/A1/ IMG_9(★5)  B/ IMG_4  (dup names across folders on purpose)"""
    root = tmp_path / "root"
    files = {
        "r1": make_jpeg(root / "IMG_1.jpg"),
        "r2": make_jpeg(root / "IMG_2.jpg"),
        "a1": make_jpeg(root / "A" / "IMG_1.jpg"),
        "a9": make_jpeg(root / "A" / "A1" / "IMG_9.jpg"),
        "b4": make_jpeg(root / "B" / "IMG_4.jpg"),
    }
    write_rating_label(files["r1"], MediaKind.IMAGE, 3, Label.NONE)
    write_rating_label(files["a1"], MediaKind.IMAGE, 1, Label.RED)
    write_rating_label(files["a9"], MediaKind.IMAGE, 5, Label.NONE)
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


def _open(win, qtbot, folder: Path) -> None:
    win.open_folder(folder)
    qtbot.waitUntil(lambda: win.folder == folder, timeout=5000)


def _collect(win, qtbot) -> None:
    with qtbot.waitSignal(win.signals.collect_finished, timeout=5000):
        win.show_rated_collection()
    qtbot.waitUntil(lambda: win.collection_active, timeout=2000)


def _rel(win, root: Path) -> list[str]:
    return sorted(it.path.relative_to(root).as_posix() for it in win.model.items())


def test_show_rated_collection_loads_rated_items_under_root(win, tree, qtbot):
    _open(win, qtbot, tree)
    _collect(win, qtbot)

    assert _rel(win, tree) == ["A/A1/IMG_9.jpg", "A/IMG_1.jpg", "IMG_1.jpg"]
    assert all(it.rating >= 1 for it in win.model.items())
    assert win.folder is None
    assert win.collection_active
    assert win.current == 0


def test_collection_header_names_the_collection_and_the_parent_folder(win, tree, qtbot):
    _open(win, qtbot, tree)
    _collect(win, qtbot)

    header = win.header.text()
    assert RATED_NODE_TEXT in header
    assert str(tree) in header
    item = win.current_item()
    assert f"{item.path.parent.name}/{item.path.name}" in header


def test_collection_highlights_the_panel_node_and_shows_the_count(win, tree, qtbot):
    _open(win, qtbot, tree / "A")
    win.root_folder = tree
    _collect(win, qtbot)

    node = win.folder_panel._tree.topLevelItem(0)
    assert node.font(0).bold()
    assert node.text(0) == f"{RATED_NODE_TEXT}   (3장)"
    assert win.folder_panel.current_folder() is None


def test_show_rated_collection_without_root_is_a_noop(win, qtbot):
    assert win.root_folder is None
    win.show_rated_collection()
    assert not win.collection_active
    assert "루트" in win.statusBar().currentMessage()


def test_opening_a_folder_leaves_collection_mode(win, tree, qtbot):
    _open(win, qtbot, tree)
    _collect(win, qtbot)

    _open(win, qtbot, tree / "B")

    assert not win.collection_active
    assert win.folder == tree / "B"
    assert win.folder_panel.current_folder() == tree / "B"
    assert not win.folder_panel._tree.topLevelItem(0).font(0).bold()
    assert [it.path.name for it in win.model.items()] == ["IMG_4.jpg"]


def test_refresh_in_collection_mode_recollects_and_keeps_current(win, tree, qtbot):
    _open(win, qtbot, tree)
    _collect(win, qtbot)
    win.next_item()
    kept = win.current_item().path
    write_rating_label(tree / "B" / "IMG_4.jpg", MediaKind.IMAGE, 2, Label.NONE)

    with qtbot.waitSignal(win.signals.collect_finished, timeout=5000):
        win.refresh_folder()
    qtbot.waitUntil(lambda: len(win.model.items()) == 4, timeout=2000)

    assert "B/IMG_4.jpg" in _rel(win, tree)
    assert win.current_item().path == kept
    assert win.collection_active


def test_collection_with_no_rated_items_shows_dedicated_message(win, tmp_path, qtbot):
    root = tmp_path / "plain"
    make_jpeg(root / "x.jpg")
    _open(win, qtbot, root)
    _collect(win, qtbot)

    assert win.model.items() == []
    assert win.header.text() == NO_RATED_TEXT


def test_growing_the_root_while_collecting_recollects_for_the_new_root(win, tree, qtbot):
    _open(win, qtbot, tree / "A")
    _collect(win, qtbot)
    assert _rel(win, tree / "A") == ["A1/IMG_9.jpg", "IMG_1.jpg"]

    with qtbot.waitSignal(win.signals.collect_finished, timeout=5000):
        win.go_to_parent_root()
    qtbot.waitUntil(lambda: len(win.model.items()) == 3, timeout=2000)

    assert win.root_folder == tree
    assert win.collection_active
    assert _rel(win, tree) == ["A/A1/IMG_9.jpg", "A/IMG_1.jpg", "IMG_1.jpg"]


def test_stale_collection_result_is_dropped(win, tree, qtbot):
    """A result for a root the user has since left must not replace the view."""
    _open(win, qtbot, tree)
    win.show_rated_collection()
    # Leave collection mode before the result lands.
    _open(win, qtbot, tree / "B")
    qtbot.wait(300)   # give any in-flight collect_finished a chance to arrive

    assert not win.collection_active
    assert win.folder == tree / "B"
    assert [it.path.name for it in win.model.items()] == ["IMG_4.jpg"]


def test_menu_action_with_shortcut_exists(win):
    actions = [a for a in win.findChildren(type(win.auto_advance_action)) if RATED_NODE_TEXT in a.text()]
    assert actions, "menu action for the rated collection is missing"
    assert actions[0].shortcut() == QKeySequence("Ctrl+Shift+R")


def test_close_during_collection_returns_promptly(win, tree, qtbot, monkeypatch):
    def slow_collect(root, *, is_cancelled, on_progress=None):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if is_cancelled():
                return None
            time.sleep(0.02)
        return []

    monkeypatch.setattr(workers, "collect_rated", slow_collect)
    _open(win, qtbot, tree)
    win.show_rated_collection()
    qtbot.wait(100)

    t0 = time.monotonic()
    win.close()
    assert time.monotonic() - t0 < 1.5


def test_header_while_collecting_from_empty_state_says_collecting(win, tree, qtbot, monkeypatch):
    def slow_collect(root, *, is_cancelled, on_progress=None):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if is_cancelled():
                return None
            time.sleep(0.02)
        return []

    monkeypatch.setattr(workers, "collect_rated", slow_collect)
    win.root_folder = tree           # a root but no folder open (fresh start)
    win.show_rated_collection()
    win._update_header()

    assert win.header.text() == COLLECTING_TEXT
    win.close()


def test_header_keeps_folder_view_until_collection_result_arrives(win, tree, qtbot, monkeypatch):
    def slow_collect(root, *, is_cancelled, on_progress=None):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if is_cancelled():
                return None
            time.sleep(0.02)
        return []

    monkeypatch.setattr(workers, "collect_rated", slow_collect)
    _open(win, qtbot, tree)
    win.show_rated_collection()
    win.next_item()   # navigating while the collection is still being gathered

    header = win.header.text()
    assert str(tree) in header
    assert RATED_NODE_TEXT not in header
    assert "IMG_2.jpg" in header and "root/IMG_2.jpg" not in header
    win.close()


# ---------- rating cache wiring ----------

def test_window_defaults_the_rating_cache_next_to_the_thumbnail_cache(win, tmp_path):
    # the fixture's thumbnail cache is tmp_path/"cache" -> ratings.json sits beside it
    assert win.rating_cache.file == tmp_path / "ratings.json"


def test_opening_a_folder_fills_the_rating_cache(win, tree, qtbot):
    _open(win, qtbot, tree)
    assert len(win.rating_cache) == 2
    item = win.model.items()[0]
    assert win.rating_cache.lookup(item.path, item.mtime, item.size) == (item.rating, item.label)


def test_setting_a_rating_updates_the_cache_for_the_rewritten_file(win, tree, qtbot):
    _open(win, qtbot, tree)
    item = win.current_item()
    with qtbot.waitSignal(win.signals.write_finished, timeout=5000):
        win.set_rating(4)
    st = item.path.stat()
    assert win.rating_cache.lookup(item.path, st.st_mtime, st.st_size) == (4, Label.NONE)


def test_f5_in_a_folder_rereads_files_instead_of_trusting_the_cache(win, tree, qtbot):
    _open(win, qtbot, tree)
    lied = win.model.items()[1]                          # IMG_2.jpg, unrated on disk
    win.rating_cache.store(lied.path, lied.mtime, lied.size, 5, Label.NONE)

    with qtbot.waitSignal(win.signals.scan_finished, timeout=5000):
        win.open_folder(tree)                            # plain re-open: cache trusted
    qtbot.waitUntil(lambda: win.model.items()[1].rating == 5, timeout=2000)

    with qtbot.waitSignal(win.signals.scan_finished, timeout=5000):
        win.refresh_folder()                             # F5: files re-read
    qtbot.waitUntil(lambda: win.model.items()[1].rating == 0, timeout=2000)


def test_f5_in_collection_mode_rereads_files(win, tree, qtbot):
    _open(win, qtbot, tree)
    _collect(win, qtbot)
    lied = tree / "IMG_2.jpg"
    st = lied.stat()
    win.rating_cache.store(lied, st.st_mtime, st.st_size, 5, Label.NONE)

    with qtbot.waitSignal(win.signals.collect_finished, timeout=5000):
        win.show_rated_collection()                      # re-click: cache trusted -> 4 items
    qtbot.waitUntil(lambda: len(win.model.items()) == 4, timeout=2000)

    with qtbot.waitSignal(win.signals.collect_finished, timeout=5000):
        win.refresh_folder()                             # F5: re-read -> back to 3
    qtbot.waitUntil(lambda: len(win.model.items()) == 3, timeout=2000)


def test_close_persists_the_rating_cache(win, tree, qtbot, tmp_path):
    from core.rating_cache import RatingCache

    _open(win, qtbot, tree)
    item = win.model.items()[0]
    win.close()

    reloaded = RatingCache(win.rating_cache.file)
    assert reloaded.lookup(item.path, item.mtime, item.size) == (item.rating, item.label)
