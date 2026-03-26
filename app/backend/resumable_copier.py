from __future__ import annotations

import os
import time
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from app.backend.chunk_bitmap import CompletedChunks
from app.backend.fs_safety import flush_and_fsync, read_exact
from app.backend.metadata_store import MetadataStore


@dataclass
class CopyState:
    # idle | copying | paused | complete | error
    status: str = "idle"


class ResumableCopier(QObject):
    progressChanged = pyqtSignal(int, int)  # bytes_copied, total_bytes
    speedChanged = pyqtSignal(float)  # MB/s
    statusChanged = pyqtSignal(str)
    errorOccurred = pyqtSignal(str)
    copyCompleted = pyqtSignal(str, str)  # final_path, partial_path
    deviceDisconnected = pyqtSignal(str, str)  # message, partial_path

    def __init__(
        self,
        parent: Optional[QObject] = None,
        chunk_size: int = 1024 * 1024,
        chunks_per_tick: int = 1,
        timer_interval_ms: int = 1,
    ) -> None:
        super().__init__(parent)
        self.chunk_size = int(chunk_size)
        self.chunks_per_tick = int(chunks_per_tick)
        self.timer_interval_ms = int(timer_interval_ms)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_timer_tick)

        self._state = CopyState()
        self.pause_requested = False

        self.source_path: Optional[str] = None
        self.dest_path: Optional[str] = None  # partial/working file
        self.final_dest_path: Optional[str] = None
        self.meta_path: Optional[str] = None

        self.source_size: int = 0
        self.total_chunks: int = 0
        self.current_chunk_index: int = 0
        self.completed: Optional[CompletedChunks] = None
        self.bytes_copied: int = 0

        self.src_fp = None
        self.dest_fp = None

        self._speed_start_time: float = 0.0
        self._last_speed_time: float = 0.0
        self._last_speed_bytes: int = 0

        self._tick = 0

    def _set_status(self, s: str) -> None:
        # Keep an internal lowercase state for control flow,
        # while emitting a human-readable label for the UI.
        self._state.status = s.lower()
        self.statusChanged.emit(s)

    def _close_files(self) -> None:
        for fp in (self.src_fp, self.dest_fp):
            try:
                if fp is not None:
                    fp.close()
            except Exception:
                pass
        self.src_fp = None
        self.dest_fp = None

    def pause(self) -> None:
        if self._state.status != "copying":
            return
        self.pause_requested = True
        # Stop between chunks.
        self.timer.stop()
        self._set_status("Paused")

    def resume(self) -> None:
        if self._state.status not in ("paused", "idle"):
            return
        # Reopen file handles and validate metadata again.
        # This keeps resume robust after interruptions (USB disconnect/reconnect).
        if not self.source_path or not self.dest_path:
            return
        self.start_or_resume(self.source_path, self.dest_path, self.final_dest_path or self.dest_path)

    def start_or_resume(self, source_path: str, partial_destination_path: str, final_destination_path: str) -> None:
        self._set_status("Starting")
        self.timer.stop()
        self.pause_requested = False
        self._close_files()

        try:
            # Normalize paths for robust resume across different drive-letter casing etc.
            self.source_path = os.path.abspath(str(Path(source_path)))
            self.dest_path = os.path.abspath(str(Path(partial_destination_path)))
            self.final_dest_path = os.path.abspath(str(Path(final_destination_path)))
            source_path_norm = os.path.normcase(self.source_path)
            dest_path_norm = os.path.normcase(self.dest_path)
            self.meta_path = self.dest_path + ".resume.json"

            if not os.path.isfile(self.source_path):
                raise FileNotFoundError(f"Source file not found: {self.source_path}")

            self.source_size = os.path.getsize(self.source_path)
            self.total_chunks = int(ceil(self.source_size / float(self.chunk_size))) if self.source_size > 0 else 0

            # Build or validate metadata
            meta = None
            if os.path.exists(self.meta_path):
                try:
                    meta = MetadataStore.load(self.meta_path).data
                except Exception:
                    meta = None

            valid = self._validate_metadata(meta, source_path_norm, dest_path_norm, self.source_size)
            if meta is None or not valid:
                self._start_new_copy()
            else:
                self._load_existing_copy(meta)

            # Ensure destination exists and we can random-access it
            os.makedirs(str(Path(self.dest_path).parent), exist_ok=True)

            self._open_dest_for_copy()
            self._open_source_for_copy()

            # Always enforce dest length == source_size best-effort.
            if self.dest_fp is not None:
                try:
                    if self.source_size is not None:
                        current_size = os.fstat(self.dest_fp.fileno()).st_size
                        if current_size != self.source_size:
                            # Truncate only if larger; extension is handled by writes.
                            if current_size > self.source_size:
                                self.dest_fp.truncate(self.source_size)
                except Exception:
                    pass

            if self.current_chunk_index >= self.total_chunks:
                self._finalize_complete()
                return

            self._speed_start_time = time.perf_counter()
            self._last_speed_time = self._speed_start_time
            self._last_speed_bytes = self.bytes_copied

            self._set_status("Copying")
            self.timer.start(self.timer_interval_ms)
        except Exception as e:
            self._set_status("Error")
            msg = f"{type(e).__name__}: {e}"
            self.errorOccurred.emit(msg)

    def _validate_metadata(
        self,
        meta: Optional[dict],
        source_path_norm: str,
        dest_path_norm: str,
        source_size: int,
    ) -> bool:
        if not meta:
            return False
        try:
            if meta.get("schema_version") != 1:
                return False
            if os.path.normcase(str(meta.get("source_path"))) != source_path_norm:
                return False
            if os.path.normcase(str(meta.get("destination_path"))) != dest_path_norm:
                return False
            if int(meta.get("source_size")) != int(source_size):
                return False
            if int(meta.get("chunk_size")) != int(self.chunk_size):
                return False
            if int(meta.get("total_chunks")) != int(self.total_chunks):
                # If source_size changed, total_chunks changes; invalid above already checks size.
                return False
            return True
        except Exception:
            return False

    def _start_new_copy(self) -> None:
        self.completed = CompletedChunks.new_empty(self.total_chunks, self.chunk_size)
        self.current_chunk_index = 0
        self.bytes_copied = 0
        assert self.meta_path is not None

        # Create initial metadata early so interruptions can be resumed.
        completed_obj = self.completed.to_metadata()
        meta = MetadataStore.new_metadata(
            source_path=self.source_path or "",
            destination_path=self.dest_path or "",
            source_size=self.source_size,
            chunk_size=self.chunk_size,
            total_chunks=self.total_chunks,
            completed_obj=completed_obj,
            bytes_copied=self.bytes_copied,
        )
        MetadataStore.save_atomic(self.meta_path, meta)

    def _load_existing_copy(self, meta: dict) -> None:
        completed_obj = meta.get("completed") or {}
        self.bytes_copied = int(meta.get("bytes_copied") or 0)
        self.bytes_copied = max(0, min(self.source_size, self.bytes_copied))

        # Prefer chunk-map resume; fall back to byte-offset if the completed chunk structure
        # is missing or fails to parse.
        try:
            self.completed = CompletedChunks.from_metadata(completed_obj, self.total_chunks, self.chunk_size)
            self.current_chunk_index = self.completed.find_first_incomplete()
        except Exception:
            # Fallback: treat `bytes_copied` as a completed prefix.
            if self.chunk_size > 0:
                bytes_copied = max(0, min(self.source_size, self.bytes_copied))
                completed_chunks = bytes_copied // self.chunk_size
            else:
                completed_chunks = 0
            completed_chunks = max(0, min(self.total_chunks, int(completed_chunks)))
            self.completed = CompletedChunks(kind="ranges", total_chunks=self.total_chunks, chunk_size=self.chunk_size, ranges=[[0, completed_chunks]])
            self.current_chunk_index = completed_chunks

    def _open_dest_for_copy(self) -> None:
        assert self.dest_path is not None
        # r+b fails for non-existing; w+b creates.
        mode = "r+b" if os.path.exists(self.dest_path) else "w+b"
        self.dest_fp = open(self.dest_path, mode)

    def _open_source_for_copy(self) -> None:
        assert self.source_path is not None
        self.src_fp = open(self.source_path, "rb")

    def _on_timer_tick(self) -> None:
        if self._state.status != "copying":
            return
        if self.pause_requested:
            return

        try:
            for _ in range(self.chunks_per_tick):
                if self.current_chunk_index >= self.total_chunks:
                    self._finalize_complete()
                    return

                if self.completed is None:
                    raise RuntimeError("Completed tracker missing")

                # Safety: skip completed chunks if metadata got out of sync.
                if self.completed.is_completed(self.current_chunk_index):
                    self.current_chunk_index += 1
                    continue

                self._copy_one_chunk(self.current_chunk_index)
                self.current_chunk_index += 1

            # Let UI refresh between ticks.
            self._tick += 1
        except Exception as e:
            self.timer.stop()
            self._set_status("Error")
            self.errorOccurred.emit(f"{type(e).__name__}: {e}")

    def _copy_one_chunk(self, chunk_idx: int) -> None:
        if self.src_fp is None or self.dest_fp is None:
            raise RuntimeError("File handles not open")
        if chunk_idx < 0 or chunk_idx >= self.total_chunks:
            return

        offset = chunk_idx * self.chunk_size
        remaining = self.source_size - offset
        to_copy = min(self.chunk_size, remaining)
        if to_copy <= 0:
            return

        try:
            self.src_fp.seek(offset)
            data = read_exact(self.src_fp, to_copy)
            if len(data) != to_copy:
                raise IOError(f"Unexpected EOF while reading chunk {chunk_idx}")
        except OSError as e:
            if _looks_like_device_disconnect(e):
                self.timer.stop()
                self._set_status("Disconnected")
                self.deviceDisconnected.emit(
                    "USB/peripheral device disconnected. Your partial file is saved and you can resume from where it left off.",
                    self.dest_path or "",
                )
                raise
            raise

        self.dest_fp.seek(offset)
        written = self.dest_fp.write(data)
        if written != len(data):
            raise IOError(f"Short write while writing chunk {chunk_idx}")

        # Ensure chunk bytes are on disk before we mark metadata.
        flush_and_fsync(self.dest_fp)

        if self.completed is None:
            raise RuntimeError("Completed tracker missing")
        self.completed.mark_completed(chunk_idx)
        self.bytes_copied += to_copy

        assert self.meta_path is not None
        meta = MetadataStore.new_metadata(
            source_path=self.source_path or "",
            destination_path=self.dest_path or "",
            source_size=self.source_size,
            chunk_size=self.chunk_size,
            total_chunks=self.total_chunks,
            completed_obj=self.completed.to_metadata(),
            bytes_copied=self.bytes_copied,
        )
        MetadataStore.save_atomic(self.meta_path, meta)

        self._emit_progress_and_speed(to_copy)

    def _emit_progress_and_speed(self, last_chunk_bytes: int) -> None:
        self.progressChanged.emit(self.bytes_copied, self.source_size)

        now = time.perf_counter()
        dt = now - self._last_speed_time
        if dt <= 0:
            return
        delta_bytes = self.bytes_copied - self._last_speed_bytes
        mb_per_s = (delta_bytes / dt) / (1024 * 1024)
        self.speedChanged.emit(float(mb_per_s))
        self._last_speed_time = now
        self._last_speed_bytes = self.bytes_copied

    def _finalize_complete(self) -> None:
        self.timer.stop()
        if self.completed is None:
            return
        # Mark remaining chunks as completed for final metadata consistency.
        while self.current_chunk_index < self.total_chunks:
            if not self.completed.is_completed(self.current_chunk_index):
                # No file IO here; copy loop should have already written.
                self.completed.mark_completed(self.current_chunk_index)
                # bytes_copied might be off if we reach here unexpectedly; keep it simple.
            self.current_chunk_index += 1

        self.bytes_copied = self.source_size
        assert self.meta_path is not None
        meta = MetadataStore.new_metadata(
            source_path=self.source_path or "",
            destination_path=self.dest_path or "",
            source_size=self.source_size,
            chunk_size=self.chunk_size,
            total_chunks=self.total_chunks,
            completed_obj=self.completed.to_metadata(),
            bytes_copied=self.bytes_copied,
        )
        MetadataStore.save_atomic(self.meta_path, meta)
        self.progressChanged.emit(self.source_size, self.source_size)
        self.speedChanged.emit(0.0)
        self._set_status("Complete")
        self._close_files()

        partial_path = self.dest_path or ""
        final_path = self.final_dest_path or partial_path

        # Finalize "formatting correctly": rename partial to final atomically where possible.
        try:
            if partial_path and final_path and os.path.normcase(partial_path) != os.path.normcase(final_path):
                os.replace(partial_path, final_path)
        except Exception:
            # If rename fails, keep partial; user can manually rename.
            final_path = partial_path

        # Metadata is no longer needed once complete.
        try:
            if self.meta_path and os.path.exists(self.meta_path):
                os.remove(self.meta_path)
        except Exception:
            pass

        self.copyCompleted.emit(final_path, partial_path)


def _looks_like_device_disconnect(e: OSError) -> bool:
    # Common Windows I/O codes when removable media disappears mid-read.
    winerror = getattr(e, "winerror", None)
    if winerror in (21, 1117, 1167, 31):  # ERROR_NOT_READY, ERROR_IO_DEVICE, ERROR_DEVICE_NOT_CONNECTED, ERROR_GEN_FAILURE
        return True
    msg = str(e).lower()
    if "device" in msg and ("not ready" in msg or "i/o" in msg or "io" in msg or "disconnected" in msg):
        return True
    return False

