from __future__ import annotations

from types import SimpleNamespace

from ..text_import import (
    IMPORT_MODE_KEYED,
    IMPORT_MODE_TRANSLATIONS,
    IMPORT_POLICY_EMPTY,
    IMPORT_POLICY_OVERWRITE,
    OUTCOME_AMBIGUOUS,
    OUTCOME_DUPLICATE,
    OUTCOME_EMPTY,
    OUTCOME_EXISTING,
    OUTCOME_NOT_FOUND,
    OUTCOME_SAME,
    OUTCOME_SOURCE_MISMATCH,
    OUTCOME_UPDATE,
    build_import_plan,
    parse_import_text,
)


def _unit(
    uid: str,
    key: str,
    source: str,
    translation: str = "",
    *,
    pending_delete: bool = False,
):
    return SimpleNamespace(
        uid=uid,
        label=key,
        record_id="",
        file_rel="Text.dbt",
        source_text=source,
        current_text=translation,
        pending_delete=pending_delete,
    )


def assert_text_import_planning_is_safe_and_lightweight() -> None:
    units = (
        _unit("one", "_ONE_+0", "One"),
        _unit("two", "_TWO_+0", "Two", "已有"),
        _unit("three", "_THREE_+0", "Three", "相同"),
    )
    parsed = parse_import_text(
        "_ONE_+0\tOne\t一\n\n_TWO_+0\t二\n_THREE_+0\tThree\t相同\n",
        IMPORT_MODE_KEYED,
    )
    if parsed.blank_lines != 1 or parsed.issues or len(parsed.rows) != 3:
        raise AssertionError("keyed import did not parse two/three-column rows and skip blank lines")
    plan = build_import_plan(
        parsed,
        units,
        (),
        mode=IMPORT_MODE_KEYED,
        policy=IMPORT_POLICY_EMPTY,
        allow_empty=False,
    )
    if [row.outcome for row in plan.rows] != [OUTCOME_UPDATE, OUTCOME_EXISTING, OUTCOME_SAME]:
        raise AssertionError("keyed import did not preserve the fill-empty overwrite policy")
    if plan.skipped_count != 3 or plan.problem_count or len(plan.updates) != 1:
        raise AssertionError("keyed import summary counts are inconsistent")
    if units[0].current_text or units[1].current_text != "已有":
        raise AssertionError("building an import preview mutated project-like units")

    overwrite = build_import_plan(
        parsed,
        units,
        (),
        mode=IMPORT_MODE_KEYED,
        policy=IMPORT_POLICY_OVERWRITE,
        allow_empty=False,
    )
    if [row.unit_uid for row in overwrite.updates] != ["one", "two"]:
        raise AssertionError("overwrite import did not target the expected stable unit IDs")

    problems = parse_import_text(
        "_MISSING_+0\tMissing\t缺失\n"
        "_ONE_+0\tWrong source\t一\n"
        "_TWO_+0\tTwo\t\n"
        "_DUP_+0\tA\n"
        "_DUP_+0\tB\n"
        "bad\tcolumns\there\ttoo many\n",
        IMPORT_MODE_KEYED,
    )
    problem_plan = build_import_plan(
        problems,
        units,
        (),
        mode=IMPORT_MODE_KEYED,
        policy=IMPORT_POLICY_OVERWRITE,
        allow_empty=False,
    )
    outcomes = [row.outcome for row in problem_plan.rows]
    if outcomes != [
        OUTCOME_NOT_FOUND,
        OUTCOME_SOURCE_MISMATCH,
        OUTCOME_EMPTY,
        OUTCOME_DUPLICATE,
        OUTCOME_DUPLICATE,
    ]:
        raise AssertionError("keyed import did not classify unsafe rows without applying them")
    if len(problem_plan.issues) != 1 or problem_plan.problem_count != 5:
        raise AssertionError("keyed import problem counts did not include format errors")

    ambiguous_units = (*units, _unit("one-copy", "_ONE_+0", "One"))
    ambiguous = build_import_plan(
        parse_import_text("_ONE_+0\t一", IMPORT_MODE_KEYED),
        ambiguous_units,
        (),
        mode=IMPORT_MODE_KEYED,
        policy=IMPORT_POLICY_OVERWRITE,
        allow_empty=False,
    )
    if ambiguous.rows[0].outcome != OUTCOME_AMBIGUOUS:
        raise AssertionError("a key shared by multiple units was imported without disambiguation")

    translations = parse_import_text("甲\n\n乙", IMPORT_MODE_TRANSLATIONS)
    positional = build_import_plan(
        translations,
        units,
        (units[0], units[1]),
        mode=IMPORT_MODE_TRANSLATIONS,
        policy=IMPORT_POLICY_OVERWRITE,
        allow_empty=False,
    )
    if [row.unit_uid for row in positional.updates] != ["one", "two"]:
        raise AssertionError("translation-only import did not preserve selected unit order")
    mismatch = build_import_plan(
        translations,
        units,
        (units[0],),
        mode=IMPORT_MODE_TRANSLATIONS,
        policy=IMPORT_POLICY_OVERWRITE,
        allow_empty=False,
    )
    if mismatch.rows or not any(issue.code == "selection_count" for issue in mismatch.issues):
        raise AssertionError("translation-only import accepted a selection count mismatch")

    invalid = parse_import_text("\tmissing key\nonly one column", IMPORT_MODE_KEYED)
    if len(invalid.issues) != 2 or invalid.rows:
        raise AssertionError("invalid keyed import rows were accepted")

    pending = _unit("pending", "_PENDING_+0", "Pending", "待删", pending_delete=True)
    pending_plan = build_import_plan(
        parse_import_text("_PENDING_+0\tPending\t待删", IMPORT_MODE_KEYED),
        (pending,),
        (),
        mode=IMPORT_MODE_KEYED,
        policy=IMPORT_POLICY_OVERWRITE,
        allow_empty=False,
    )
    if pending_plan.rows[0].outcome != OUTCOME_UPDATE:
        raise AssertionError("import did not clear a pending-delete state when restoring its translation")
