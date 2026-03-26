from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFileDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from app.backend.controller import CopyController


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ResumeCopyApp")
        self.setAcceptDrops(True)

        self.controller = CopyController(self)
        self.controller.progressChanged.connect(self._on_progress_changed)
        self.controller.speedChanged.connect(self._on_speed_changed)
        self.controller.statusChanged.connect(self._on_status_changed)
        self.controller.errorOccurred.connect(self._on_error)

        self._build_ui()
        self._set_idle_ui()

    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)

        # Source
        src_row = QHBoxLayout()
        self.src_edit = QLineEdit()
        self.src_edit.setPlaceholderText("Select source file (USB/peripheral)")
        self.src_browse = QPushButton("Browse...")
        self.src_browse.clicked.connect(self._pick_source)
        src_row.addWidget(QLabel("Source:"))
        src_row.addWidget(self.src_edit, 1)
        src_row.addWidget(self.src_browse)
        layout.addLayout(src_row)

        # Destination
        dst_row = QHBoxLayout()
        self.dst_edit = QLineEdit()
        self.dst_edit.setPlaceholderText("Select destination file location")
        self.dst_browse = QPushButton("Browse...")
        self.dst_browse.clicked.connect(self._pick_destination)
        dst_row.addWidget(QLabel("Destination:"))
        dst_row.addWidget(self.dst_edit, 1)
        dst_row.addWidget(self.dst_browse)
        layout.addLayout(dst_row)

        # Progress + speed
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)  # render smoother than percent
        layout.addWidget(self.progress)

        speed_row = QHBoxLayout()
        self.speed_label = QLabel("Speed: - MB/s")
        self.status_label = QLabel("Status: idle")
        speed_row.addWidget(self.speed_label)
        speed_row.addWidget(self.status_label, 1)
        layout.addLayout(speed_row)

        # Buttons
        btn_row = QHBoxLayout()
        self.start_resume_btn = QPushButton("Start / Resume")
        self.pause_btn = QPushButton("Pause")
        self.start_resume_btn.clicked.connect(self._start_or_resume)
        self.pause_btn.clicked.connect(self._pause)
        btn_row.addWidget(self.start_resume_btn)
        btn_row.addWidget(self.pause_btn)
        layout.addLayout(btn_row)

        # Tip
        self.drop_tip = QLabel("Drag a file from Explorer onto the window to set Source.")
        self.drop_tip.setWordWrap(True)
        layout.addWidget(self.drop_tip)

    def _set_idle_ui(self) -> None:
        self.progress.setValue(0)
        self.speed_label.setText("Speed: - MB/s")
        self.pause_btn.setEnabled(False)

    def _set_running_ui(self) -> None:
        self.pause_btn.setEnabled(True)

    def _set_paused_ui(self) -> None:
        self.pause_btn.setEnabled(False)

    def _pick_source(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select source file")
        if path:
            self.src_edit.setText(path)

    def _pick_destination(self) -> None:
        # Use SaveFileName to allow a path that might not exist yet.
        path, _ = QFileDialog.getSaveFileName(self, "Select destination file")
        if path:
            self.dst_edit.setText(path)

    def _get_source_path(self) -> Optional[Path]:
        p = self.src_edit.text().strip()
        if not p:
            return None
        return Path(p)

    def _get_destination_path(self) -> Optional[Path]:
        p = self.dst_edit.text().strip()
        if not p:
            return None
        return Path(p)

    def _start_or_resume(self) -> None:
        src = self._get_source_path()
        dst = self._get_destination_path()
        if not src or not dst:
            QMessageBox.warning(self, "Missing paths", "Please select both Source and Destination.")
            return
        self.controller.start_or_resume(str(src), str(dst))
        self._set_running_ui()

    def _pause(self) -> None:
        self.controller.pause()
        self._set_paused_ui()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        if not event.mimeData().hasUrls():
            return
        urls = event.mimeData().urls()
        if not urls:
            return
        local_path = urls[0].toLocalFile()
        if local_path and os.path.isfile(local_path):
            self.src_edit.setText(local_path)

    # Controller -> UI
    def _on_progress_changed(self, bytes_copied: int, total_bytes: int) -> None:
        if total_bytes <= 0:
            self.progress.setValue(0)
            return
        pct_x10 = int((bytes_copied * 1000) // total_bytes)
        self.progress.setValue(max(0, min(1000, pct_x10)))

    def _on_speed_changed(self, mb_per_s: float) -> None:
        if mb_per_s <= 0:
            self.speed_label.setText("Speed: - MB/s")
        else:
            self.speed_label.setText(f"Speed: {mb_per_s:.2f} MB/s")

    def _on_status_changed(self, text: str) -> None:
        self.status_label.setText(f"Status: {text}")
        if text.lower().startswith("paused"):
            self._set_paused_ui()
        elif text.lower().startswith("complete"):
            self.pause_btn.setEnabled(False)

    def _on_error(self, message: str) -> None:
        self._set_idle_ui()
        QMessageBox.critical(self, "Copy error", message)

