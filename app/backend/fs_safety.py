from __future__ import annotations

import os
from typing import BinaryIO


def read_exact(fp: BinaryIO, size: int) -> bytes:
    """
    Read exactly `size` bytes unless EOF is reached.
    Returns fewer bytes only if EOF occurs unexpectedly.
    """
    chunks = []
    remaining = size
    while remaining > 0:
        data = fp.read(remaining)
        if not data:
            break
        chunks.append(data)
        remaining -= len(data)
    return b"".join(chunks)


def flush_and_fsync(fp: BinaryIO) -> None:
    fp.flush()
    try:
        os.fsync(fp.fileno())
    except OSError:
        # Best-effort.
        pass

