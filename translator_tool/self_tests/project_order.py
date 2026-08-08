from __future__ import annotations

from pathlib import Path
import tempfile

from ..project import Project, STATUS_EXTRA


def _dbt(column: str, rows: tuple[tuple[int, str, str], ...]) -> str:
    body = "".join(f'{row_id}     "{label}"   "{text}"   |\n' for row_id, label, text in rows)
    return (
        'Table Description:\n'
        f'"id" INT  0   |"label" STRING  0   |"{column}" STRING  0   |\n'
        '\nData:\n'
        f'{body}'
    )


def assert_missing_translations_follow_source_order() -> None:
    source_rows = (
        (10, "FIRST", "First"),
        (900, "NEW_MIDDLE", "New middle"),
        (20, "LAST", "Last"),
    )
    target_rows = (
        (10, "FIRST", "甲"),
        (20, "LAST", "乙"),
        (5, "TARGET_ONLY", "额外"),
    )
    with tempfile.TemporaryDirectory(prefix="translator_tool_order_") as raw_temp:
        root = Path(raw_temp)
        languages = root / "languages"
        target = languages / "#chinese"
        target.mkdir(parents=True)
        (languages / "Order.dbt").write_text(_dbt("english", source_rows), encoding="utf-8")
        (target / "Order.dbt").write_text(_dbt("chinese", target_rows), encoding="utf-8")

        project = Project.load(root, "#chinese", enable_codec=False)
        units = [unit for unit in project.units if unit.file_rel == "Order.dbt"]
        source_labels = [unit.label for unit in units if unit.ref.source_row is not None]
        if source_labels != ["FIRST", "NEW_MIDDLE", "LAST"]:
            raise AssertionError(f"missing translation moved out of source order: {source_labels!r}")
        if units[-1].filter_status() != STATUS_EXTRA or units[-1].label != "TARGET_ONLY":
            raise AssertionError("target-only entry was not kept after source-backed entries")
