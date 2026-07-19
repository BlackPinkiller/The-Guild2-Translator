from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Iterable, Mapping


class FileChangedError(OSError):
    def __init__(self, paths: Iterable[Path]) -> None:
        self.paths = tuple(Path(path) for path in paths)
        super().__init__("file content changed since it was loaded: " + ", ".join(map(str, self.paths)))


def atomic_write(path: Path, data: bytes) -> None:
    """Replace a file without exposing partially written content."""
    atomic_write_many({path: data})


def atomic_write_many(
    writes: Mapping[Path, bytes],
    deletions: Iterable[Path] = (),
    *,
    expected: Mapping[Path, bytes] | None = None,
) -> None:
    """Commit several same-volume file changes, rolling back completed replacements on failure."""
    prepared_writes = {Path(path): data for path, data in writes.items()}
    deleted_paths = {Path(path) for path in deletions}
    expected_raw = {Path(path): data for path, data in (expected or {}).items()}
    overlap = set(prepared_writes) & deleted_paths
    if overlap:
        raise ValueError(f"paths cannot be written and deleted together: {sorted(map(str, overlap))}")
    if not prepared_writes and not deleted_paths:
        return

    temp_paths: dict[Path, Path] = {}
    backups: list[tuple[Path, Path | None]] = []
    try:
        for path, data in sorted(prepared_writes.items(), key=lambda item: str(item[0])):
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, raw_temp = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
            )
            temp_path = Path(raw_temp)
            temp_paths[path] = temp_path
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())

        changed = []
        for path, original in sorted(expected_raw.items(), key=lambda item: str(item[0])):
            current = path.read_bytes() if path.exists() else b""
            if current != original:
                changed.append(path)
        if changed:
            raise FileChangedError(changed)

        for path in sorted((*prepared_writes, *deleted_paths), key=str):
            if path in deleted_paths and not path.exists():
                continue
            backup_path = _create_backup(path) if path.exists() else None
            backups.append((path, backup_path))
            temp_path = temp_paths.get(path)
            if temp_path is not None:
                os.replace(temp_path, path)
            else:
                path.unlink()

    except Exception as exc:
        rollback_errors: list[str] = []
        for path, backup_path in reversed(backups):
            try:
                if path.exists():
                    path.unlink()
                if backup_path is not None and backup_path.exists():
                    os.replace(backup_path, path)
            except OSError as rollback_exc:
                rollback_errors.append(f"{path}: {rollback_exc}")
        if rollback_errors:
            detail = "; ".join(rollback_errors)
            raise OSError(f"file update failed ({exc}); rollback also failed: {detail}") from exc
        raise
    else:
        for _path, backup_path in backups:
            if backup_path is None:
                continue
            try:
                backup_path.unlink()
            except OSError:
                pass
    finally:
        for temp_path in temp_paths.values():
            try:
                temp_path.unlink()
            except OSError:
                pass


def _create_backup(path: Path) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".bak",
        dir=path.parent,
    )
    backup_path = Path(raw_path)
    try:
        with path.open("rb") as source, os.fdopen(descriptor, "wb") as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            backup_path.unlink()
        except OSError:
            pass
        raise
    return backup_path
