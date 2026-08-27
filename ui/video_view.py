from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Signal
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
        self._widget = QVideoWidget(self)
        self.player.setVideoOutput(self._widget)
        self.player.errorOccurred.connect(self._on_error)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._widget)
        self.setStyleSheet("background:#141414;")

    def load(self, path: Path) -> None:
        self.stop()
        self._source = path
        self.player.setSource(QUrl.fromLocalFile(str(path)))

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

    def _on_error(self, _err, message: str) -> None:
        self.error.emit(message or "재생할 수 없는 영상입니다")
