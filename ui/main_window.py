from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThreadPool
from PySide6.QtGui import QAction, QImage, QKeyEvent, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QLabel, QMainWindow, QMessageBox, QStackedWidget, QVBoxLayout, QWidget,
)

from core.filters import NO_FILTER, Filter
from core.models import Label, MediaItem, MediaKind
from core.thumbnails import ThumbnailCache, default_cache_dir
from ui.image_cache import ImageCache
from ui.loupe_view import LoupeView
from ui.media_list_model import MediaListModel
from ui.thumb_views import Filmstrip, GridView
from ui.video_view import VideoView
from ui.workers import ImageLoadJob, MetadataWriteJob, ScanJob, ThumbnailJob, WorkerSignals

PRELOAD_OFFSETS = (1, -1, 2, -2)
EMPTY_TEXT = "폴더를 열어주세요 (Ctrl+O)"
NO_ITEMS_TEXT = "이 폴더에 사진이 없습니다"
NO_MATCH_TEXT = "필터에 맞는 항목이 없습니다 (Alt+0: 필터 해제)"
_RATING_KEYS = {Qt.Key.Key_1: 1, Qt.Key.Key_2: 2, Qt.Key.Key_3: 3, Qt.Key.Key_4: 4, Qt.Key.Key_5: 5}
_LABEL_KEYS = {Qt.Key.Key_6: Label.RED, Qt.Key.Key_7: Label.YELLOW, Qt.Key.Key_8: Label.GREEN, Qt.Key.Key_9: Label.BLUE}


class MainWindow(QMainWindow):
    def __init__(self, thumb_cache: ThumbnailCache | None = None, settings: QSettings | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Photo Culling Viewer")
        self.resize(1280, 800)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.suppress_dialogs = False

        self.settings = settings or QSettings("WindowPhotoViewer", "WindowPhotoViewer")
        self.thumb_cache = thumb_cache or ThumbnailCache(default_cache_dir())
        self.image_cache = ImageCache(6)
        self.model = MediaListModel(self)
        self.folder: Path | None = None
        self._loading_folder: Path | None = None
        self.current: int = -1
        # index -> the ImageLoadJob decoding it (jobs are kept so a cancelled queue
        # entry can be told apart from one already running; see _cancel_stale_images)
        self._pending_images: dict[int, ImageLoadJob] = {}
        self._index_by_id: dict[int, int] = {}
        self._closing = False

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

        self.thumb_pool = QThreadPool(self)
        self.thumb_pool.setMaxThreadCount(4)
        self.image_pool = QThreadPool(self)
        self.image_pool.setMaxThreadCount(2)
        self.write_pool = QThreadPool(self)
        self.write_pool.setMaxThreadCount(1)   # serialize writes: never two jobs on one file
        self.scan_pool = QThreadPool(self)
        self.scan_pool.setMaxThreadCount(1)

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

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.header, 0)
        root.addWidget(self.mode_stack, 1)
        self.setCentralWidget(central)
        self.statusBar()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("파일(&F)")
        open_action = QAction("폴더 열기…", self)
        open_action.setShortcut(QKeySequence("Ctrl+O"))
        open_action.triggered.connect(self.choose_folder)
        file_menu.addAction(open_action)

        view_menu = self.menuBar().addMenu("보기(&V)")
        self.auto_advance_action = QAction("별점 후 자동 다음", self)
        self.auto_advance_action.setCheckable(True)
        self.auto_advance_action.setChecked(self.auto_advance)
        self.auto_advance_action.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self.auto_advance_action.toggled.connect(lambda on: setattr(self, "auto_advance", on))
        view_menu.addAction(self.auto_advance_action)

    # ---------------- loading ----------------
    def load_items(self, items: list[MediaItem], folder: Path | None) -> None:
        # Drop work queued for the folder we are leaving: its thumbnails and decodes
        # are worthless now and would make the new folder wait behind them.
        # (clear() drops only queued jobs; a running one finishes and its result is
        # discarded by _index_of, which no longer knows the old items.)
        self.thumb_pool.clear()
        self.image_pool.clear()
        self.folder = folder
        self.video.stop()
        self.image_cache.clear()
        self._pending_images.clear()
        self.model.set_items(items)
        self._index_by_id = {id(it): i for i, it in enumerate(self.model.items())}
        visible = self.model.visible_indices()
        self._set_current(visible[0] if visible else -1)
        self._request_thumbnails(self._priority_order())
        self.statusBar().showMessage(f"{len(items)}개 항목", 3000)

    def open_folder(self, folder: Path) -> None:
        self._loading_folder = folder
        self.statusBar().showMessage(f"불러오는 중: {folder}")
        self.scan_pool.start(ScanJob(folder, self.signals))

    def _on_scan_finished(self, items: list[MediaItem], folder: Path) -> None:
        # scan_pool is a single FIFO thread: opening B while A is still scanning means
        # A's result arrives after we already asked for B. Bind the result to the
        # folder the job actually scanned and drop anything the user moved on from.
        if self._closing or folder != self._loading_folder:
            return
        self.load_items(items, folder)
        self.settings.setValue("last_folder", str(folder))

    def _on_scan_failed(self, message: str, folder: Path) -> None:
        if self._closing or folder != self._loading_folder:
            return
        self._show_error(f"폴더를 열 수 없습니다: {message}")

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
        self._reconcile_current_after_filter()

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
        parts = [
            str(self.folder) if self.folder else "",
            item.stars(),
            f"[{item.label.value}]" if item.label is not Label.NONE else "",
            item.path.name,
            f"{pos}/{len(visible)}",
            item.exif.format() if item.exif else "",
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
        self.write_pool.start(MetadataWriteJob(item, self.signals))

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
            if key in _RATING_KEYS:
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

        if key == Qt.Key.Key_Space and self.content_stack.currentWidget() is self.video and not self.is_grid:
            self.video.toggle_play()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Space):
            self.next_item()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Backspace):
            self.prev_item()
        elif key == Qt.Key.Key_Home:
            self.first_item()
        elif key == Qt.Key.Key_End:
            self.last_item()
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
        # Best-effort quick drain for a responsive close: drop anything not yet
        # started and give running jobs a couple seconds to wrap up. This is not
        # what keeps us safe on timeout — self.signals is unparented (see __init__)
        # specifically so a job that outlives this wait can still emit safely; Qt
        # drops the emit once this window's slots are gone instead of crashing.
        for pool in (self.scan_pool, self.thumb_pool, self.image_pool):
            pool.clear()
            pool.waitForDone(2000)
        # write_pool: never clear() — a queued rating write must not be silently
        # dropped on close. Just give it a couple seconds to finish.
        self.write_pool.waitForDone(2000)
        super().closeEvent(event)
