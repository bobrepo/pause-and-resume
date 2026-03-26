from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Metadata:
    data: Dict[str, Any]


class MetadataStore:
    @staticmethod
    def load(meta_path: str) -> Metadata:
        p = Path(meta_path)
        with p.open("r", encoding="utf-8") as f:
            return Metadata(json.load(f))

    @staticmethod
    def save_atomic(meta_path: str, metadata: Dict[str, Any]) -> None:
        """
        Write JSON safely:
        - Write to `meta_path + ".tmp"`
        - Flush + fsync temp file
        - os.replace to atomically swap
        """
        p = Path(meta_path)
        tmp_path = str(p) + ".tmp"

        # Keep it compact: we rewrite frequently (per chunk).
        payload = json.dumps(metadata, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                # Best-effort; some FS may not support fsync reliably.
                pass

        os.replace(tmp_path, meta_path)

    @staticmethod
    def new_metadata(
        source_path: str,
        destination_path: str,
        source_size: int,
        chunk_size: int,
        total_chunks: int,
        completed_obj: Dict[str, Any],
        bytes_copied: int,
    ) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "source_path": source_path,
            "destination_path": destination_path,
            "source_size": int(source_size),
            "chunk_size": int(chunk_size),
            "total_chunks": int(total_chunks),
            "completed": completed_obj,
            "bytes_copied": int(bytes_copied),
            "last_updated_utc": utc_now_iso(),
        }

