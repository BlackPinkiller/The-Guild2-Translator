from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Protocol


IMPORT_MODE_KEYED = "keyed"
IMPORT_MODE_TRANSLATIONS = "translations"

IMPORT_POLICY_EMPTY = "empty"
IMPORT_POLICY_OVERWRITE = "overwrite"

OUTCOME_UPDATE = "update"
OUTCOME_SAME = "same"
OUTCOME_EXISTING = "existing"
OUTCOME_EMPTY = "empty"
OUTCOME_NOT_FOUND = "not_found"
OUTCOME_SOURCE_MISMATCH = "source_mismatch"
OUTCOME_DUPLICATE = "duplicate"
OUTCOME_AMBIGUOUS = "ambiguous"

MAX_IMPORT_CHARS = 64 * 1024 * 1024
MAX_IMPORT_ROWS = 100_000


class ImportUnit(Protocol):
    uid: str
    label: str
    record_id: str
    file_rel: str
    source_text: str
    current_text: str
    pending_delete: bool


@dataclass(frozen=True)
class ParsedImportRow:
    line_number: int
    key: str
    source: str | None
    translation: str


@dataclass(frozen=True)
class ImportIssue:
    line_number: int
    code: str
    preview: str = ""


@dataclass(frozen=True)
class ParsedImportText:
    rows: tuple[ParsedImportRow, ...]
    blank_lines: int
    issues: tuple[ImportIssue, ...]


@dataclass(frozen=True)
class PlannedImportRow:
    row: ParsedImportRow
    outcome: str
    unit_uid: str = ""
    current_text: str = ""


@dataclass(frozen=True)
class TextImportPlan:
    rows: tuple[PlannedImportRow, ...]
    blank_lines: int
    issues: tuple[ImportIssue, ...]

    @property
    def updates(self) -> tuple[PlannedImportRow, ...]:
        return tuple(row for row in self.rows if row.outcome == OUTCOME_UPDATE)

    @property
    def problem_count(self) -> int:
        problem_outcomes = {
            OUTCOME_NOT_FOUND,
            OUTCOME_SOURCE_MISMATCH,
            OUTCOME_DUPLICATE,
            OUTCOME_AMBIGUOUS,
        }
        return len(self.issues) + sum(row.outcome in problem_outcomes for row in self.rows)

    @property
    def skipped_count(self) -> int:
        skipped_outcomes = {OUTCOME_SAME, OUTCOME_EXISTING, OUTCOME_EMPTY}
        return self.blank_lines + sum(row.outcome in skipped_outcomes for row in self.rows)

    def outcome_counts(self) -> Counter[str]:
        return Counter(row.outcome for row in self.rows)


def parse_import_text(text: str, mode: str) -> ParsedImportText:
    if len(text) > MAX_IMPORT_CHARS:
        return ParsedImportText((), 0, (ImportIssue(0, "too_large"),))
    rows: list[ParsedImportRow] = []
    issues: list[ImportIssue] = []
    blank_lines = 0
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            blank_lines += 1
            continue
        if len(rows) + len(issues) >= MAX_IMPORT_ROWS:
            issues.append(ImportIssue(line_number, "too_many_rows"))
            break
        fields = line.split("\t")
        if mode == IMPORT_MODE_KEYED:
            if len(fields) == 3:
                key, source, translation = fields
            elif len(fields) == 2:
                key, translation = fields
                source = None
            else:
                issues.append(ImportIssue(line_number, "column_count", line[:160]))
                continue
            key = key.strip()
            if not key:
                issues.append(ImportIssue(line_number, "missing_key", line[:160]))
                continue
        elif mode == IMPORT_MODE_TRANSLATIONS:
            if len(fields) != 1:
                issues.append(ImportIssue(line_number, "translation_has_tab", line[:160]))
                continue
            key = ""
            source = None
            translation = line
        else:
            raise ValueError(f"unsupported text import mode: {mode}")
        rows.append(ParsedImportRow(line_number, key, source, translation))
    return ParsedImportText(tuple(rows), blank_lines, tuple(issues))


def build_import_plan(
    parsed: ParsedImportText,
    units: Iterable[ImportUnit],
    selected_units: Iterable[ImportUnit],
    *,
    mode: str,
    policy: str,
    allow_empty: bool,
) -> TextImportPlan:
    all_units = tuple(units)
    selected = tuple(selected_units)
    if policy not in {IMPORT_POLICY_EMPTY, IMPORT_POLICY_OVERWRITE}:
        raise ValueError(f"unsupported text import policy: {policy}")
    if mode == IMPORT_MODE_TRANSLATIONS:
        if len(parsed.rows) != len(selected):
            issue = ImportIssue(0, "selection_count", f"{len(parsed.rows)}\t{len(selected)}")
            return TextImportPlan((), parsed.blank_lines, (*parsed.issues, issue))
        planned = tuple(
            _plan_for_unit(row, unit, policy=policy, allow_empty=allow_empty)
            for row, unit in zip(parsed.rows, selected)
        )
        return TextImportPlan(planned, parsed.blank_lines, parsed.issues)
    if mode != IMPORT_MODE_KEYED:
        raise ValueError(f"unsupported text import mode: {mode}")

    units_by_key: dict[str, list[ImportUnit]] = defaultdict(list)
    for unit in all_units:
        key = unit.label or unit.record_id or unit.file_rel
        if key:
            units_by_key[key].append(unit)
    input_counts = Counter(row.key for row in parsed.rows)
    planned_rows: list[PlannedImportRow] = []
    for row in parsed.rows:
        if input_counts[row.key] > 1:
            planned_rows.append(PlannedImportRow(row, OUTCOME_DUPLICATE))
            continue
        candidates = units_by_key.get(row.key, ())
        if not candidates:
            planned_rows.append(PlannedImportRow(row, OUTCOME_NOT_FOUND))
            continue
        if row.source is not None:
            source_matches = [unit for unit in candidates if unit.source_text == row.source]
            if not source_matches:
                planned_rows.append(PlannedImportRow(row, OUTCOME_SOURCE_MISMATCH))
                continue
            candidates = source_matches
        if len(candidates) != 1:
            planned_rows.append(PlannedImportRow(row, OUTCOME_AMBIGUOUS))
            continue
        planned_rows.append(
            _plan_for_unit(row, candidates[0], policy=policy, allow_empty=allow_empty)
        )
    return TextImportPlan(tuple(planned_rows), parsed.blank_lines, parsed.issues)


def _plan_for_unit(
    row: ParsedImportRow,
    unit: ImportUnit,
    *,
    policy: str,
    allow_empty: bool,
) -> PlannedImportRow:
    if not row.translation and not allow_empty:
        outcome = OUTCOME_EMPTY
    elif row.translation == unit.current_text and not unit.pending_delete:
        outcome = OUTCOME_SAME
    elif policy == IMPORT_POLICY_EMPTY and unit.current_text:
        outcome = OUTCOME_EXISTING
    else:
        outcome = OUTCOME_UPDATE
    return PlannedImportRow(row, outcome, unit.uid, unit.current_text)
