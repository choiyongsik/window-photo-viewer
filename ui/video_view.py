from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QVBoxLayout, QWidget


class VideoView(QWidget):
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source: Path | None = None
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._widget)
        self.setStyleSheet("background:#141414;")

    def load(self, path: Path) -> None:
        self.stop()
        self._source = path
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
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_play()
        super().mousePressEvent(event)

    def _on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        # Leave the first frame visible at end of playback; Space (or a click) resumes.
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.player.setPosition(0)
            self.player.pause()

    def _on_error(self, _err, message: str) -> None:
        self.error.emit(message or "재생할 수 없는 영상입니다")
