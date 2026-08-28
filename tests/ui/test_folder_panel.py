from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt

import ui.folder_panel as folder_panel_module
from tests.helpers import make_jpeg
from ui.folder_panel import PLACEHOLDER_TEXT, RATED_NODE_TEXT, FolderPanel, list_child_folders


def _make_folder(base: Path, name: str, n_images: int = 1, n_videos: int = 0) -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n_images):
        make_jpeg(d / f"img_{i}.jpg")
    for i in range(n_videos):
        (d / f"clip_{i}.mp4").write_bytes(b"")
    return d


# ---------------- list_child_folders ----------------

def test_list_child_folders_order_and_hidden_exclusion(tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "B2")
    _make_folder(root, "B10")
    _make_folder(root, "A")
    hidden = root / ".hidden"
    hidden.mkdir()
    (root / "afile.txt").write_text("not a directory")

    result = list_child_folders(root)

    assert [p.name for p in result] == ["A", "B2", "B10"]


def test_list_child_folders_permission_error_returns_empty(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()

    def boom(self):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "iterdir", boom)

    assert list_child_folders(root) == []


def test_list_child_folders_survives_a_missing_folder(tmp_path):
    missing = tmp_path / "does-not-exist"
    assert list_child_folders(missing) == []


# ---------------- FolderPanel: root / lazy loading ----------------

def test_set_root_shows_root_expanded(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "A")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    assert panel.root() == root
    # top-level 0 is the virtual "rated photos" node, the real root sits below it
    assert panel._tree.topLevelItemCount() == 2
    root_item = panel._tree.topLevelItem(1)
    assert root_item.isExpanded() is True
    assert root_item.text(0) == "root"
    assert root_item.toolTip(0) == str(root)


def test_children_lazily_loaded_on_expand(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A")
    _make_folder(a, "A1")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    a_item = panel._path_to_item[a]
    assert a_item.isExpanded() is False
    assert a_item.childCount() == 1
    placeholder = a_item.child(0)
    assert placeholder.data(0, Qt.ItemDataRole.UserRole) is None

    a_item.setExpanded(True)

    assert a_item.childCount() == 1
    real_child = a_item.child(0)
    assert real_child.data(0, Qt.ItemDataRole.UserRole) == a / "A1"
    assert real_child.text(0) == "A1"


def test_hidden_dirs_excluded_from_tree(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "A")
    hidden = root / ".hidden"
    hidden.mkdir()

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    root_item = panel._tree.topLevelItem(1)
    names = [root_item.child(i).text(0) for i in range(root_item.childCount())]
    assert names == ["A"]


def test_set_root_none_clears(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "A")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    panel.set_root(None)

    assert panel.root() is None
    assert panel._tree.topLevelItemCount() == 0
    assert panel.current_folder() is None
    assert PLACEHOLDER_TEXT in panel._header.text()


# ---------------- FolderPanel: set_folder / highlight ----------------

def test_set_folder_deep_child_expands_ancestors_and_highlights(qtbot, tmp_path):
    root = tmp_path / "root"
    b = _make_folder(root, "B")
    b1 = _make_folder(b, "B1")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    panel.set_folder(b1)

    assert panel.current_folder() == b1
    b_item = panel._path_to_item[b]
    assert b_item.isExpanded() is True
    b1_item = panel._path_to_item[b1]
    assert b1_item.font(0).bold() is True
    assert panel._tree.currentItem() is b1_item


def test_set_folder_outside_root_is_a_noop(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "A")
    other = tmp_path / "other"
    other.mkdir()

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)
    panel.set_folder(root / "A")

    panel.set_folder(other)   # outside root: no-op

    assert panel.current_folder() == root / "A"


def test_set_folder_none_clears_highlight(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "A")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)
    panel.set_folder(root / "A")

    panel.set_folder(None)

    assert panel.current_folder() is None


# ---------------- FolderPanel: visible_folders / navigation ----------------

def test_visible_folders_dfs_order(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "A")
    b = _make_folder(root, "B")
    _make_folder(b, "B1")
    _make_folder(b, "B2")
    _make_folder(root, "C")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    assert panel.visible_folders() == [root, root / "A", b, root / "C"]

    panel._path_to_item[b].setExpanded(True)

    assert panel.visible_folders() == [
        root, root / "A", b, b / "B1", b / "B2", root / "C",
    ]


def test_next_prev_folder_boundaries(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "A")
    _make_folder(root, "B")
    _make_folder(root, "C")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    panel.set_folder(root)
    assert panel.prev_folder() is None
    assert panel.next_folder() == root / "A"

    panel.set_folder(root / "A")
    assert panel.prev_folder() == root
    assert panel.next_folder() == root / "B"

    panel.set_folder(root / "C")
    assert panel.next_folder() is None


# ---------------- FolderPanel: click ----------------

def test_click_emits_folder_activated(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)
    panel.set_folder(root)

    item = panel._path_to_item[a]
    with qtbot.waitSignal(panel.folder_activated, timeout=1000) as blocker:
        panel._tree.itemClicked.emit(item, 0)
    assert blocker.args == [a]


def test_click_on_current_folder_is_a_noop(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)
    panel.set_folder(a)

    item = panel._path_to_item[a]
    with qtbot.assertNotEmitted(panel.folder_activated):
        panel._tree.itemClicked.emit(item, 0)


# ---------------- FolderPanel: contains ----------------

def test_contains(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A")
    other = tmp_path / "other"
    other.mkdir()

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    assert panel.contains(root) is True
    assert panel.contains(a) is True
    assert panel.contains(other) is False


def test_contains_with_no_root_is_always_false(qtbot, tmp_path):
    panel = FolderPanel()
    qtbot.addWidget(panel)

    assert panel.contains(tmp_path) is False


# ---------------- FolderPanel: counts ----------------

def test_counts_arrive_and_are_applied_to_the_item(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A", n_images=2, n_videos=1)

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    item = panel._path_to_item[a]
    qtbot.waitUntil(lambda: "장" in item.text(0), timeout=5000)
    assert item.text(0) == "A   (2장 · 1영상)"


def test_counts_arrive_without_videos_omit_the_video_count(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A", n_images=3)

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    item = panel._path_to_item[a]
    qtbot.waitUntil(lambda: "장" in item.text(0), timeout=5000)
    assert item.text(0) == "A   (3장)"


def test_stale_counts_are_ignored(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A", n_images=1)

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    item = panel._path_to_item[a]
    panel._path_to_item.pop(a)   # simulate the path no longer being tracked in the tree

    panel._on_counts_ready({a: (99, 99)})

    assert item.text(0) == "A"


# ---------------- shutdown ----------------

def test_shutdown_drains_the_count_pool_promptly(qtbot, tmp_path, monkeypatch):
    root = tmp_path / "root"
    _make_folder(root, "A")
    _make_folder(root, "B")
    _make_folder(root, "C")

    real_counts = folder_panel_module._counts

    def slow_counts(path):
        time.sleep(0.3)
        return real_counts(path)

    monkeypatch.setattr(folder_panel_module, "_counts", slow_counts)

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)   # queues one FolderCountJob covering A, B, C

    start = time.monotonic()
    panel.shutdown()
    elapsed = time.monotonic() - start

    assert elapsed < 2.5
    assert panel._count_pool.activeThreadCount() == 0


# ---------------- hidden current folder (dead-end regression) ----------------

def test_set_folder_hidden_current_folder_is_listed_and_navigable(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A")
    hidden = root / ".hidden"
    hidden.mkdir()
    make_jpeg(hidden / "x.jpg")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    panel.set_folder(hidden)

    assert panel.current_folder() == hidden
    assert hidden in panel.visible_folders()
    assert panel.next_folder() == a   # natural order: '.hidden' sorts before 'A'

    panel.set_folder(a)
    assert panel.prev_folder() == hidden


def test_set_folder_hidden_folder_under_an_already_loaded_ancestor(qtbot, tmp_path):
    """The ancestor gets expanded (and loaded, without the hidden child) BEFORE the
    hidden folder ever becomes current -- set_folder must still be able to reach it
    by forcing a reload, not just by keeping it at first-load time."""
    root = tmp_path / "root"
    a = _make_folder(root, "A")
    hidden = a / ".hidden"
    hidden.mkdir()
    make_jpeg(hidden / "x.jpg")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)
    panel._path_to_item[a].setExpanded(True)   # loads A's children while hidden is excluded

    panel.set_folder(hidden)

    assert panel.current_folder() == hidden
    assert hidden in panel.visible_folders()


def test_offset_folder_falls_back_to_nearest_visible_ancestor_when_collapsed(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "A")
    b = _make_folder(root, "B")
    _make_folder(b, "B1")
    _make_folder(b, "B2")
    _make_folder(root, "C")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)
    panel.set_folder(b / "B1")   # expands B along the way

    panel._path_to_item[b].setExpanded(False)   # collapse B: B1 drops out of visible order

    assert panel.next_folder() == root / "C"
    assert panel.prev_folder() == root / "A"


# ---------------- minors ----------------

def test_child_lookup_is_case_insensitive(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "Alpha")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    panel.set_folder(root / "alpha")   # different case than the real "Alpha" on disk

    assert panel.current_folder() == root / "alpha"
    assert panel._tree.currentItem() is not None


def test_contains_normalizes_dot_dot_traversal(qtbot, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    other = tmp_path / "other"
    other.mkdir()

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    assert panel.contains(root / ".." / "other") is False


def test_tree_never_takes_focus(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A")

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.show()
    qtbot.waitExposed(panel)
    panel.set_root(root)

    item = panel._path_to_item[a]
    rect = panel._tree.visualItemRect(item)
    qtbot.mouseClick(panel._tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())

    assert panel._tree.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert not panel._tree.hasFocus()


def test_header_reelides_on_resize(qtbot, tmp_path):
    root = tmp_path / "a-fairly-long-root-folder-name-for-eliding"
    root.mkdir()

    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.resize(400, 300)
    panel.show()
    qtbot.waitExposed(panel)
    panel.set_root(root)
    wide_text = panel._header.text()

    panel.resize(200, 300)
    qtbot.waitUntil(lambda: panel._header.text() != wide_text, timeout=2000)


# ---------------- FolderPanel: virtual "rated" node ----------------

def test_rated_node_is_first_top_level_item_when_root_set(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "A")
    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)

    node = panel._tree.topLevelItem(0)
    assert node.text(0) == RATED_NODE_TEXT
    assert node.childCount() == 0
    assert panel._tree.topLevelItem(1).text(0) == "root"


def test_rated_node_absent_without_root(qtbot, tmp_path):
    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(None)
    assert panel._tree.topLevelItemCount() == 0


def test_rated_node_click_emits_rated_collection_activated(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "A")
    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)
    panel.set_folder(root)

    node = panel._tree.topLevelItem(0)
    with qtbot.waitSignal(panel.rated_collection_activated, timeout=1000):
        with qtbot.assertNotEmitted(panel.folder_activated):
            panel._tree.itemClicked.emit(node, 0)


def test_rated_node_click_reemits_even_when_already_active(qtbot, tmp_path):
    """Re-clicking the node is how the user re-collects (a refresh)."""
    root = tmp_path / "root"
    _make_folder(root, "A")
    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)
    panel.set_collection_active(True)

    node = panel._tree.topLevelItem(0)
    with qtbot.waitSignal(panel.rated_collection_activated, timeout=1000):
        panel._tree.itemClicked.emit(node, 0)


def test_rated_node_not_in_visible_folders(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A")
    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)
    assert panel.visible_folders() == [root, a]


def test_set_collection_active_highlights_node_and_clears_folder(qtbot, tmp_path):
    root = tmp_path / "root"
    a = _make_folder(root, "A")
    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)
    panel.set_folder(a)

    panel.set_collection_active(True)
    node = panel._tree.topLevelItem(0)
    assert node.font(0).bold()
    assert panel._tree.currentItem() is node
    assert not panel._path_to_item[a].font(0).bold()
    assert panel.current_folder() is None

    panel.set_collection_active(False)
    assert not node.font(0).bold()


def test_set_rated_count_updates_node_text(qtbot, tmp_path):
    root = tmp_path / "root"
    _make_folder(root, "A")
    panel = FolderPanel()
    qtbot.addWidget(panel)
    panel.set_root(root)
    node = panel._tree.topLevelItem(0)

    panel.set_rated_count(12)
    assert node.text(0) == f"{RATED_NODE_TEXT}   (12장)"
    panel.set_rated_count(None)
    assert node.text(0) == RATED_NODE_TEXT
