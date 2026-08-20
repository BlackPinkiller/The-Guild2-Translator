from __future__ import annotations

from pathlib import Path
import tempfile

from ..project import Project, TODO_REASON_EMPTY, TODO_REASON_MISSING_ROW


def _dbt(column: str, rows: tuple[tuple[int, str, str], ...]) -> str:
    body = "".join(
        f'{row_id}     "{label}"   "{text}"   |\n'
        for row_id, label, text in rows
    )
    return (
        "Table Description:\n"
        f'"id" INT  0   |"label" STRING  0   |"{column}" STRING  0   |\n'
        "\nData:\n"
        f"{body}"
    )


def assert_missing_rows_do_not_report_translation_format_errors() -> None:
    source_rows = (
        (1, "MISSING", "Missing %1SN"),
        (2, "EMPTY", "Empty %1SN"),
    )
    target_rows = ((2, "EMPTY", ""),)
    with tempfile.TemporaryDirectory(prefix="translator_tool_validation_") as raw_temp:
        root = Path(raw_temp)
        languages = root / "languages"
        target = languages / "#chinese"
        target.mkdir(parents=True)
        (languages / "Validation.dbt").write_text(
            _dbt("english", source_rows),
            encoding="utf-8",
        )
        (target / "Validation.dbt").write_text(
            _dbt("chinese", target_rows),
            encoding="utf-8",
        )

        project = Project.load(root, "#chinese", enable_codec=False)
        missing = next(unit for unit in project.units if unit.label == "MISSING")
        empty = next(unit for unit in project.units if unit.label == "EMPTY")

        if missing.todo_reason != TODO_REASON_MISSING_ROW or not missing.is_missing_translation:
            raise AssertionError("the missing-row fixture was not recognized as untranslated")
        if missing.active_issues():
            raise AssertionError(
                "an untranslated row reported translation format errors: "
                f"{missing.active_issues()!r}"
            )
        if empty.todo_reason != TODO_REASON_EMPTY or empty.is_missing_translation:
            raise AssertionError("an existing empty translation row was treated as missing")
        if not empty.active_issues():
            raise AssertionError("an existing empty translation row lost format validation")

        project.apply_unit_edits(((missing, "已添加译文", None),))
        if missing.is_missing_translation:
            raise AssertionError("an unsaved translation remained classified as missing")
        if not missing.active_issues():
            raise AssertionError("a newly entered translation did not enable format validation")
