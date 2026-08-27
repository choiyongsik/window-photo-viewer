from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QStyle, QVBoxLayout, QWidget

SEEK_STEP_MS = 5000
_CONTROLS_STYLE = (
    "QWidget#videoControls { background:#181818; }"
    "QPushButton { color:#dddddd; background:transparent; border:none; font-size:14px; }"
    "QPushButton:hover { color:#ffffff; }"
    "QLabel { color:#dddddd; }"
)


def format_time(ms: int) -> str:
    """0 → '00:00', 3661000 → '1:01:01'. Negative values clamp to zero."""
    total = max(0, int(ms)) // 1000
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


class _SeekSlider(QSlider):
    """Slider that jumps to the clicked position (instead of paging) and scrubs while dragged.

    Emits the standard sliderPressed / sliderMoved / sliderReleased signals so the owner can
    treat clicks and drags uniformly.
    """

    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def _value_at(self, x: int) -> int:
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), x, self.width())

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        value = self._value_at(int(event.position().x()))
        self.setSliderDown(True)
        self.sliderPressed.emit()
        self.setValue(value)
        self.sliderMoved.emit(value)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self.isSliderDown():
            value = self._value_at(int(event.position().x()))
            self.setValue(value)
            self.sliderMoved.emit(value)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self.setSliderDown(False)
            self.sliderReleased.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class VideoView(QWidget):
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source: Path | None = None
        self._duration = 0
        self._scrubbing = False

        self.player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self.player.setAudioOutput(self._audio)
        self.player.setLoops(1)  # explicit: no auto-repeat, end-of-media handling owns the rewind
        self._widget = QVideoWidget(self)
        # QVideoWidget would otherwise swallow the mouse press before it reaches us;
        # make it transparent to mouse events so VideoView.mousePressEvent gets the click.
        self._widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.player.setVideoOutput(self._widget)

        self.player.errorOccurred.connect(self._on_error)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(self._on_playback_state)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._widget, 1)
        layout.addWidget(self._build_controls(), 0)
        self.setStyleSheet("background:#141414;")

    # ---------------- controls ----------------
    def _build_controls(self) -> QWidget:
        bar = QWidget(self)
        bar.setObjectName("videoControls")
        bar.setFixedHeight(32)
        bar.setStyleSheet(_CONTROLS_STYLE)

        self.play_button = QPushButton("▶", bar)
        self.play_button.setFixedWidth(32)
        self.play_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.play_button.clicked.connect(self.toggle_play)

        self.slider = _SeekSlider(bar)
        self.slider.setRange(0, 0)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        self.slider.sliderReleased.connect(self._on_slider_released)

        self.time_label = QLabel("00:00 / 00:00", bar)
        self.time_label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.time_label.setMinimumWidth(96)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.mute_button = QPushButton("🔊", bar)
        self.mute_button.setFixedWidth(32)
        self.mute_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mute_button.clicked.connect(self.toggle_mute)

        row = QHBoxLayout(bar)
        row.setContentsMargins(6, 0, 6, 0)
        row.setSpacing(6)
        row.addWidget(self.play_button)
        row.addWidget(self.slider, 1)
        row.addWidget(self.time_label)
        row.addWidget(self.mute_button)
        return bar

    def _update_time_label(self, position: int) -> None:
        self.time_label.setText(f"{format_time(position)} / {format_time(self._duration)}")

    def _on_position(self, position: int) -> None:
        if self._scrubbing:
            return  # the user owns the slider until release
        self.slider.setValue(position)
        self._update_time_label(position)

    def _on_duration(self, duration: int) -> None:
        self._duration = max(0, duration)
        self.slider.setRange(0, self._duration)
        self._update_time_label(self.slider.value())

    def _on_slider_pressed(self) -> None:
        self._scrubbing = True

    def _on_slider_moved(self, value: int) -> None:
        self.player.setPosition(value)
        self._update_time_label(value)

    def _on_slider_released(self) -> None:
        self._scrubbing = False

    def _on_playback_state(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("❚❚" if playing else "▶")

    def toggle_mute(self) -> None:
        muted = not self._audio.isMuted()
        self._audio.setMuted(muted)
        self.mute_button.setText("🔇" if muted else "🔊")

    def seek_by(self, delta_ms: int) -> None:
        target = self.player.position() + delta_ms
        if self._duration > 0:
            target = min(target, self._duration)
        self.player.setPosition(max(0, target))

    # ---------------- playback ----------------
    def load(self, path: Path) -> None:
        self.stop()
        self._source = path
        self._duration = 0
        self._scrubbing = False
        self.slider.setRange(0, 0)
        self.slider.setValue(0)
        self._update_time_label(0)
        self.player.setSource(QUrl.fromLocalFile(str(path)))

    def play(self) -> None:
        self.player.play()

    def pause(self) -> None:
        self.player.pause()

    def toggle_play(self) -> None:
        if self.is_playing():
            self.player.pause()
        else:
            self.player.play()

    def stop(self) -> None:
        self.player.stop()

    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def source_path(self) -> Path | None:
        return self._source

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        # Only clicks on the picture toggle playback; the control bar handles its own clicks.
        if event.button() == Qt.MouseButton.LeftButton and self._widget.geometry().contains(
            event.position().toPoint()
        ):
            self.toggle_play()
        super().mousePressEvent(event)

    def _on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        # Leave the first frame visible at end of playback; Space (or a click) resumes.
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.player.setPosition(0)
            self.player.pause()

    def _on_error(self, _err, message: str) -> None:
        self.error.emit(message or "재생할 수 없는 영상입니다")
