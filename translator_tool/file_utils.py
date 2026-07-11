from __future__ import annotations

import os
from pathlib import Path


def atomic_write(path: Path, data: bytes) -> None:
    """Replace a file without exposing partially written content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_bytes(data)
    try:
        os.replace(temp_path, path)
    except PermissionError:
        # The game can occasionally keep a language file open on Windows.
        path.write_bytes(data)
        try:
            temp_path.unlink()
        except OSError:
            pass
