from __future__ import annotations

from pathlib import Path
import time

from ..project import Project, ProjectError
from .performance import SAVED_FILE_REFRESH_LIMIT_SECONDS, assert_within_budget


def assert_saved_file_refresh(project_root: Path, codec_root: Path) -> None:
    project = Project.load(project_root, "#chinese", codec_root=codec_root)
    conflict_unit = next(
        unit for unit in project.units if unit.ref.kind == "dbt" and unit.ref.target_row is not None
    )
    conflict_path = conflict_unit.ref.target_doc.path
    original_raw = conflict_path.read_bytes()
    external_raw = original_raw + b"\r\n// external change"
    project.apply_unit_edits(((conflict_unit, conflict_unit.current_text + "x", None),))
    conflict_path.write_bytes(external_raw)
    try:
        project.save()
    except ProjectError:
        pass
    else:
        raise AssertionError("save overwrote a translation file changed externally after load")
    if conflict_path.read_bytes() != external_raw:
        raise AssertionError("external-change conflict altered the on-disk translation file")
    conflict_path.write_bytes(original_raw)

    project = Project.load(project_root, "#chinese", codec_root=codec_root)
    edited = next(
        unit
        for unit in project.units
        if unit.ref.kind == "dbt" and unit.ref.target_row is not None and not unit.pending_delete
    )
    untouched = next(unit for unit in project.units if unit.file_rel != edited.file_rel)
    project.apply_unit_edits(((edited, edited.current_text + "x", None),))
    result = project.save()
    started = time.perf_counter()
    project.reload_saved_files(result.changed_files)
    assert_within_budget(
        "single changed-file refresh",
        time.perf_counter() - started,
        SAVED_FILE_REFRESH_LIMIT_SECONDS,
        detail=f"units={len(project.units)}",
    )
    refreshed = project.unit_by_uid(edited.uid)
    if refreshed is None or refreshed.current_text != edited.current_text or refreshed.is_dirty:
        raise AssertionError("changed-file refresh did not accept the durable edited translation")
    if project.unit_by_uid(untouched.uid) is not untouched:
        raise AssertionError("changed-file refresh rebuilt an unrelated translation file")
    _assert_matches_full_reload(project, codec_root)

    project.apply_unit_edits(((refreshed, refreshed.current_text, True),))
    deleted_result = project.save()
    project.reload_saved_files(deleted_result.changed_files)
    deleted = project.unit_by_uid(refreshed.uid)
    if deleted is None or deleted.ref.target_row is not None or deleted.current_text:
        raise AssertionError("changed-file refresh did not restore a deleted source row to missing state")
    _assert_matches_full_reload(project, codec_root)

    project.apply_unit_edits(((deleted, "Inserted refresh test", None),))
    inserted_result = project.save()
    project.reload_saved_files(inserted_result.changed_files)
    inserted = project.unit_by_uid(deleted.uid)
    if inserted is None or inserted.ref.target_row is None or inserted.current_text != "Inserted refresh test":
        raise AssertionError("changed-file refresh did not bind a newly inserted DBT row")
    _assert_matches_full_reload(project, codec_root)


def _assert_matches_full_reload(project: Project, codec_root: Path) -> None:
    reloaded = Project.load(project.root, project.language, codec_root=codec_root)
    current = [
        (unit.uid, unit.current_text, unit.display_status(), unit.ref.target_row is not None)
        for unit in project.units
    ]
    expected = [
        (unit.uid, unit.current_text, unit.display_status(), unit.ref.target_row is not None)
        for unit in reloaded.units
    ]
    if current != expected:
        raise AssertionError("changed-file refresh diverged from a full project reload")
