from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, QFileSystemWatcher, QSettings, Qt, QThreadPool, QTimer
from PySide6.QtGui import QAction, QActionGroup, QIcon, QImage, QKeyEvent, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QLabel, QMainWindow, QMessageBox, QSplitter, QStackedWidget, QVBoxLayout, QWidget,
)

from core.filters import NO_FILTER, Filter
from core.models import Label, MediaItem, MediaKind
from core.rating_cache import RatingCache
from core.sorting import SortMode, sort_items
from core.thumbnails import ThumbnailCache, default_cache_dir
from ui.folder_panel import RATED_NODE_TEXT, FolderPanel
from ui.image_cache import ImageCache
from ui.loupe_view import LoupeView
from ui.media_list_model import MediaListModel
from ui.resources import app_icon_path
from ui.thumb_views import Filmstrip, GridView
from ui.video_view import SEEK_STEP_MS, VideoView
from ui.workers import ImageLoadJob, MetadataWriteJob, RatedCollectJob, ScanJob, ThumbnailJob, WorkerSignals

# User-facing app name. Internal identifiers (QSettings org/app name, the
# %LOCALAPPDATA%\WindowPhotoViewer folder, the exe) stay "WindowPhotoViewer" so
# existing settings/caches keep working and paths stay ASCII.
APP_TITLE = "골라보기"
PRELOAD_OFFSETS = (1, -1, 2, -2)
EMPTY_TEXT = "폴더를 열어주세요 (Ctrl+O)"
NO_ITEMS_TEXT = "이 폴더에 사진이 없습니다"
NO_MATCH_TEXT = "필터에 맞는 항목이 없습니다 (Alt+0: 필터 해제)"
NO_RATED_TEXT = "루트 아래에 별점 있는 사진이 없습니다"
COLLECTING_TEXT = "별점 사진 수집 중…"
NO_ROOT_TEXT = "루트 폴더가 없습니다 — 먼저 폴더를 열어주세요 (Ctrl+O)"
_RATING_KEYS = {Qt.Key.Key_1: 1, Qt.Key.Key_2: 2, Qt.Key.Key_3: 3, Qt.Key.Key_4: 4, Qt.Key.Key_5: 5}
_LABEL_KEYS = {Qt.Key.Key_6: Label.RED, Qt.Key.Key_7: Label.YELLOW, Qt.Key.Key_8: Label.GREEN, Qt.Key.Key_9: Label.BLUE}
# VK_1..VK_5 (Windows virtual-key codes for the top-row digit keys).
_VK_DIGITS = {0x31: 1, 0x32: 2, 0x33: 3, 0x34: 4, 0x35: 5}


def _digit_from_event(event: QKeyEvent) -> int | None:
    """The 1..5 digit an Alt+Shift+<digit> press means.

    With Shift held, Qt normally reports the shifted symbol (Key_Exclam, Key_At, …)
    instead of Key_1..Key_5 on a US layout — but the offscreen platform used in tests
    delivers plain Key_1..Key_5 even with Shift, so event.key() is checked first (this
    is the path the automated tests exercise). nativeVirtualKey() is the fallback for
    real keyboards where Shift does remap the key.
    """
    key = event.key()
    if key in _RATING_KEYS:
        return _RATING_KEYS[key]
    if sys.platform != "win32":
        # _VK_DIGITS is the Windows VK_1..VK_5 virtual-key namespace; nativeVirtualKey()
        # returns a different, platform-specific code space elsewhere (X11 keysyms,
        # macOS virtual keycodes), so the lookup below would be meaningless there. This
        # app targets Windows only (see README), so that's the only platform it needs to
        # cover -- just don't misinterpret an unrelated code as a digit on another OS.
        return None
    return _VK_DIGITS.get(event.nativeVirtualKey())


class MainWindow(QMainWindow):
    def __init__(
        self,
        thumb_cache: ThumbnailCache | None = None,
        settings: QSettings | None = None,
        parent=None,
        rating_cache: RatingCache | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(QIcon(str(app_icon_path())))
        self.resize(1280, 800)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.suppress_dialogs = False

        self.settings = settings or QSettings("WindowPhotoViewer", "WindowPhotoViewer")
        self.thumb_cache = thumb_cache or ThumbnailCache(default_cache_dir())
        # Ratings already read from files, keyed by (path, mtime, size) -- lives next
        # to the thumbnail cache (%LOCALAPPDATA%\WindowPhotoViewer\ratings.json).
        self.rating_cache = rating_cache or RatingCache(self.thumb_cache.cache_dir.parent / "ratings.json")
        self.image_cache = ImageCache(6)
        self.model = MediaListModel(self)
        self.folder: Path | None = None
        self._loading_folder: Path | None = None
        # Root-wide "★ rated photos" collection: the root it was collected for while
        # that view is showing (self.folder is None then), else None.
        self._collection_root: Path | None = None
        self._collect_job: RatedCollectJob | None = None
        self.current: int = -1
        # index -> the ImageLoadJob decoding it (jobs are kept so a cancelled queue
        # entry can be told apart from one already running; see _cancel_stale_images)
        self._pending_images: dict[int, ImageLoadJob] = {}
        self._index_by_id: dict[int, int] = {}
        self._closing = False
        self._restore_path: Path | None = None
        self._refresh_from_watcher = False
        self._watch_unavailable_warned = False
        self._suppress_watch_until: float = 0.0
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_directory_changed)
        self._watch_timer = QTimer(self)
        self._watch_timer.setSingleShot(True)
        self._watch_timer.setInterval(700)
        self._watch_timer.timeout.connect(self._on_watch_timeout)

        # Deliberately unparented: worker-thread jobs (ThumbnailJob/ImageLoadJob/
        # MetadataWriteJob/ScanJob) hold this object and emit to it from other threads.
        # A job may still be running when the window closes; if signals were a child
        # QObject it would be destroyed with the window and a late emit would crash
        # ("Signal source has been deleted"). Left unparented, it stays alive as long
        # as any job references it, and Qt auto-disconnects its queued connections to
        # this window's slots once the window itself is destroyed — so a late emit is
        # simply dropped instead of crashing.
        self.signals = WorkerSignals()
        self.signals.scan_finished.connect(self._on_scan_finished)
        self.signals.scan_failed.connect(self._on_scan_failed)
        self.signals.thumbnail_ready.connect(self._on_thumbnail_ready)
        self.signals.thumbnail_failed.connect(self._on_thumbnail_failed)
        self.signals.image_ready.connect(self._on_image_ready)
        self.signals.image_failed.connect(self._on_image_failed)
        self.signals.write_finished.connect(self._on_write_finished)
        self.signals.collect_progress.connect(self._on_collect_progress)
        self.signals.collect_finished.connect(self._on_collect_finished)

        self.thumb_pool = QThreadPool(self)
        self.thumb_pool.setMaxThreadCount(4)
        self.image_pool = QThreadPool(self)
        self.image_pool.setMaxThreadCount(2)
        self.write_pool = QThreadPool(self)
        self.write_pool.setMaxThreadCount(1)   # serialize writes: never two jobs on one file
        self.scan_pool = QThreadPool(self)
        self.scan_pool.setMaxThreadCount(1)
        # Its own pool, not scan_pool: a root-wide collection can run for a while,
        # and scan_pool is a single FIFO thread -- queued there it would make the
        # next plain folder open wait behind it.
        self.collect_pool = QThreadPool(self)
        self.collect_pool.setMaxThreadCount(1)

        self._build_ui()
        self._build_menu()
        self._set_current(-1)

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        self.header = QLabel()
        self.header.setStyleSheet("padding:4px 8px; background:#181818; color:#dddddd;")

        self.loupe = LoupeView()
        self.video = VideoView()
        self.video.error.connect(self._on_video_error)
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.loupe)
        self.content_stack.addWidget(self.video)

        self.filmstrip = Filmstrip()
        self.filmstrip.setModel(self.model)
        self.filmstrip.row_activated.connect(self._on_row_activated)

        self.loupe_page = QWidget()
        lp = QVBoxLayout(self.loupe_page)
        lp.setContentsMargins(0, 0, 0, 0)
        lp.setSpacing(0)
        lp.addWidget(self.content_stack, 1)
        lp.addWidget(self.filmstrip, 0)

        self.grid = GridView()
        self.grid.setModel(self.model)
        self.grid.row_activated.connect(self._on_row_activated)
        self.grid.row_double_clicked.connect(self._on_grid_double_clicked)

        self.mode_stack = QStackedWidget()
        self.mode_stack.addWidget(self.loupe_page)
        self.mode_stack.addWidget(self.grid)

        self.folder_panel = FolderPanel()
        self.folder_panel.folder_activated.connect(self.open_folder)
        self.folder_panel.rated_collection_activated.connect(self.show_rated_collection)
        # Restore the last root if it still exists on disk; otherwise clear the
        # stale setting rather than pointing the tree at a folder that's gone.
        saved_root = self.root_folder
        self.root_folder = saved_root if (saved_root is not None and saved_root.is_dir()) else None

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.folder_panel)
        self.splitter.addWidget(self.mode_stack)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setChildrenCollapsible(False)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.header, 0)
        root.addWidget(self.splitter, 1)
        self.setCentralWidget(central)
        self.statusBar()

        splitter_state = self.settings.value("splitter_state")
        if isinstance(splitter_state, QByteArray):
            self.splitter.restoreState(splitter_state)

        # Applied last, after folder_panel is already parented into the splitter/
        # layout — calling setVisible() on it before that made it a stray top-level
        # window for one frame (a startup flash) whenever the saved state was visible.
        self.folder_panel.setVisible(self.folder_panel_visible)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("파일(&F)")
        open_action = QAction("폴더 열기…", self)
        open_action.setShortcut(QKeySequence("Ctrl+O"))
        open_action.triggered.connect(self.choose_folder)
        file_menu.addAction(open_action)

        # F5 is handled entirely through this QAction's shortcut (same pattern as
        # Ctrl+O above) rather than an explicit branch in keyPressEvent -- Qt's
        # shortcut dispatch consumes the key event before it would reach
        # keyPressEvent, including under qtbot.keyClick in the offscreen tests.
        refresh_action = QAction("새로고침", self)
        refresh_action.setShortcut(QKeySequence("F5"))
        refresh_action.triggered.connect(self.refresh_folder)
        file_menu.addAction(refresh_action)

        go_up_action = QAction("루트 한 단계 위로", self)
        go_up_action.setShortcut(QKeySequence("Alt+Up"))
        go_up_action.triggered.connect(self.go_to_parent_root)
        file_menu.addAction(go_up_action)

        set_root_action = QAction("현재 폴더를 루트로", self)
        set_root_action.triggered.connect(self.set_root_to_current_folder)
        file_menu.addAction(set_root_action)

        view_menu = self.menuBar().addMenu("보기(&V)")
        self.auto_advance_action = QAction("별점 후 자동 다음", self)
        self.auto_advance_action.setCheckable(True)
        self.auto_advance_action.setChecked(self.auto_advance)
        self.auto_advance_action.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self.auto_advance_action.toggled.connect(lambda on: setattr(self, "auto_advance", on))
        view_menu.addAction(self.auto_advance_action)

        self.folder_panel_action = QAction("폴더 패널", self)
        self.folder_panel_action.setCheckable(True)
        self.folder_panel_action.setChecked(self.folder_panel_visible)
        self.folder_panel_action.setShortcut(QKeySequence("Ctrl+B"))
        self.folder_panel_action.toggled.connect(lambda on: setattr(self, "folder_panel_visible", on))
        view_menu.addAction(self.folder_panel_action)

        rated_action = QAction(f"{RATED_NODE_TEXT} 모아보기", self)
        rated_action.setShortcut(QKeySequence("Ctrl+Shift+R"))
        rated_action.triggered.connect(lambda checked=False: self.show_rated_collection())
        view_menu.addAction(rated_action)

        filter_menu = view_menu.addMenu("필터")
        self._filter_action_group = QActionGroup(self)
        self._filter_action_group.setExclusive(True)
        self._filter_actions: dict[Filter, QAction] = {}

        def add_filter_action(label: str, hint: str, f: Filter) -> None:
            # Key hints are shown in the shortcut column but NOT registered as shortcuts:
            # keyPressEvent already owns Alt+N / Alt+Shift+N / Alt+X / Alt+0, and a
            # registered QKeySequence would race it (and break Alt+Shift+digit on real
            # keyboards where Shift remaps the key).
            action = QAction(f"{label}	{hint}", self)
            action.setCheckable(True)
            action.setChecked(f == self.model.filter())
            action.triggered.connect(lambda checked=False, flt=f: self.set_filter(flt))
            self._filter_action_group.addAction(action)
            filter_menu.addAction(action)
            self._filter_actions[f] = action

        add_filter_action("전체", "Alt+0", NO_FILTER)
        filter_menu.addSeparator()
        for n in range(1, 6):
            add_filter_action(f"★{n} 이상", f"Alt+{n}", Filter(min_rating=n))
        filter_menu.addSeparator()
        for n in range(1, 6):
            add_filter_action(f"정확히 ★{n}", f"Alt+Shift+{n}", Filter(exact_rating=n))
        filter_menu.addSeparator()
        add_filter_action("Reject만", "Alt+X", Filter(rejected_only=True))

        sort_menu = view_menu.addMenu("정렬")
        self._sort_action_group = QActionGroup(self)
        self._sort_action_group.setExclusive(True)
        self._sort_actions: dict[SortMode, QAction] = {}
        current_mode = self.sort_mode
        for mode in (SortMode.NAME_ASC, SortMode.CAPTURE_DESC, SortMode.MTIME_DESC):
            action = QAction(mode.describe(), self)
            action.setCheckable(True)
            action.setChecked(mode is current_mode)
            action.triggered.connect(lambda checked=False, m=mode: setattr(self, "sort_mode", m))
            self._sort_action_group.addAction(action)
            sort_menu.addAction(action)
            self._sort_actions[mode] = action

    # ---------------- loading ----------------
    def load_items(self, items: list[MediaItem], folder: Path | None) -> None:
        # Drop work queued for the folder we are leaving: its thumbnails and decodes
        # are worthless now and would make the new folder wait behind them.
        # (clear() drops only queued jobs; a running one finishes and its result is
        # discarded by _index_of, which no longer knows the old items.)
        self.thumb_pool.clear()
        self.image_pool.clear()
        if folder is not None:
            # A real folder replaces whatever collection was showing (or loading).
            self._cancel_collect()
            self._collection_root = None
            self.folder_panel.set_collection_active(False)
        self.folder = folder
        self.folder_panel.set_folder(folder)
        self.video.stop()
        self.image_cache.clear()
        self._pending_images.clear()
        self.model.set_items(sort_items(items, self.sort_mode))
        watched = self._watcher.directories()
        if watched:
            self._watcher.removePaths(watched)
        if folder is not None:
            if not self._watcher.addPath(str(folder)) and not self._watch_unavailable_warned:
                self._watch_unavailable_warned = True
                self.statusBar().showMessage("폴더 감시를 시작할 수 없습니다", 5000)
        self._index_by_id = {id(it): i for i, it in enumerate(self.model.items())}
        visible = self.model.visible_indices()
        self._set_current(visible[0] if visible else -1)
        self._request_thumbnails(self._priority_order())
        self.statusBar().showMessage(f"{len(items)}개 항목", 3000)

    def open_folder(self, folder: Path, *, refresh: bool = False) -> None:
        """*refresh*: re-read every file's rating instead of trusting the rating
        cache (an explicit F5 -- the way externally edited ratings get noticed)."""
        # Normalize away '..'/relative traversal before anything compares this path
        # against root_folder — otherwise e.g. "root/../other" could be judged
        # "inside root" by a naive comparison despite actually being outside it.
        # Falls back to the path as given if it can't be resolved (never required
        # to exist here — ScanJob below is what actually needs it to be a folder).
        try:
            folder = folder.resolve()
        except OSError:
            pass
        # Root rule: opening a folder inside the current root just moves within the
        # tree; opening one outside it re-roots the tree at that folder. Ctrl+O
        # inside the tree therefore never disturbs the root, but any other jump does.
        # Opening a folder abandons any collection still being gathered (its result
        # would be stale on arrival anyway -- see _on_collect_finished). Done before
        # the root rule below: re-rooting while a collection is active would
        # otherwise kick off a collection for the new root just to cancel it.
        self._cancel_collect()
        self._collection_root = None
        if self.root_folder is None or not self.folder_panel.contains(folder):
            self.root_folder = folder
        self._loading_folder = folder
        self.statusBar().showMessage(f"불러오는 중: {folder}")
        self.scan_pool.start(ScanJob(folder, self.signals, self.rating_cache, refresh=refresh))

    def _on_scan_finished(self, items: list[MediaItem], folder: Path) -> None:
        # scan_pool is a single FIFO thread: opening B while A is still scanning means
        # A's result arrives after we already asked for B. Bind the result to the
        # folder the job actually scanned and drop anything the user moved on from.
        # from_watcher is captured (and the flag cleared) unconditionally on every
        # call, whichever branch below ends up running, so it never leaks into an
        # unrelated later refresh.
        from_watcher = self._refresh_from_watcher
        self._refresh_from_watcher = False
        if self._closing or folder != self._loading_folder:
            self._restore_path = None
            return
        if folder == self.folder:
            # A refresh (F5, a manual re-open of the same folder, or the folder
            # watcher) of the folder already open. Our own XMP writes (tmp+rename)
            # fire the watcher too; when a watcher-triggered refresh finds nothing
            # actually changed on disk, skip the reload so it doesn't disturb scroll
            # position / selection. An explicit refresh (F5 / re-open) always reloads
            # even when the path set is unchanged, so that externally edited ratings
            # or labels (e.g. from Lightroom or exiftool) are picked up.
            new_paths = {it.path for it in items}
            old_paths = {it.path for it in self.model.items()}
            if from_watcher and new_paths == old_paths:
                self._restore_path = None
                self.statusBar().showMessage("변경 없음", 2000)
                self.settings.setValue("last_folder", str(folder))
                return
            self.load_items(items, folder)
            if self._restore_path is not None:
                for i, item in enumerate(self.model.items()):
                    if item.path == self._restore_path:
                        self._set_current(i)
                        break
            self._restore_path = None
            self.statusBar().showMessage(f"{len(items)}개 항목 (새로고침)", 3000)
            self.settings.setValue("last_folder", str(folder))
            return
        self._restore_path = None
        self.load_items(items, folder)
        self.settings.setValue("last_folder", str(folder))

    def refresh_folder(self) -> None:
        # An explicit F5 bypasses the rating cache (ratings edited by another tool
        # that kept the file's mtime would otherwise never show up). A refresh the
        # folder watcher triggered keeps trusting it: whatever changed on disk has a
        # new mtime/size and misses the cache on its own.
        refresh = not self._refresh_from_watcher
        if self._collection_root is not None:
            current = self.current_item()
            self._restore_path = current.path if current is not None else None
            self.show_rated_collection(refresh=refresh)
            return
        if self.folder is None:
            return
        current = self.current_item()
        self._restore_path = current.path if current is not None else None
        self.open_folder(self.folder, refresh=refresh)

    # ---------------- rated collection ----------------
    @property
    def collection_active(self) -> bool:
        """True while the main view shows the root-wide rated collection."""
        return self._collection_root is not None and self._collect_job is None

    def show_rated_collection(self, *, refresh: bool = False) -> None:
        """Gather every rated (★1..5) item under the current root into one list.
        Runs in the background; the view switches when the result arrives.
        *refresh*: re-read every file instead of trusting the rating cache."""
        root = self.root_folder
        if root is None:
            self.statusBar().showMessage(NO_ROOT_TEXT, 5000)
            return
        self._cancel_collect()
        self._loading_folder = None   # a folder scan still in flight is now stale
        self._collection_root = root
        self.statusBar().showMessage(f"별점 사진 수집 중… ({root})")
        job = RatedCollectJob(root, self.signals, self.rating_cache, refresh=refresh)
        self._collect_job = job
        self.collect_pool.start(job)

    def _cancel_collect(self) -> None:
        job = self._collect_job
        if job is None:
            return
        job.cancel()
        self.collect_pool.clear()
        self._collect_job = None

    def _on_collect_progress(self, job: RatedCollectJob, progress) -> None:
        if self._closing or job is not self._collect_job:
            return
        self.statusBar().showMessage(
            f"별점 사진 수집 중… {progress.folders}폴더 · {progress.files}장 확인 · ★{progress.rated}"
        )

    def _on_collect_finished(self, job: RatedCollectJob, items: list[MediaItem] | None) -> None:
        # Identity, not root, decides staleness: a cancelled job's None must never be
        # mistaken for the failure of the job that replaced it for the same root.
        if self._closing or job is not self._collect_job:
            self._restore_path = None
            return
        self._collect_job = None
        root = job.root
        if items is None:   # not a cancellation (that clears _collect_job first): a failure
            self._restore_path = None
            self.statusBar().showMessage("별점 사진을 수집할 수 없습니다", 8000)
            return
        self.load_items(items, None)
        self.folder_panel.set_collection_active(True)
        self.folder_panel.set_rated_count(len(items))
        if self._restore_path is not None:
            for i, item in enumerate(self.model.items()):
                if item.path == self._restore_path:
                    self._set_current(i)
                    break
        self._restore_path = None
        self.statusBar().showMessage(f"★ {len(items)}장 (루트: {root})", 5000)

    def _on_scan_failed(self, message: str, folder: Path) -> None:
        self._refresh_from_watcher = False
        self._restore_path = None
        if self._closing or folder != self._loading_folder:
            return
        self._show_error(f"폴더를 열 수 없습니다: {message}")

    def _on_directory_changed(self, _path: str) -> None:
        if self._closing:
            return
        self._watch_timer.start()   # (re)start the debounce window; start() restarts if already running

    def _on_watch_timeout(self) -> None:
        if self._closing:
            return
        if time.monotonic() < self._suppress_watch_until:
            # Our own XMP write is still within its suppression window. Re-arm so a
            # genuine external change that lands during this window is not lost.
            self._watch_timer.start()
            return
        self._refresh_from_watcher = True
        self.refresh_folder()

    def _show_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 10000)
        if not self.suppress_dialogs:
            QMessageBox.warning(self, "오류", message)

    # ---------------- settings ----------------
    @property
    def auto_advance(self) -> bool:
        return bool(self.settings.value("auto_advance", False, type=bool))

    @auto_advance.setter
    def auto_advance(self, on: bool) -> None:
        self.settings.setValue("auto_advance", bool(on))
        if hasattr(self, "auto_advance_action") and self.auto_advance_action.isChecked() != bool(on):
            self.auto_advance_action.setChecked(bool(on))

    @property
    def folder_panel_visible(self) -> bool:
        return bool(self.settings.value("folder_panel_visible", True, type=bool))

    @folder_panel_visible.setter
    def folder_panel_visible(self, on: bool) -> None:
        self.settings.setValue("folder_panel_visible", bool(on))
        if hasattr(self, "folder_panel"):
            self.folder_panel.setVisible(bool(on))
        if hasattr(self, "folder_panel_action") and self.folder_panel_action.isChecked() != bool(on):
            self.folder_panel_action.setChecked(bool(on))

    @property
    def root_folder(self) -> Path | None:
        value = self.settings.value("root_folder", "", type=str)
        return Path(value) if value else None

    @root_folder.setter
    def root_folder(self, folder: Path | None) -> None:
        self.settings.setValue("root_folder", str(folder) if folder is not None else "")
        self.folder_panel.set_root(folder)
        if self._collection_root is not None and folder is not None and folder != self._collection_root:
            # The collection is "everything rated under the root": a new root means
            # a new collection (e.g. Alt+Up widens it). set_root() rebuilt the tree,
            # so the node highlight/count are re-applied when the result lands.
            self.show_rated_collection()

    @property
    def sort_mode(self) -> SortMode:
        return SortMode.from_value(self.settings.value("sort_mode", SortMode.NAME_ASC.value, type=str))

    @sort_mode.setter
    def sort_mode(self, mode: SortMode) -> None:
        if mode is self.sort_mode:
            return
        self.settings.setValue("sort_mode", mode.value)
        if hasattr(self, "_sort_actions"):
            action = self._sort_actions.get(mode)
            if action is not None and not action.isChecked():
                action.setChecked(True)
        self._resort()

    def cycle_sort(self) -> None:
        self.sort_mode = self.sort_mode.next()

    def _resort(self) -> None:
        # Reorders the SAME MediaItem objects already in the model rather than a
        # full load_items() reload: a reload would re-request every thumbnail (N
        # ThumbnailJobs per `S` press) and could race a still-in-flight job for the
        # same item against a new one on the same on-disk cache file. Identity-based
        # reorder keeps thumbnail/failed/requested state, so nothing is re-requested.
        current_item = self.current_item()
        ordered = sort_items(self.model.items(), self.sort_mode)
        self.model.reorder(ordered)
        self._index_by_id = {id(it): i for i, it in enumerate(self.model.items())}
        # image_cache / _pending_images are keyed by positional index, which the
        # reorder just changed -- drop them rather than risk showing the wrong
        # item's decoded image at a given index. The item itself didn't change, so
        # the loupe/video already on screen stays correct without a reload.
        self.image_cache.clear()
        self._pending_images.clear()
        if current_item is not None:
            idx = self._index_of(current_item)
            if idx >= 0:
                self._set_current(idx, show=False)

    def last_folder(self) -> Path | None:
        value = self.settings.value("last_folder", "", type=str)
        return Path(value) if value else None

    def choose_folder(self) -> None:
        if self.suppress_dialogs:
            return
        start = str(self.last_folder() or Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "사진 폴더 선택", start)
        if chosen:
            self.open_folder(Path(chosen))

    # ---------------- lookup helpers ----------------
    def _index_of(self, item: MediaItem) -> int:
        return self._index_by_id.get(id(item), -1)

    def current_item(self) -> MediaItem | None:
        items = self.model.items()
        return items[self.current] if 0 <= self.current < len(items) else None

    def _active_view(self):
        return self.grid if self.is_grid else self.filmstrip

    # ---------------- modes ----------------
    @property
    def is_grid(self) -> bool:
        return self.mode_stack.currentWidget() is self.grid

    def show_grid(self) -> None:
        self.video.stop()
        self.mode_stack.setCurrentWidget(self.grid)
        row = self.model.row_for_item_index(self.current)
        if row >= 0:
            self.grid.set_current_row(row)
        self._request_thumbnails(self._priority_order())

    def show_loupe(self) -> None:
        was_grid = self.is_grid
        self.mode_stack.setCurrentWidget(self.loupe_page)
        if was_grid:
            self._show_current()

    def _on_grid_double_clicked(self, row: int) -> None:
        if 0 <= row < self.model.rowCount():
            self._set_current(self.model.item_index_at_row(row), show=False)
        self.show_loupe()

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ---------------- filter ----------------
    def set_filter(self, f: Filter) -> None:
        self.model.set_filter(f)
        self._sync_filter_actions(f)
        self._reconcile_current_after_filter()

    def _sync_filter_actions(self, f: Filter) -> None:
        actions = getattr(self, "_filter_actions", None)
        if not actions:
            return
        action = actions.get(f)
        if action is not None:
            if not action.isChecked():
                action.setChecked(True)
        else:  # a filter with no menu entry: uncheck everything
            checked = self._filter_action_group.checkedAction()
            if checked is not None:
                self._filter_action_group.setExclusive(False)
                checked.setChecked(False)
                self._filter_action_group.setExclusive(True)

    def clear_filter(self) -> None:
        self.set_filter(NO_FILTER)

    def _reconcile_current_after_filter(self) -> None:
        """Keep current if still visible; otherwise move to the next visible item (or the last, or none)."""
        visible = self.model.visible_indices()
        if self.current in visible:
            self._set_current(self.current)   # re-sync views / header
            return
        if not visible:
            self._set_current(-1)
            return
        later = [i for i in visible if i > self.current]
        self._set_current(later[0] if later else visible[-1])

    # ---------------- thumbnails ----------------
    def _priority_order(self) -> list[int]:
        on_screen = [self.model.item_index_at_row(r) for r in self._active_view().visible_rows()]
        seen = set(on_screen)
        visible_rest = [i for i in self.model.visible_indices() if i not in seen]
        seen.update(visible_rest)
        hidden = [i for i in range(len(self.model.items())) if i not in seen]
        return on_screen + visible_rest + hidden

    def _request_thumbnails(self, indices: list[int]) -> None:
        items = self.model.items()
        for idx in indices:
            if self.model.has_thumbnail_request(idx):
                continue
            self.model.mark_requested(idx)
            self.thumb_pool.start(ThumbnailJob(items[idx], self.thumb_cache, self.signals))

    def _on_thumbnail_ready(self, item: MediaItem, path: str) -> None:
        if self._closing:
            return
        idx = self._index_of(item)
        if idx < 0:
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.model.set_thumbnail_failed(idx)
        else:
            self.model.set_thumbnail(idx, pixmap)

    def _on_thumbnail_failed(self, item: MediaItem, _message: str) -> None:
        if self._closing:
            return
        idx = self._index_of(item)
        if idx >= 0:
            self.model.set_thumbnail_failed(idx)

    # ---------------- current item / display ----------------
    def _set_current(self, idx: int, *, show: bool = True) -> None:
        self.current = idx
        self._cancel_stale_images()
        row = self.model.row_for_item_index(idx) if idx >= 0 else -1
        if row >= 0:
            self.filmstrip.set_current_row(row)
            self.grid.set_current_row(row)
        if show:
            self._show_current()
        self._preload_neighbors()
        self._update_header()

    def _empty_text(self) -> str:
        """What to show when there is no current item: no folder, an empty folder, or a filter that matches nothing."""
        if self.model.items():
            return NO_MATCH_TEXT if self.model.filter().is_active else EMPTY_TEXT
        if self._collection_root is not None:
            return COLLECTING_TEXT if self._collect_job is not None else NO_RATED_TEXT
        return NO_ITEMS_TEXT if self.folder is not None else EMPTY_TEXT

    def _show_current(self) -> None:
        self.video.stop()
        item = self.current_item()
        if item is None:
            self.content_stack.setCurrentWidget(self.loupe)
            self.loupe.set_placeholder(self._empty_text())
            return
        if item.kind is MediaKind.VIDEO:
            self.video.load(item.path)
            self.content_stack.setCurrentWidget(self.video)
            if not self.is_grid:   # nothing visible in grid mode — don't start playback
                self.video.play()
            return
        self.content_stack.setCurrentWidget(self.loupe)
        image = self.image_cache.get(self.current)
        if image is not None:
            self.loupe.set_image(image)
        else:
            self.loupe.set_placeholder("불러오는 중…")
            self._request_image(self.current)

    def _request_image(self, idx: int) -> None:
        if idx in self._pending_images or idx in self.image_cache:
            return
        item = self.model.items()[idx]
        if item.kind is not MediaKind.IMAGE:
            return
        job = ImageLoadJob(item, self.signals)
        self._pending_images[idx] = job
        self.image_pool.start(job)

    def _cancel_stale_images(self) -> None:
        """Throw away decodes the user has navigated past.

        Every ImageLoadJob result is a full-size QImage (~100 MB for 24 MP), so holding
        down an arrow key must not leave a queue of them decoding long after the user
        stopped. QThreadPool.clear() drops only *queued* runnables — and a dropped one
        never emits, so its index has to leave _pending_images too, or _request_image's
        dedupe would make it un-requestable forever. Jobs that already started are left
        pending: their result is still on its way, and re-requesting them would just
        decode the same file twice. Whatever is still wanted is re-queued right after
        by _show_current / _preload_neighbors, so the surviving queue is exactly the
        current item and its ±2 neighbours.
        """
        self.image_pool.clear()
        for idx, job in list(self._pending_images.items()):
            if not job.started:
                del self._pending_images[idx]

    def _preload_neighbors(self) -> None:
        visible = self.model.visible_indices()
        if self.current not in visible:
            return
        pos = visible.index(self.current)
        for offset in PRELOAD_OFFSETS:
            p = pos + offset
            if 0 <= p < len(visible):
                self._request_image(visible[p])

    def _on_image_ready(self, item: MediaItem, image: QImage) -> None:
        if self._closing:
            return
        idx = self._index_of(item)
        if idx < 0:
            return
        self._pending_images.pop(idx, None)
        self.image_cache.put(idx, image)
        if idx == self.current:
            self.loupe.set_image(image)

    def _on_image_failed(self, item: MediaItem, message: str) -> None:
        if self._closing:
            return
        idx = self._index_of(item)
        if idx < 0:
            return
        self._pending_images.pop(idx, None)
        if not item.path.exists():
            self._remove_item(idx)
            return
        if idx == self.current:
            self.loupe.set_placeholder(f"표시할 수 없음\n{item.path.name}\n{message}")

    def _remove_item(self, idx: int) -> None:
        items = list(self.model.items())
        removed = items.pop(idx)
        was_current = self.current
        keep_filter = self.model.filter()
        self.image_cache.clear()
        self._pending_images.clear()
        self.model.set_items(items)
        self.model.set_filter(keep_filter)
        self._index_by_id = {id(it): i for i, it in enumerate(items)}
        visible = self.model.visible_indices()
        if not visible:
            self._set_current(-1)
        else:
            # The removal left-shifts every index after `idx`; compensate so we land
            # on the item the user was actually looking at (or the one that slid into
            # its slot), not the item one further along.
            target = was_current - 1 if idx < was_current else was_current
            target = max(0, min(target, len(items) - 1))
            candidates = [i for i in visible if i >= target]
            self._set_current(candidates[0] if candidates else visible[-1])
        self._request_thumbnails(self._priority_order())
        self.statusBar().showMessage(f"파일이 사라져 목록에서 제외: {removed.path.name}", 8000)

    def _update_header(self) -> None:
        item = self.current_item()
        if item is None:
            self.header.setText(self._empty_text())
            return
        visible = self.model.visible_indices()
        pos = visible.index(self.current) + 1 if self.current in visible else 0
        # Only once the collection is what's on screen: while it is still being
        # gathered from a folder view, the header keeps describing that folder.
        in_collection = self._collection_root is not None and self.folder is None
        parts = [
            f"{RATED_NODE_TEXT} ({self._collection_root})" if in_collection else (str(self.folder) if self.folder else ""),
            item.stars(),
            f"[{item.label.value}]" if item.label is not Label.NONE else "",
            # Items from many folders share names in a collection: show the parent too.
            f"{item.path.parent.name}/{item.path.name}" if in_collection else item.path.name,
            f"{pos}/{len(visible)}",
            item.exif.format() if item.exif else "",
            f"sort: {self.sort_mode.describe()}" if self.sort_mode is not SortMode.NAME_ASC else "",
            f"filter: {self.model.filter().describe()}" if self.model.filter().is_active else "",
            "⚠ 기록 실패" if item.write_error else "",
        ]
        self.header.setText("   ".join(p for p in parts if p))

    # ---------------- navigation ----------------
    def _step(self, delta: int) -> None:
        visible = self.model.visible_indices()
        if not visible:
            return
        if self.current not in visible:
            self._set_current(visible[0])
            return
        pos = max(0, min(len(visible) - 1, visible.index(self.current) + delta))
        if visible[pos] != self.current:
            self._set_current(visible[pos])

    def next_item(self) -> None:
        self._step(1)

    def prev_item(self) -> None:
        self._step(-1)

    def first_item(self) -> None:
        visible = self.model.visible_indices()
        if visible:
            self._set_current(visible[0])

    def last_item(self) -> None:
        visible = self.model.visible_indices()
        if visible:
            self._set_current(visible[-1])

    def next_folder(self) -> None:
        p = self.folder_panel.next_folder()
        if p:
            self.open_folder(p)

    def prev_folder(self) -> None:
        p = self.folder_panel.prev_folder()
        if p:
            self.open_folder(p)

    def toggle_folder_panel(self) -> None:
        self.folder_panel_visible = not self.folder_panel_visible

    def go_to_parent_root(self) -> None:
        """Alt+Up: grow the tree upward by one level. The currently open folder is
        unaffected and stays highlighted (it is still under the new, higher root)."""
        root = self.root_folder
        if root is None or root.parent == root:
            return   # already at the top — root.parent == root is the only way the
            # new root (root.parent) could equal the current one, so this one check
            # also covers the "new root equals current root" case.
        self.root_folder = root.parent
        self.folder_panel.set_folder(self.folder)

    def set_root_to_current_folder(self) -> None:
        """'현재 폴더를 루트로': re-root the tree at whatever folder is open now."""
        if self.folder is None or self.folder == self.root_folder:
            return   # already the root — skip the rebuild
        self.root_folder = self.folder
        self.folder_panel.set_folder(self.folder)

    def _on_row_activated(self, row: int) -> None:
        if 0 <= row < self.model.rowCount():
            self._set_current(self.model.item_index_at_row(row))

    # ---------------- rating / label ----------------
    # Spec §7: "재시도는 같은 키 재입력". When the last write of a value failed, the
    # in-memory value is kept, so the plain toggle rule would read the same key press
    # as "toggle this off" and write 0 instead of retrying. Re-dispatch the same value
    # instead whenever the key asks for what the item already holds and that value is
    # the one that failed to reach the file.
    def set_rating(self, rating: int) -> None:
        item = self.current_item()
        if item is None:
            return
        if item.write_error and item.rating == rating:
            self._apply_change(item, rating=rating)
            return
        new = 0 if (rating != 0 and item.rating == rating) else rating
        self._apply_change(item, rating=new)

    def toggle_reject(self) -> None:
        item = self.current_item()
        if item is None:
            return
        if item.write_error and item.is_rejected:
            self._apply_change(item, rating=-1)
            return
        self._apply_change(item, rating=0 if item.is_rejected else -1)

    def set_label(self, label: Label) -> None:
        item = self.current_item()
        if item is None:
            return
        if item.write_error and item.label is label:
            self._apply_change(item, label=label)
            return
        self._apply_change(item, label=Label.NONE if item.label is label else label)

    def _apply_change(self, item: MediaItem, *, rating: int | None = None, label: Label | None = None) -> None:
        idx = self._index_of(item)
        if rating is not None:
            item.rating = rating
        if label is not None:
            item.label = label
        item.write_error = None
        self.model.item_changed(idx)
        self._suppress_watch_until = time.monotonic() + 2.0
        self.write_pool.start(MetadataWriteJob(item, self.signals, self.rating_cache))

        if self.model.filter().is_active and not self.model.filter().matches(item):
            self.model.refresh_filter()
            self._reconcile_current_after_filter()
        elif rating is not None and rating > 0 and self.auto_advance:
            self.next_item()
        # Unconditional: next_item() is a no-op on the last item, and the header still
        # has to pick up the rating that was just applied.
        self._update_header()

    def _on_write_finished(self, item: MediaItem, error: str) -> None:
        if self._closing:
            return
        idx = self._index_of(item)
        if idx < 0:
            return
        item.write_error = error or None
        self.model.item_changed(idx)
        self._suppress_watch_until = time.monotonic() + 2.0
        if error:
            self.statusBar().setStyleSheet("color:#ff4040;")
            self.statusBar().showMessage(f"기록 실패: {error}", 15000)
        else:
            self.statusBar().setStyleSheet("")
        if idx == self.current:
            self._update_header()

    def _on_video_error(self, message: str) -> None:
        """Spec §7: an unplayable video explains itself instead of showing a black
        rectangle. The item stays current, so rating and navigation keep working."""
        if self._closing:
            return
        # QMediaPlayer reports errors asynchronously, often after the user has already
        # moved on. Only speak up while the failing video is the one still on screen —
        # otherwise a stale codec error would overwrite a newer, more useful message.
        item = self.current_item()
        if item is None or item.kind is not MediaKind.VIDEO or self.video.source_path() != item.path:
            return
        self.statusBar().setStyleSheet("color:#ff4040;")
        self.statusBar().showMessage(f"재생할 수 없는 영상: {message}", 15000)

    # ---------------- keys ----------------
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        mods = event.modifiers()
        if mods & Qt.KeyboardModifier.ControlModifier:
            super().keyPressEvent(event)   # Ctrl+O / Ctrl+Shift+A are QAction shortcuts
            return
        if mods & Qt.KeyboardModifier.AltModifier:
            digit = _digit_from_event(event)
            if mods & Qt.KeyboardModifier.ShiftModifier and digit is not None:
                self.set_filter(Filter(exact_rating=digit))
            elif key in _RATING_KEYS:
                self.set_filter(Filter(min_rating=_RATING_KEYS[key]))
            elif key == Qt.Key.Key_X:
                self.set_filter(Filter(rejected_only=True))
            elif key == Qt.Key.Key_0:
                self.clear_filter()
            else:
                super().keyPressEvent(event)
                return
            event.accept()
            return

        video_showing = self.content_stack.currentWidget() is self.video and not self.is_grid
        if key == Qt.Key.Key_Space and video_showing:
            self.video.toggle_play()
        elif key in (Qt.Key.Key_Comma, Qt.Key.Key_Period) and video_showing:
            self.video.seek_by(-SEEK_STEP_MS if key == Qt.Key.Key_Comma else SEEK_STEP_MS)
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Space):
            self.next_item()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Backspace):
            self.prev_item()
        elif key == Qt.Key.Key_Home:
            self.first_item()
        elif key == Qt.Key.Key_End:
            self.last_item()
        elif key == Qt.Key.Key_PageDown:
            self.next_folder()
        elif key == Qt.Key.Key_PageUp:
            self.prev_folder()
        elif key in _RATING_KEYS:
            self.set_rating(_RATING_KEYS[key])
        elif key == Qt.Key.Key_0:
            self.set_rating(0)
        elif key == Qt.Key.Key_X:
            self.toggle_reject()
        elif key in _LABEL_KEYS:
            self.set_label(_LABEL_KEYS[key])
        elif key == Qt.Key.Key_Z:
            self.loupe.toggle_zoom()
        elif key == Qt.Key.Key_S:
            self.cycle_sort()
        elif key == Qt.Key.Key_G:
            self.show_grid()
        elif key == Qt.Key.Key_E:
            self.show_loupe()
        elif key in (Qt.Key.Key_F, Qt.Key.Key_F11):
            self.toggle_fullscreen()
        elif key == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    # ---------------- lifecycle ----------------
    def closeEvent(self, event) -> None:  # noqa: N802
        # Stop reacting to results before draining: a ScanJob/ThumbnailJob/etc. that
        # finishes while we're waiting below must not re-queue more work on a window
        # that is on its way out (see the _closing guards on the _on_* handlers).
        self._closing = True
        self.settings.setValue("splitter_state", self.splitter.saveState())
        # FolderPanel owns its own count-job pool; drain it before the panel is torn
        # down with the rest of the window, or QThreadPool's destructor would block
        # close for as long as whatever count batch is still running (unbounded on a
        # slow or network drive) instead of the bounded waits below.
        self.folder_panel.shutdown()
        self._cancel_collect()
        self._watch_timer.stop()
        watched = self._watcher.directories()
        if watched:
            self._watcher.removePaths(watched)
        # Best-effort quick drain for a responsive close: drop anything not yet
        # started and give running jobs a couple seconds to wrap up. This is not
        # what keeps us safe on timeout — self.signals is unparented (see __init__)
        # specifically so a job that outlives this wait can still emit safely; Qt
        # drops the emit once this window's slots are gone instead of crashing.
        for pool in (self.scan_pool, self.collect_pool, self.thumb_pool, self.image_pool):
            pool.clear()
            pool.waitForDone(2000)
        # write_pool: never clear() — a queued rating write must not be silently
        # dropped on close. Just give it a couple seconds to finish.
        self.write_pool.waitForDone(2000)
        # After the pools: a write that just finished has stored its new entry.
        self.rating_cache.save()
        super().closeEvent(event)
