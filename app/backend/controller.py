from __future__ import annotations

from typing import Optional

from PyQt5.QtCore import QObject, pyqtSignal

from app.backend.resumable_copier import ResumableCopier


class CopyController(QObject):
    progressChanged = pyqtSignal(int, int)  # bytes_copied, total_bytes
    speedChanged = pyqtSignal(float)  # MB/s
    statusChanged = pyqtSignal(str)
    errorOccurred = pyqtSignal(str)
    copyCompleted = pyqtSignal(str, str)  # final_path, partial_path
    deviceDisconnected = pyqtSignal(str, str)  # message, partial_path

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.copier = ResumableCopier()
        self.copier.progressChanged.connect(self.progressChanged)
        self.copier.speedChanged.connect(self.speedChanged)
        self.copier.statusChanged.connect(self.statusChanged)
        self.copier.errorOccurred.connect(self.errorOccurred)
        self.copier.copyCompleted.connect(self.copyCompleted)
        self.copier.deviceDisconnected.connect(self.deviceDisconnected)

    def start_or_resume(self, source_path: str, partial_destination_path: str, final_destination_path: str) -> None:
        self.copier.start_or_resume(source_path, partial_destination_path, final_destination_path)

    def pause(self) -> None:
        self.copier.pause()

    def resume(self) -> None:
        self.copier.resume()

