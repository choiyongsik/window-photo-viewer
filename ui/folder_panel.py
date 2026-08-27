from __future__ import annotations

import os
import stat
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtWidgets import QLabel, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from core.models import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from core.scanner import natural_key

PANEL_MIN_WIDTH = 180
PLACEHOLDER_TEXT = "폴더 없음"
# Windows marks files/dirs hidden with an attribute rather than a leading dot; 0
# elsewhere, which makes the mask test below a no-op on other platforms (mirrors
# core/scanner.py's own check).
_HIDDEN_MASK = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0)

# Item data roles on column 0. _ROLE_PATH holds the Path for a real folder node, or
# None for the lazy-load placeholder child every unexpanded directory node carries.
# _ROLE_LOADED marks whether a directory node's real children have been fetched yet
# (guards against re-listing the folder every time it is re-expanded).
_ROLE_PATH = Qt.ItemDataRole.UserRole
_ROLE_LOADED = Qt.ItemDataRole.UserRole + 1


def _is_hidden(entry: Path) -> bool:
    if entry.name.startswith("."):
        return True
    if not _HIDDEN_MASK:
        return False
    try:
        st = entry.stat()
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & _HIDDEN_MASK)


def _counts(folder: Path) -> tuple[int, int]:
    """Non-recursive image/video counts for one folder, ignoring XMP. A folder that
    cannot be scanned at all (permission denied, or it vanished between being listed
    and being counted) still gets listed, just with (0, 0) — any OSError here is a
    filesystem race or access problem, not a reason to blow up folder navigation."""
    images = videos = 0
    try:
        with os.scandir(folder) as it:
            for entry in it:
                try:
                    if not entry.is_file():
                        continue
                except OSError:
                    continue
                ext = os.path.splitext(entry.name)[1].lower()
                if ext in IMAGE_EXTENSIONS:
                    images += 1
                elif ext in VIDEO_EXTENSIONS:
                    videos += 1
    except OSError:
        return (0, 0)
    return images, videos


def list_child_folders(folder: Path, keep: frozenset[Path] = frozenset()) -> list[Path]:
    """The direct subdirectories of *folder*, natural-sorted.

    Hidden directories (leading '.' or the Windows hidden attribute) are excluded,
    except any that appear in *keep* — used so a hidden folder that is the current
    folder (or an ancestor of it) stays reachable and navigable instead of becoming
    a dead end once it's no longer the folder the user just opened.

    Ordinary filesystem races (folder vanished, permission denied, an unreadable
    directory entry) never raise here — they just yield an empty list, same as an
    empty folder. Losing a subtree should never break the rest of the tree.
    """
    try:
        entries = list(folder.iterdir())
    except OSError:
        return []

    dirs: list[Path] = []
    for entry in entries:
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        if _is_hidden(entry) and entry not in keep:
            continue
        dirs.append(entry)
    dirs.sort(key=lambda p: natural_key(p.name))
    return dirs


def _safe_resolve(path: Path) -> Path:
    """path.resolve(), falling back to path itself if resolution fails (e.g. a
    component vanished mid-resolve) — used to normalize away '..' traversal before
    comparing paths, never to require the path to exist."""
    try:
        return path.resolve()
    except OSError:
        return path


def _display_name(path: Path) -> str:
    # A drive root (e.g. "C:\") has an empty .name; fall back to the full path so
    # the node is never shown blank.
    return path.name or str(path)


class _CountSignals(QObject):
    counts_ready = Signal(object)  # dict[Path, tuple[int, int]]


class FolderCountJob(QRunnable):
    """Counts images/videos for each of *paths*, off the UI thread, and reports the
    whole batch at once via *signals*."""

    def __init__(self, paths: list[Path], signals: _CountSignals):
        super().__init__()
        self.paths = paths
        self.signals = signals

    def run(self) -> None:
        # _counts() already tolerates OSError internally, but this runs on a pooled
        # worker thread with no other safety net — an unexpected exception on one
        # path (a weird filename, a permission quirk _counts didn't anticipate)
        # must not silently kill the whole batch (and the thread) before it emits.
        counts: dict[Path, tuple[int, int]] = {}
        for path in self.paths:
            try:
                counts[path] = _counts(path)
            except Exception:
                counts[path] = (0, 0)
        self.signals.counts_ready.emit(counts)


class FolderPanel(QWidget):
    """Left-hand root-based folder tree: a lazily-loaded QTreeWidget rooted at
    whatever folder MainWindow considers the current root, so PgUp/PgDn (wired by
    MainWindow) can hop between folders in tree order without a file dialog.
    Never takes keyboard focus — MainWindow owns all shortcuts.
    """

    folder_activated = Signal(object)   # Path — ignored when it is the current folder

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(PANEL_MIN_WIDTH)
        self._root: Path | None = None
        self._root_item: QTreeWidgetItem | None = None
        self._current: Path | None = None
        self._highlighted_item: QTreeWidgetItem | None = None
        self._path_to_item: dict[Path, QTreeWidgetItem] = {}

        self._header = QLabel()
        self._header.setStyleSheet("padding:4px 8px; background:#181818; color:#dddddd;")
        self._header.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._header.setWordWrap(False)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(1)
        self._tree.setHeaderHidden(True)
        self._tree.setUniformRowHeights(True)
        self._tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tree.setStyleSheet(
            "QTreeWidget { background:#181818; color:#dddddd; border:none; }"
            "QTreeWidget::item:selected { background:#333333; }"
        )
        self._tree.itemExpanded.connect(self._on_item_expanded)
        self._tree.itemClicked.connect(self._on_item_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._header, 0)
        layout.addWidget(self._tree, 1)

        # Background folder counting: 2 worker threads, one shared signal holder.
        # Deliberately unparented (mirrors ui.workers.WorkerSignals): a FolderCountJob
        # may still be running after this panel is destroyed (e.g. test teardown), and
        # each job keeps its own reference to _count_signals, so it stays alive as long
        # as any job needs it. Qt auto-disconnects the queued connection to this
        # panel's slot once the panel itself is destroyed, so a late emit is dropped
        # instead of crashing.
        self._count_pool = QThreadPool(self)
        self._count_pool.setMaxThreadCount(2)
        self._count_signals = _CountSignals()
        self._count_signals.counts_ready.connect(self._on_counts_ready)

        self.set_root(None)

    def shutdown(self) -> None:
        """Stop background counting and wait (briefly) for any in-flight batch.

        Call this before the panel is destroyed (e.g. from MainWindow.closeEvent).
        QThreadPool's destructor blocks until every running job finishes; without
        draining it first, destroying the panel while a count job is still counting
        a large or slow (e.g. network-share) folder would stall window close for as
        long as that job takes — unbounded. clear() drops anything still queued;
        waitForDone(2000) gives whatever is already running a couple seconds to
        finish, same budget as MainWindow's other worker pools.
        """
        self._count_pool.clear()
        self._count_pool.waitForDone(2000)

    # ---------------- root ----------------
    def set_root(self, root: Path | None) -> None:
        self._count_pool.clear()
        self._tree.clear()
        self._path_to_item.clear()
        self._root = root
        self._root_item = None
        self._current = None
        self._highlighted_item = None

        if root is None:
            self._header.setText(PLACEHOLDER_TEXT)
            self._header.setToolTip("")
            return

        self._header.setText(self._elided(str(root)))
        self._header.setToolTip(str(root))
        root_item = self._make_node(root)
        self._tree.addTopLevelItem(root_item)
        self._root_item = root_item
        self._path_to_item[root] = root_item
        root_item.setExpanded(True)   # loads root's children synchronously

    def root(self) -> Path | None:
        return self._root

    def contains(self, folder: Path) -> bool:
        """folder == root or folder is under root, after resolving both to their
        canonical form (so e.g. 'root/../other' is not mistaken for being inside
        root). resolve() can touch the filesystem (to follow symlinks); it never
        requires the path to exist, and falls back to the path as given if it
        can't be resolved."""
        if self._root is None:
            return False
        try:
            _safe_resolve(folder).relative_to(_safe_resolve(self._root))
        except ValueError:
            return False
        return True

    # ---------------- nodes / lazy loading ----------------
    def _make_node(self, path: Path) -> QTreeWidgetItem:
        item = QTreeWidgetItem([_display_name(path)])
        item.setData(0, _ROLE_PATH, path)
        item.setData(0, _ROLE_LOADED, False)
        item.setToolTip(0, str(path))
        item.addChild(self._make_placeholder())
        return item

    @staticmethod
    def _make_placeholder() -> QTreeWidgetItem:
        placeholder = QTreeWidgetItem([""])
        placeholder.setData(0, _ROLE_PATH, None)
        return placeholder

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        if item.data(0, _ROLE_LOADED):
            return   # already loaded — a placeholder never coexists with real children
        path = item.data(0, _ROLE_PATH)
        keep = self._keep_hidden_for(path) if path is not None else frozenset()
        self._load_children(item, keep)

    def _load_children(self, item: QTreeWidgetItem, keep: frozenset[Path] = frozenset()) -> None:
        """(Re)loads *item*'s real children, replacing whatever is there now (a
        placeholder on first load, or stale children on a forced reload). *keep*
        lets a hidden directory stay listed — see list_child_folders."""
        item.setData(0, _ROLE_LOADED, True)
        item.takeChildren()
        path = item.data(0, _ROLE_PATH)
        if path is None:
            return

        new_paths: list[Path] = []
        for child_path in list_child_folders(path, keep):
            child_item = self._make_node(child_path)
            item.addChild(child_item)
            self._path_to_item[child_path] = child_item
            new_paths.append(child_path)

        if new_paths:
            self._count_pool.start(FolderCountJob(new_paths, self._count_signals))

    def _keep_hidden_for(self, path: Path) -> frozenset[Path]:
        """The one immediate child of *path* that must stay listed even if hidden,
        because it is (or leads to) the current folder — keeps a hidden current
        folder, or a folder under a hidden ancestor, reachable and navigable
        instead of a dead end."""
        current = self._current
        if current is None:
            return frozenset()
        try:
            rel_parts = current.relative_to(path).parts
        except ValueError:
            return frozenset()
        if not rel_parts:
            return frozenset()   # current == path itself, not one of its children
        return frozenset({path / rel_parts[0]})

    def _on_counts_ready(self, counts: dict[Path, tuple[int, int]]) -> None:
        for path, (images, videos) in counts.items():
            item = self._path_to_item.get(path)
            if item is None:
                continue   # stale: path no longer in the tree (root changed since)
            label = f"{_display_name(path)}   ({images}장"
            if videos:
                label += f" · {videos}영상"
            label += ")"
            item.setText(0, label)

    # ---------------- current folder / highlight ----------------
    def set_folder(self, folder: Path | None) -> None:
        if folder is not None and not self.contains(folder):
            return   # outside root: MainWindow decides root changes, not us
        self._current = folder
        self._clear_highlight()
        if folder is None:
            return
        item = self._ensure_item(folder)
        if item is not None:
            self._highlight(item)

    def current_folder(self) -> Path | None:
        return self._current

    def _ensure_item(self, folder: Path) -> QTreeWidgetItem | None:
        """The tree item for *folder*, expanding (and lazily loading) every ancestor
        along the way. folder must already satisfy self.contains(folder)."""
        if self._root_item is None:
            return None
        if folder == self._root:
            return self._root_item
        item = self._root_item
        for name in folder.relative_to(self._root).parts:
            if not item.isExpanded():
                item.setExpanded(True)   # synchronously loads item's real children
            child = self._child_by_name(item, name)
            if child is None:
                # Not found — possibly hidden and not kept when this node was last
                # loaded (e.g. it only became the current folder afterwards, while
                # the ancestor was already expanded for something else). Force a
                # reload that keeps it, then look again.
                path = item.data(0, _ROLE_PATH)
                target = self._existing_dir(path, name) if path is not None else None
                if target is not None:
                    self._load_children(item, frozenset({target}))
                    child = self._child_by_name(item, name)
            if child is None:
                return None
            item = child
        return item

    @staticmethod
    def _existing_dir(parent: Path, name: str) -> Path | None:
        candidate = parent / name
        try:
            return candidate if candidate.is_dir() else None
        except OSError:
            return None

    @staticmethod
    def _child_by_name(parent: QTreeWidgetItem, name: str) -> QTreeWidgetItem | None:
        # casefold(), not a plain ==: Windows filesystems (and thus this tree, and
        # the paths MainWindow hands to set_folder) are case-insensitive.
        target = name.casefold()
        for i in range(parent.childCount()):
            child = parent.child(i)
            path = child.data(0, _ROLE_PATH)
            if path is not None and path.name.casefold() == target:
                return child
        return None

    def _clear_highlight(self) -> None:
        if self._highlighted_item is not None:
            font = self._highlighted_item.font(0)
            if font.bold():
                font.setBold(False)
                self._highlighted_item.setFont(0, font)
            self._highlighted_item = None
        self._tree.setCurrentItem(None)

    def _highlight(self, item: QTreeWidgetItem) -> None:
        font = item.font(0)
        if not font.bold():
            font.setBold(True)
            item.setFont(0, font)
        self._tree.setCurrentItem(item)
        self._tree.scrollToItem(item)
        self._highlighted_item = item

    # ---------------- visible order / navigation ----------------
    def visible_folders(self) -> list[Path]:
        """Folders in the tree's current visible (expanded) order, root first."""
        result: list[Path] = []
        if self._root_item is not None:
            self._collect_visible(self._root_item, result)
        return result

    def _collect_visible(self, item: QTreeWidgetItem, result: list[Path]) -> None:
        path = item.data(0, _ROLE_PATH)
        if path is None:
            return   # placeholder
        result.append(path)
        if item.isExpanded():
            for i in range(item.childCount()):
                self._collect_visible(item.child(i), result)

    def next_folder(self) -> Path | None:
        return self._offset_folder(1)

    def prev_folder(self) -> Path | None:
        return self._offset_folder(-1)

    def _offset_folder(self, delta: int) -> Path | None:
        if self._current is None:
            return None
        folders = self.visible_folders()
        anchor = self._current
        if anchor not in folders:
            # The current folder isn't visible right now — most commonly because an
            # ancestor got collapsed after it became current. Anchor on the nearest
            # visible ancestor instead of giving up, so PgDn/PgUp still move.
            anchor = self._nearest_visible_ancestor(anchor, set(folders))
            if anchor is None:
                return None
        idx = folders.index(anchor) + delta
        return folders[idx] if 0 <= idx < len(folders) else None

    def _nearest_visible_ancestor(self, folder: Path, visible: set[Path]) -> Path | None:
        if self._root is None:
            return None
        current = folder
        while current != self._root:
            current = current.parent
            if current in visible:
                return current
        return None

    # ---------------- clicking ----------------
    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        path = item.data(0, _ROLE_PATH)
        if path is None or path == self._current:
            return
        self.folder_activated.emit(path)

    # ---------------- misc ----------------
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # The splitter can resize this panel at any time; re-elide against the
        # header's actual current width rather than the width it had when the root
        # was first set, or the header text would go stale (too short or with
        # room to spare) after a drag.
        if self._root is not None:
            self._header.setText(self._elided(str(self._root)))

    def _elided(self, text: str) -> str:
        metrics = self._header.fontMetrics()
        # Before the panel has ever been laid out/shown, width() can be 0 or an
        # arbitrary default; floor it at the panel's guaranteed minimum width so
        # eliding still makes sense in that case.
        width = max(self._header.width(), PANEL_MIN_WIDTH) - 16
        return metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, width)
