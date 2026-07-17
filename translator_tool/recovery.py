from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time

from .file_utils import atomic_write
from .project import Project
from .settings import settings_dir


RECOVERY_VERSION = 1
MAX_RECOVERY_BYTES = 64 * 1024 * 1024
MAX_RECOVERY_UNITS = 100_000


@dataclass(frozen=True)
class RecoveryUnit:
    uid: str
    base_text: str
    text: str
    pending_delete: bool


@dataclass(frozen=True)
class RecoveryDraft:
    project_root: str
    language: str
    saved_at: float
    units: tuple[RecoveryUnit, ...]


def recovery_path(project_root: Path, language: str) -> Path:
    identity = f"{project_root.resolve()}\n{language}".encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()[:24]
    return settings_dir() / "recovery" / f"{digest}.json"


def save_recovery_draft(project: Project) -> int:
    units = tuple(
        RecoveryUnit(unit.uid, unit.translate_text, unit.current_text, unit.pending_delete)
        for unit in project.units
        if unit.pending_delete or unit.current_text != unit.translate_text
    )
    path = recovery_path(project.root, project.language)
    if not units:
        clear_recovery_draft(project.root, project.language)
        return 0
    payload = {
        "version": RECOVERY_VERSION,
        "project_root": str(project.root.resolve()),
        "language": project.language,
        "saved_at": time.time(),
        "units": [
            {
                "uid": unit.uid,
                "base_text": unit.base_text,
                "text": unit.text,
                "pending_delete": unit.pending_delete,
            }
            for unit in units
        ],
    }
    data = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(data) > MAX_RECOVERY_BYTES:
        raise OSError(f"recovery draft is too large: {len(data)} bytes")
    atomic_write(path, data)
    return len(units)


def load_recovery_draft(project_root: Path, language: str) -> RecoveryDraft | None:
    path = recovery_path(project_root, language)
    try:
        if path.stat().st_size > MAX_RECOVERY_BYTES:
            raise ValueError("recovery draft exceeds the size limit")
        raw = json.loads(path.read_text(encoding="utf-8"))
        expected_root = str(project_root.resolve())
        if (
            not isinstance(raw, dict)
            or raw.get("version") != RECOVERY_VERSION
            or raw.get("project_root") != expected_root
            or raw.get("language") != language
        ):
            raise ValueError("recovery draft identity does not match the project")
        raw_units = raw.get("units")
        if not isinstance(raw_units, list) or len(raw_units) > MAX_RECOVERY_UNITS:
            raise ValueError("recovery draft unit list is invalid")
        units: list[RecoveryUnit] = []
        for item in raw_units:
            if not isinstance(item, dict):
                raise ValueError("recovery draft entry is invalid")
            uid = item.get("uid")
            base_text = item.get("base_text")
            text = item.get("text")
            pending_delete = item.get("pending_delete")
            if not isinstance(uid, str) or not isinstance(base_text, str) or not isinstance(text, str) or not isinstance(pending_delete, bool):
                raise ValueError("recovery draft entry has invalid fields")
            units.append(RecoveryUnit(uid, base_text, text, pending_delete))
        saved_at = raw.get("saved_at")
        if not isinstance(saved_at, (int, float)):
            saved_at = 0.0
        return RecoveryDraft(expected_root, language, float(saved_at), tuple(units))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        try:
            path.unlink()
        except OSError:
            pass
        return None


def apply_recovery_draft(project: Project, draft: RecoveryDraft) -> tuple[int, int]:
    restored = 0
    skipped = 0
    for recovered in draft.units:
        unit = project.unit_by_uid(recovered.uid)
        if unit is None or unit.translate_text != recovered.base_text:
            skipped += 1
            continue
        unit.set_text(recovered.text)
        unit.set_pending_delete(recovered.pending_delete)
        restored += 1
    return restored, skipped


def clear_recovery_draft(project_root: Path, language: str) -> None:
    path = recovery_path(project_root, language)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
