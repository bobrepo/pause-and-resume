from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPaintEvent
from PyQt5.QtWidgets import (
    QButtonGroup,
    QFrame,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QRadioButton,
    QPushButton,
    QProgressBar,
    QHBoxLayout,
    QVBoxLayout,
    QStackedWidget,
    QWidget,
)

from app.backend.controller import CopyController


class GradientBackground(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAutoFillBackground(False)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        # Simple "background image" feel: layered gradients.
        w = max(1, self.width())
        h = max(1, self.height())

        # Base
        painter.fillRect(0, 0, w, h, QColor("#0b1220"))

        # Soft blobs
        for (x, y, r, c) in [
            (int(w * 0.15), int(h * 0.25), int(min(w, h) * 0.55), QColor(90, 110, 255, 60)),
            (int(w * 0.75), int(h * 0.20), int(min(w, h) * 0.45), QColor(0, 220, 180, 50)),
            (int(w * 0.55), int(h * 0.85), int(min(w, h) * 0.60), QColor(255, 140, 70, 40)),
        ]:
            painter.setBrush(c)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(x - r // 2, y - r // 2, r, r)

        painter.end()


def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("Card")
    f.setFrameShape(QFrame.NoFrame)
    return f


class WizardWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ResumeCopyApp")
        self.setMinimumSize(860, 520)

        self.controller = CopyController(self)
        self.controller.progressChanged.connect(self._on_progress_changed)
        self.controller.speedChanged.connect(self._on_speed_changed)
        self.controller.statusChanged.connect(self._on_status_changed)
        self.controller.errorOccurred.connect(self._on_error)
        self.controller.copyCompleted.connect(self._on_copy_completed)
        self.controller.deviceDisconnected.connect(self._on_device_disconnected)

        self._auto_return_timer = QTimer(self)
        self._auto_return_timer.setSingleShot(True)
        self._auto_return_timer.timeout.connect(self._go_start)

        self._build_ui()
        self._go_start()

    def _build_ui(self) -> None:
        root = GradientBackground(self)
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(14)

        title = QLabel("ResumeCopyApp")
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: white;")
        subtitle = QLabel("Resumable file copy for USB / peripheral devices")
        subtitle.setStyleSheet("color: rgba(255,255,255,180);")
        root_layout.addWidget(title)
        root_layout.addWidget(subtitle)

        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack, 1)

        # Screen 1: start (mode + paths)
        self.page_start = GradientBackground()
        self.page_start.setObjectName("PageStart")
        self.page_start.setAcceptDrops(True)
        self.page_start.dragEnterEvent = self.dragEnterEvent  # type: ignore
        self.page_start.dropEvent = self.dropEvent  # type: ignore

        p1 = QVBoxLayout(self.page_start)
        p1.setContentsMargins(0, 0, 0, 0)
        p1.setSpacing(14)

        card1 = _card()
        c1 = QVBoxLayout(card1)
        c1.setContentsMargins(18, 18, 18, 18)
        c1.setSpacing(12)

        mode_label = QLabel("Step 1: Choose mode")
        mode_label.setStyleSheet("color: white; font-size: 14px; font-weight: 600;")
        c1.addWidget(mode_label)

        self.mode_fresh = QRadioButton("Start from beginning (new copy)")
        self.mode_resume = QRadioButton("Resume a partially-copied file")
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.mode_fresh)
        self.mode_group.addButton(self.mode_resume)
        self.mode_fresh.setChecked(True)
        for rb in (self.mode_fresh, self.mode_resume):
            rb.setStyleSheet("color: rgba(255,255,255,210); font-size: 13px;")
            c1.addWidget(rb)

        self.mode_help = QLabel(
            "Tip: If the USB/peripheral disconnects during copy, the app saves progress and can resume later."
        )
        self.mode_help.setWordWrap(True)
        self.mode_help.setStyleSheet("color: rgba(255,255,255,160);")
        c1.addWidget(self.mode_help)

        card2 = _card()
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(18, 18, 18, 18)
        c2.setSpacing(10)

        paths_label = QLabel("Step 2: Select files")
        paths_label.setStyleSheet("color: white; font-size: 14px; font-weight: 600;")
        c2.addWidget(paths_label)

        # Source
        src_row = QHBoxLayout()
        self.src_edit = QLineEdit()
        self.src_edit.setPlaceholderText("Select source file (USB/peripheral)")
        self.src_browse = QPushButton("Browse...")
        self.src_browse.clicked.connect(self._pick_source)
        src_row.addWidget(QLabel("Source:"))
        src_row.addWidget(self.src_edit, 1)
        src_row.addWidget(self.src_browse)
        c2.addLayout(src_row)

        # Destination (final) for fresh mode
        self.dst_edit = QLineEdit()
        self.dst_edit.setPlaceholderText("Select destination file location (final file)")
        self.dst_browse = QPushButton("Browse...")
        self.dst_browse.clicked.connect(self._pick_destination_final)
        dst_row = QHBoxLayout()
        dst_row.addWidget(QLabel("Destination:"))
        dst_row.addWidget(self.dst_edit, 1)
        dst_row.addWidget(self.dst_browse)
        c2.addLayout(dst_row)

        # Corrupted/partial destination for resume mode
        self.partial_edit = QLineEdit()
        self.partial_edit.setPlaceholderText("Select corrupted/partial destination file to resume (e.g., .partial)")
        self.partial_browse = QPushButton("Browse...")
        self.partial_browse.clicked.connect(self._pick_partial_existing)
        part_row = QHBoxLayout()
        part_row.addWidget(QLabel("Partial file:"))
        part_row.addWidget(self.partial_edit, 1)
        part_row.addWidget(self.partial_browse)
        c2.addLayout(part_row)

        actions_row = QHBoxLayout()
        actions_row.addStretch(1)
        self.start_btn = QPushButton("Continue to copy")
        self.start_btn.clicked.connect(self._start_from_screen1)
        actions_row.addWidget(self.start_btn)
        c2.addLayout(actions_row)

        self.drop_tip = QLabel("Drag a file from Explorer onto this window to set Source.")
        self.drop_tip.setWordWrap(True)
        self.drop_tip.setStyleSheet("color: rgba(255,255,255,150);")
        c2.addWidget(self.drop_tip)

        p1.addWidget(card1)
        p1.addWidget(card2, 1)
        self.stack.addWidget(self.page_start)

        # Screen 2: progress
        self.page_progress = GradientBackground()
        p2 = QVBoxLayout(self.page_progress)
        p2.setContentsMargins(0, 0, 0, 0)
        p2.setSpacing(14)

        prog_card = _card()
        pc = QVBoxLayout(prog_card)
        pc.setContentsMargins(18, 18, 18, 18)
        pc.setSpacing(12)

        self.progress_title = QLabel("Copying…")
        self.progress_title.setStyleSheet("color: white; font-size: 16px; font-weight: 650;")
        pc.addWidget(self.progress_title)

        self.progress_sub = QLabel("Do not remove the USB/peripheral device while copying.")
        self.progress_sub.setStyleSheet("color: rgba(255,255,255,170);")
        pc.addWidget(self.progress_sub)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(True)
        pc.addWidget(self.progress)

        info_row = QHBoxLayout()
        self.speed_label = QLabel("Speed: - MB/s")
        self.status_label = QLabel("Status: Copying")
        self.speed_label.setStyleSheet("color: rgba(255,255,255,210);")
        self.status_label.setStyleSheet("color: rgba(255,255,255,210);")
        info_row.addWidget(self.speed_label)
        info_row.addStretch(1)
        info_row.addWidget(self.status_label)
        pc.addLayout(info_row)

        btn_row = QHBoxLayout()
        self.pause_btn = QPushButton("Pause (safe)")
        self.pause_btn.clicked.connect(self._pause)
        self.back_to_start_btn = QPushButton("Back to start")
        self.back_to_start_btn.clicked.connect(self._go_start)
        btn_row.addWidget(self.pause_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.back_to_start_btn)
        pc.addLayout(btn_row)

        p2.addWidget(prog_card, 1)
        self.stack.addWidget(self.page_progress)

        # Screen 3: status (completed or disconnected)
        self.page_status = GradientBackground()
        p3 = QVBoxLayout(self.page_status)
        p3.setContentsMargins(0, 0, 0, 0)
        p3.setSpacing(14)

        status_card = _card()
        sc = QVBoxLayout(status_card)
        sc.setContentsMargins(18, 18, 18, 18)
        sc.setSpacing(12)

        self.status_title = QLabel("Status")
        self.status_title.setStyleSheet("color: white; font-size: 16px; font-weight: 650;")
        sc.addWidget(self.status_title)

        self.status_message = QLabel("")
        self.status_message.setWordWrap(True)
        self.status_message.setStyleSheet("color: rgba(255,255,255,200);")
        sc.addWidget(self.status_message)

        self.status_file = QLabel("")
        self.status_file.setWordWrap(True)
        self.status_file.setStyleSheet("color: rgba(255,255,255,160);")
        sc.addWidget(self.status_file)

        status_btns = QHBoxLayout()
        self.status_back_btn = QPushButton("Back to start")
        self.status_back_btn.clicked.connect(self._go_start)
        status_btns.addWidget(self.status_back_btn)
        status_btns.addStretch(1)
        self.status_resume_btn = QPushButton("Resume now")
        self.status_resume_btn.clicked.connect(self._resume_now)
        status_btns.addWidget(self.status_resume_btn)
        sc.addLayout(status_btns)

        p3.addWidget(status_card, 1)
        self.stack.addWidget(self.page_status)

        # Theme
        self._apply_theme()
        self.mode_group.buttonClicked.connect(self._sync_mode_ui)
        self._sync_mode_ui()

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QLineEdit {
              padding: 8px 10px;
              border-radius: 10px;
              border: 1px solid rgba(255,255,255,40);
              background: rgba(255,255,255,18);
              color: rgba(255,255,255,230);
              selection-background-color: rgba(120,160,255,120);
            }
            QLabel { color: rgba(255,255,255,210); }
            QPushButton {
              padding: 10px 14px;
              border-radius: 10px;
              background: rgba(255,255,255,18);
              border: 1px solid rgba(255,255,255,45);
              color: rgba(255,255,255,230);
            }
            QPushButton:hover { background: rgba(255,255,255,26); }
            QPushButton:disabled { color: rgba(255,255,255,110); background: rgba(255,255,255,10); }
            QProgressBar {
              border-radius: 10px;
              border: 1px solid rgba(255,255,255,40);
              background: rgba(255,255,255,14);
              color: rgba(255,255,255,180);
              padding: 2px;
            }
            QProgressBar::chunk {
              border-radius: 8px;
              background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 rgba(90,110,255,220),
                stop:0.6 rgba(0,220,180,220),
                stop:1 rgba(255,140,70,220));
            }
            QFrame#Card {
              border-radius: 16px;
              background: rgba(255,255,255,10);
              border: 1px solid rgba(255,255,255,22);
            }
            """
        )

    def _sync_mode_ui(self) -> None:
        fresh = self.mode_fresh.isChecked()
        self.dst_edit.setEnabled(fresh)
        self.dst_browse.setEnabled(fresh)
        self.partial_edit.setEnabled(not fresh)
        self.partial_browse.setEnabled(not fresh)

    def _pick_source(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select source file")
        if path:
            self.src_edit.setText(path)

    def _pick_destination_final(self) -> None:
        # Use SaveFileName to allow a path that might not exist yet.
        path, _ = QFileDialog.getSaveFileName(self, "Select destination file")
        if path:
            self.dst_edit.setText(path)

    def _pick_partial_existing(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select partial/corrupted destination file to resume")
        if path:
            self.partial_edit.setText(path)

    def _get_source_path(self) -> Optional[Path]:
        p = self.src_edit.text().strip()
        if not p:
            return None
        return Path(p)

    def _get_destination_final(self) -> Optional[Path]:
        p = self.dst_edit.text().strip()
        if not p:
            return None
        return Path(p)

    def _get_partial_existing(self) -> Optional[Path]:
        p = self.partial_edit.text().strip()
        if not p:
            return None
        return Path(p)

    def _start_from_screen1(self) -> None:
        src = self._get_source_path()
        if not src:
            QMessageBox.warning(self, "Missing source", "Please select a Source file.")
            return

        if self.mode_fresh.isChecked():
            dst_final = self._get_destination_final()
            if not dst_final:
                QMessageBox.warning(self, "Missing destination", "Please select a Destination (final) path.")
                return
            final_path = str(dst_final)
            partial_path = final_path + ".partial"
        else:
            partial_existing = self._get_partial_existing()
            if not partial_existing:
                QMessageBox.warning(self, "Missing partial file", "Please select the corrupted/partial destination file to resume.")
                return
            partial_path = str(partial_existing)
            # If it's a .partial file, strip it for the final name; otherwise keep same.
            final_path = partial_path[:-8] if partial_path.lower().endswith(".partial") else partial_path

        self._reset_progress_ui()
        self.stack.setCurrentWidget(self.page_progress)
        self.pause_btn.setEnabled(True)
        self.controller.start_or_resume(str(src), partial_path, final_path)

    def _pause(self) -> None:
        self.controller.pause()
        self.pause_btn.setEnabled(False)
        self.status_label.setText("Status: Paused")

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
        mb = bytes_copied / (1024 * 1024)
        mb_total = total_bytes / (1024 * 1024)
        self.progress.setFormat(f"{mb:.1f} / {mb_total:.1f} MB")

    def _on_speed_changed(self, mb_per_s: float) -> None:
        if mb_per_s <= 0:
            self.speed_label.setText("Speed: - MB/s")
        else:
            self.speed_label.setText(f"Speed: {mb_per_s:.2f} MB/s")

    def _on_status_changed(self, text: str) -> None:
        self.status_label.setText(f"Status: {text}")
        if text.lower().startswith("paused"):
            self.pause_btn.setEnabled(False)

    def _on_error(self, message: str) -> None:
        self._auto_return_timer.stop()
        self.stack.setCurrentWidget(self.page_status)
        self.status_title.setText("Copy error")
        self.status_message.setText(message)
        self.status_file.setText("")
        self.status_resume_btn.setEnabled(False)

    def _on_device_disconnected(self, message: str, partial_path: str) -> None:
        self._auto_return_timer.stop()
        self.stack.setCurrentWidget(self.page_status)
        self.status_title.setText("Device disconnected")
        self.status_message.setText(message)
        self.status_file.setText(f"Saved partial file: {Path(partial_path).name}")
        self.status_resume_btn.setEnabled(True)

    def _on_copy_completed(self, final_path: str, partial_path: str) -> None:
        self.stack.setCurrentWidget(self.page_status)
        self.status_title.setText("Copy complete")
        self.status_message.setText("Your file was copied successfully.")
        self.status_file.setText(f"Saved file: {Path(final_path).name}")
        self.status_resume_btn.setEnabled(False)
        # “take to first screen” after completion (but show this screen briefly)
        self._auto_return_timer.start(2500)

    def _resume_now(self) -> None:
        # For simplicity, resume is driven by going back and starting again (same paths still in fields).
        # This also allows the user to reselect paths if the device letter changed.
        self._go_start()

    def _go_start(self) -> None:
        self._auto_return_timer.stop()
        self.stack.setCurrentWidget(self.page_start)
        self._sync_mode_ui()

    def _reset_progress_ui(self) -> None:
        self.progress.setValue(0)
        self.progress.setFormat("")
        self.speed_label.setText("Speed: - MB/s")
        self.status_label.setText("Status: Starting")
        self.pause_btn.setEnabled(True)

