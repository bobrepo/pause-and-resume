from __future__ import annotations

import base64
from dataclasses import dataclass
from math import ceil
from typing import List, Literal, Optional


CompletedKind = Literal["bitmap", "ranges"]


def _estimate_bitmap_json_size(total_chunks: int) -> int:
    # Rough estimate: base64 chars ~ 4/3 * bytes. bytes=ceil(chunks/8)
    if total_chunks <= 0:
        return 0
    bitmap_bytes = ceil(total_chunks / 8)
    est_base64_len = int(ceil(bitmap_bytes / 3) * 4)
    # Include some JSON overhead (very rough).
    return est_base64_len + 256


@dataclass
class CompletedChunks:
    kind: CompletedKind
    total_chunks: int
    chunk_size: int

    # bitmap fields
    bitmap_b64: Optional[str] = None

    # ranges fields: list of [start_inclusive, end_exclusive]
    ranges: Optional[List[List[int]]] = None

    @staticmethod
    def choose_kind(total_chunks: int) -> CompletedKind:
        # Practical threshold: keep the JSON metadata small enough.
        # If a 1MiB chunk size is used, even big files are usually okay.
        if _estimate_bitmap_json_size(total_chunks) <= 64 * 1024:
            return "bitmap"
        return "ranges"

    @classmethod
    def new_empty(cls, total_chunks: int, chunk_size: int) -> "CompletedChunks":
        kind = cls.choose_kind(total_chunks)
        if kind == "bitmap":
            # Start with all zeros.
            bitmap_bytes = ceil(total_chunks / 8) if total_chunks > 0 else 0
            bitmap = bytes([0]) * bitmap_bytes
            return cls(kind=kind, total_chunks=total_chunks, chunk_size=chunk_size, bitmap_b64=base64.b64encode(bitmap).decode("ascii"))
        return cls(kind="ranges", total_chunks=total_chunks, chunk_size=chunk_size, ranges=[])

    @classmethod
    def from_metadata(cls, completed_obj: dict, total_chunks: int, chunk_size: int) -> "CompletedChunks":
        kind = completed_obj.get("kind")
        if kind not in ("bitmap", "ranges"):
            raise ValueError("Unsupported completed.kind")
        if kind == "bitmap":
            b64 = completed_obj.get("value")
            if not isinstance(b64, str):
                raise ValueError("Invalid bitmap value")
            # Validate by decoding once so we fail early on corrupted metadata.
            raw = base64.b64decode(b64.encode("ascii"), validate=False)
            _ = raw  # keep for readability
            return cls(kind="bitmap", total_chunks=total_chunks, chunk_size=chunk_size, bitmap_b64=b64)

        raw_ranges = completed_obj.get("value") or []
        if not isinstance(raw_ranges, list):
            raise ValueError("Invalid ranges value")
        sanitized: List[List[int]] = []
        for r in raw_ranges:
            if not isinstance(r, list) or len(r) != 2:
                continue
            start, end = int(r[0]), int(r[1])
            start = max(0, min(total_chunks, start))
            end = max(0, min(total_chunks, end))
            if start < end:
                sanitized.append([start, end])
        sanitized.sort(key=lambda x: x[0])
        # Merge overlaps/adjacent.
        merged: List[List[int]] = []
        for s, e in sanitized:
            if not merged:
                merged.append([s, e])
                continue
            ps, pe = merged[-1]
            if s <= pe:  # overlap or adjacent
                merged[-1][1] = max(pe, e)
            else:
                merged.append([s, e])
        return cls(kind="ranges", total_chunks=total_chunks, chunk_size=chunk_size, ranges=merged)

    def to_metadata(self) -> dict:
        if self.kind == "bitmap":
            return {"kind": "bitmap", "value": self.bitmap_b64}
        return {"kind": "ranges", "value": self.ranges}

    def _bitmap(self) -> bytearray:
        if self.bitmap_b64 is None:
            return bytearray()
        raw = base64.b64decode(self.bitmap_b64.encode("ascii"))
        return bytearray(raw)

    def _set_bitmap(self, bitmap: bytearray) -> None:
        self.bitmap_b64 = base64.b64encode(bytes(bitmap)).decode("ascii")

    def is_completed(self, chunk_idx: int) -> bool:
        if chunk_idx < 0 or chunk_idx >= self.total_chunks:
            return False
        if self.kind == "bitmap":
            bitmap = self._bitmap()
            byte_i = chunk_idx // 8
            bit_i = chunk_idx % 8
            if byte_i >= len(bitmap):
                return False
            return (bitmap[byte_i] & (1 << bit_i)) != 0

        # ranges
        assert self.ranges is not None
        for start, end in self.ranges:
            if start <= chunk_idx < end:
                return True
        return False

    def mark_completed(self, chunk_idx: int) -> None:
        if chunk_idx < 0 or chunk_idx >= self.total_chunks:
            return
        if self.kind == "bitmap":
            bitmap = self._bitmap()
            byte_i = chunk_idx // 8
            bit_i = chunk_idx % 8
            # Ensure size.
            if byte_i >= len(bitmap):
                bitmap.extend([0] * (byte_i + 1 - len(bitmap)))
            bitmap[byte_i] = bitmap[byte_i] | (1 << bit_i)
            self._set_bitmap(bitmap)
            return

        # ranges: simplest reliable update -> append the single chunk, then normalize.
        assert self.ranges is not None
        self.ranges.append([chunk_idx, chunk_idx + 1])
        self.ranges.sort(key=lambda r: r[0])
        merged: List[List[int]] = []
        for s, e in self.ranges:
            if not merged:
                merged.append([s, e])
                continue
            ps, pe = merged[-1]
            if s <= pe:  # overlap or adjacent
                merged[-1][1] = max(pe, e)
            else:
                merged.append([s, e])
        self.ranges = merged

    def find_first_incomplete(self) -> int:
        if self.total_chunks <= 0:
            return 0

        if self.kind == "bitmap":
            bitmap = self._bitmap()
            for chunk_idx in range(self.total_chunks):
                byte_i = chunk_idx // 8
                bit_i = chunk_idx % 8
                if byte_i >= len(bitmap):
                    return chunk_idx
                if (bitmap[byte_i] & (1 << bit_i)) == 0:
                    return chunk_idx
            return self.total_chunks

        # ranges
        assert self.ranges is not None
        pointer = 0
        for start, end in self.ranges:
            if start > pointer:
                return pointer
            pointer = max(pointer, end)
            if pointer >= self.total_chunks:
                return self.total_chunks
        return pointer

