from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .cache import ignored_uids, set_ignored_many
from .codec_adapter import CodecError, Guild2Codec, default_codec_path
from .format_io import (
    DbtDocument,
    DbtRow,
    PlainTextDocument,
    load_dbt,
    load_plain_text,
    make_inserted_line,
    matching_source_field,
    row_key,
    translatable_fields,
)
from .validation import ValidationIssue, issue_summary, validate_translation


STATUS_MISSING_ROW = "译文缺行"
STATUS_EMPTY = "译文为空"
STATUS_SAME = "未翻译(同原文)"
STATUS_TRANSLATED = "已翻译"
STATUS_EXTRA = "译文多余"
STATUS_TRANSLATION_ONLY = "仅译文文件"
STATUS_IGNORED = "无需翻译"
MISSING_WORK_STATUSES = {STATUS_MISSING_ROW, STATUS_EMPTY, STATUS_SAME}
NON_TRANSLATION_DBT_FILES = {"tables.dbt"}


class ProjectError(RuntimeError):
    pass


class SaveValidationError(ProjectError):
    def __init__(self, messages: list[str]) -> None:
        super().__init__("\n".join(messages))
        self.messages = messages


@dataclass(frozen=True)
class SaveResult:
    """The durable effects of a successful translation save."""

    changed_files: tuple[Path, ...]
    saved_units: tuple["TranslationUnit", ...]


@dataclass
class UnitRef:
    kind: str
    target_doc: DbtDocument | PlainTextDocument
    source_doc: DbtDocument | PlainTextDocument | None = None
    source_row: DbtRow | None = None
    target_row: DbtRow | None = None
    row_key: tuple[int, str] | None = None
    source_field: str = ""
    target_field: str = ""
    source_order: int = -1
    display_order: int = -1
    field_order: int = -1


@dataclass
class TranslationUnit:
    uid: str
    file_rel: str
    record_id: str
    label: str
    field_name: str
    source_text: str
    translate_text: str
    status: str
    ref: UnitRef
    initial_issues: list[ValidationIssue] = field(default_factory=list)
    edited_text: str | None = None
    ignored: bool = False

    @property
    def current_text(self) -> str:
        return self.translate_text if self.edited_text is None else self.edited_text

    @property
    def is_dirty(self) -> bool:
        return self.edited_text is not None and self.edited_text != self.translate_text

    def set_text(self, text: str) -> None:
        self.edited_text = None if text == self.translate_text else text

    def current_status(self) -> str:
        """Classify the visible translation text, including unsaved edits."""
        if self.ignored:
            return STATUS_IGNORED
        # These entries have no corresponding source entry, so an edit cannot
        # turn them into a regular translated source entry.
        if self.status in {STATUS_EXTRA, STATUS_TRANSLATION_ONLY}:
            return self.status
        if not self.source_text and self.status == STATUS_IGNORED:
            return STATUS_IGNORED
        if self.status == STATUS_MISSING_ROW and not self.is_dirty:
            return STATUS_MISSING_ROW
        if not self.current_text:
            return STATUS_EMPTY
        if self.current_text == self.source_text:
            return STATUS_SAME
        return STATUS_TRANSLATED

    def issues(self) -> list[ValidationIssue]:
        if self.ignored and not self.is_dirty:
            return self.initial_issues
        dbt_field = self.ref.kind == "dbt"
        return self.initial_issues + validate_translation(self.source_text, self.current_text, dbt_field=dbt_field)

    def issue_text(self) -> str:
        return issue_summary(self.issues())

    def display_status(self) -> str:
        return self.current_status()

    def filter_status(self) -> str:
        return self.current_status()


@dataclass
class Project:
    root: Path
    languages_root: Path
    language: str
    codec: Guild2Codec
    source_docs: dict[str, DbtDocument]
    source_text_docs: dict[str, PlainTextDocument]
    target_dbt_docs: dict[str, DbtDocument]
    target_text_docs: dict[str, PlainTextDocument]
    units: list[TranslationUnit]
    source_order: dict[str, dict[tuple[int, str], int]]

    @classmethod
    def load(cls, root: Path, language: str = "#chinese", codec_root: Path | None = None) -> "Project":
        root = root.resolve()
        languages_root = root / "languages"
        if not languages_root.exists():
            raise ProjectError(f"languages directory not found: {languages_root}")
        codec = Guild2Codec.load(default_codec_path(root, codec_root))

        source_docs: dict[str, DbtDocument] = {}
        file_order: dict[str, int] = {}
        for path in sorted(languages_root.glob("*.dbt")):
            if path.name.lower() in NON_TRANSLATION_DBT_FILES:
                continue
            source_docs[path.name] = load_dbt(path)
            file_order[path.name] = len(file_order)

        source_text_docs: dict[str, PlainTextDocument] = {}
        source_guides_root = languages_root / "Guides"
        if source_guides_root.exists():
            for path in sorted(source_guides_root.rglob("*.txt")):
                rel = Path("Guides") / path.relative_to(source_guides_root)
                source_text_docs[rel.as_posix()] = load_plain_text(path)
                file_order[rel.as_posix()] = len(file_order)

        language_root = languages_root / language
        if not language_root.exists():
            raise ProjectError(f"language directory not found: {language_root}")

        target_dbt_docs: dict[str, DbtDocument] = {}
        for path in sorted(language_root.glob("*.dbt")):
            if path.name.lower() in NON_TRANSLATION_DBT_FILES:
                continue
            target_dbt_docs[path.name] = load_dbt(path)

        target_text_docs: dict[str, PlainTextDocument] = {}
        for path in sorted(language_root.rglob("*.txt")):
            rel = path.relative_to(language_root).as_posix()
            target_text_docs[rel] = load_plain_text(path)

        source_order: dict[str, dict[tuple[int, str], int]] = {}
        units: list[TranslationUnit] = []
        for file_name, source_doc in source_docs.items():
            target_doc = target_dbt_docs.get(file_name)
            if target_doc is None:
                continue
            order = {row_key(file_name, row): index for index, row in enumerate(source_doc.rows)}
            source_order[file_name] = order
            units.extend(build_dbt_units(file_name, source_doc, target_doc, codec, order))

        for file_rel, source_text_doc in source_text_docs.items():
            target_text_doc = target_text_docs.get(file_rel)
            if target_text_doc is not None:
                units.append(build_plain_text_unit(file_rel, target_text_doc, codec, source_text_doc))

        for file_rel, text_doc in target_text_docs.items():
            if file_rel in source_text_docs:
                continue
            units.append(build_plain_text_unit(file_rel, text_doc, codec, None))
            file_order.setdefault(file_rel, len(file_order))

        # Do not rely on construction order: the table must always reflect the
        # original file and physical line order, including target-only rows.
        units.sort(
            key=lambda unit: (
                file_order.get(unit.file_rel, len(file_order)),
                unit.ref.display_order,
                unit.ref.field_order,
                unit.uid,
            )
        )

        ignored = ignored_uids(root, language)
        for unit in units:
            unit.ignored = unit.uid in ignored

        return cls(
            root=root,
            languages_root=languages_root,
            language=language,
            codec=codec,
            source_docs=source_docs,
            source_text_docs=source_text_docs,
            target_dbt_docs=target_dbt_docs,
            target_text_docs=target_text_docs,
            units=units,
            source_order=source_order,
        )

    @staticmethod
    def language_dirs(root: Path) -> list[str]:
        languages_root = root.resolve() / "languages"
        if not languages_root.exists():
            return []
        dirs = [path.name for path in sorted(languages_root.iterdir()) if path.is_dir() and path.name.startswith("#")]
        if "#chinese" in dirs:
            dirs.remove("#chinese")
            return ["#chinese", *dirs]
        return dirs

    def unit_by_uid(self, uid: str) -> TranslationUnit | None:
        for unit in self.units:
            if unit.uid == uid:
                return unit
        return None

    def dirty_units(self) -> list[TranslationUnit]:
        return [unit for unit in self.units if unit.is_dirty]

    def set_unit_ignored(self, unit: TranslationUnit, ignored: bool) -> None:
        self.set_units_ignored((unit,), ignored)

    def set_units_ignored(self, units: Iterable[TranslationUnit], ignored: bool) -> None:
        selected = tuple(units)
        for unit in selected:
            unit.ignored = ignored
        if selected:
            set_ignored_many(self.root, self.language, tuple(unit.uid for unit in selected), ignored)

    def save(self, units: Iterable[TranslationUnit] | None = None) -> SaveResult:
        selected = list(self.dirty_units() if units is None else [unit for unit in units if unit.is_dirty])
        if not selected:
            return SaveResult((), ())

        encoded_values: dict[str, str] = {}
        errors: list[str] = []
        for unit in selected:
            blocking = [issue.message for issue in unit.issues() if issue.blocks_save]
            if blocking:
                errors.append(f"{unit.file_rel} #{unit.record_id} {unit.field_name}: {'; '.join(blocking)}")
                continue
            try:
                encoded_values[unit.uid] = self.codec.encode(unit.current_text)
            except CodecError as exc:
                errors.append(f"{unit.file_rel} #{unit.record_id} {unit.field_name}: {exc}")
        if errors:
            raise SaveValidationError(errors)

        missing_groups: dict[tuple[str, tuple[int, str]], list[TranslationUnit]] = {}
        touched_docs: dict[Path, DbtDocument | PlainTextDocument] = {}
        for unit in selected:
            ref = unit.ref
            if ref.kind == "text":
                assert isinstance(ref.target_doc, PlainTextDocument)
                ref.target_doc.set_raw_text(encoded_values[unit.uid])
                touched_docs[ref.target_doc.path] = ref.target_doc
                continue
            if ref.target_row is not None:
                assert isinstance(ref.target_doc, DbtDocument)
                ref.target_row.set_raw(ref.target_field, encoded_values[unit.uid])
                touched_docs[ref.target_doc.path] = ref.target_doc
                continue
            if ref.row_key is None:
                errors.append(f"{unit.file_rel} #{unit.record_id}: internal error, missing row key")
                continue
            missing_groups.setdefault((unit.file_rel, ref.row_key), []).append(unit)

        def missing_order(item: tuple[tuple[str, tuple[int, str]], list[TranslationUnit]]) -> tuple[str, int]:
            (file_rel, missing_key), _grouped = item
            return (file_rel, self.source_order.get(file_rel, {}).get(missing_key, 2**31 - 1))

        for (file_rel, missing_key), grouped_units in sorted(missing_groups.items(), key=missing_order):
            target_doc = self.target_dbt_docs[file_rel]
            source_doc = self.source_docs[file_rel]
            source_row = grouped_units[0].ref.source_row
            if source_row is None:
                errors.append(f"{file_rel} #{missing_key[0]}: cannot create row without source")
                continue

            values: dict[str, str] = {}
            for field_name in translatable_fields(file_rel, target_doc.string_columns):
                source_field = matching_source_field(field_name, source_doc.string_columns)
                default_value = source_row.get(source_field)
                values[field_name] = default_value
            for unit in grouped_units:
                values[unit.ref.target_field] = encoded_values[unit.uid]

            try:
                new_line = make_inserted_line(source_row, source_doc, target_doc, values)
            except ValueError as exc:
                errors.append(str(exc))
                continue

            before_index = self._insertion_line_index(file_rel, missing_key)
            target_doc.insertions.append((before_index, new_line))
            touched_docs[target_doc.path] = target_doc

        if errors:
            raise SaveValidationError(errors)

        for doc in touched_docs.values():
            if isinstance(doc, DbtDocument) and doc.parse_errors:
                joined = "; ".join(doc.parse_errors[:5])
                errors.append(f"{doc.path.name}: DBT field parse error(s): {joined}")

        if errors:
            raise SaveValidationError(errors)

        changed_files: list[Path] = []
        for doc in sorted(touched_docs.values(), key=lambda item: str(item.path)):
            new_bytes = doc.render_bytes()
            if new_bytes == doc.raw:
                continue
            atomic_write(doc.path, new_bytes)
            changed_files.append(doc.path)
        return SaveResult(tuple(changed_files), tuple(selected))

    def _insertion_line_index(self, file_rel: str, missing_key: tuple[int, str]) -> int | None:
        order_map = self.source_order.get(file_rel, {})
        missing_order = order_map.get(missing_key)
        if missing_order is None:
            return None
        target_doc = self.target_dbt_docs[file_rel]
        candidate: tuple[int, int] | None = None
        for row in target_doc.rows:
            key = row_key(file_rel, row)
            row_order = order_map.get(key)
            if row_order is None or row_order <= missing_order:
                continue
            if candidate is None or row_order < candidate[0]:
                candidate = (row_order, row.line_index)
        return candidate[1] if candidate else None


def atomic_write(path: Path, data: bytes) -> None:
    """Replace a project file without ever exposing a partially written file."""
    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_bytes(data)
    try:
        import os

        os.replace(temp_path, path)
    except PermissionError:
        # The game can occasionally keep a language file open on Windows.
        path.write_bytes(data)
        try:
            temp_path.unlink()
        except OSError:
            pass


def build_dbt_units(
    file_name: str,
    source_doc: DbtDocument,
    target_doc: DbtDocument,
    codec: Guild2Codec,
    order: dict[tuple[int, str], int],
) -> list[TranslationUnit]:
    units: list[TranslationUnit] = []
    source_index = source_doc.row_index
    target_index = target_doc.row_index
    target_fields = translatable_fields(file_name, target_doc.string_columns)

    for source_row in source_doc.rows:
        key = row_key(file_name, source_row)
        target_row = target_index.get(key)
        display_order = target_row.line_index if target_row is not None else source_row.line_index
        for field_order, target_field in enumerate(target_fields):
            source_field = matching_source_field(target_field, source_doc.string_columns)
            source_text = source_row.get(source_field)
            initial_issues: list[ValidationIssue] = []
            if source_text == "" and target_row is None:
                translate_text = ""
                status = STATUS_IGNORED
            elif target_row is None:
                translate_text = ""
                status = STATUS_MISSING_ROW
            else:
                raw_value = target_row.get(target_field)
                try:
                    translate_text = codec.decode(raw_value)
                except CodecError as exc:
                    translate_text = raw_value
                    initial_issues.append(ValidationIssue("error", str(exc)))
                if source_text == "" and translate_text == "":
                    status = STATUS_IGNORED
                elif translate_text == "":
                    status = STATUS_EMPTY
                elif translate_text == source_text:
                    status = STATUS_SAME
                else:
                    status = STATUS_TRANSLATED
            units.append(
                TranslationUnit(
                    uid=f"dbt:{file_name}:{key[0]}:{key[1]}:{target_field}",
                    file_rel=file_name,
                    record_id=str(key[0]),
                    label=key[1],
                    field_name=target_field,
                    source_text=source_text,
                    translate_text=translate_text,
                    status=status,
                    ref=UnitRef(
                        kind="dbt",
                        source_doc=source_doc,
                        target_doc=target_doc,
                        source_row=source_row,
                        target_row=target_row,
                        row_key=key,
                        source_field=source_field,
                        target_field=target_field,
                        source_order=order.get(key, -1),
                        display_order=display_order,
                        field_order=field_order,
                    ),
                    initial_issues=initial_issues,
                )
            )

    for key, target_row in target_index.items():
        if key in source_index:
            continue
        for field_order, target_field in enumerate(target_fields):
            raw_value = target_row.get(target_field)
            initial_issues = []
            try:
                translate_text = codec.decode(raw_value)
            except CodecError as exc:
                translate_text = raw_value
                initial_issues.append(ValidationIssue("error", str(exc)))
            units.append(
                TranslationUnit(
                    uid=f"extra:{file_name}:{key[0]}:{key[1]}:{target_field}",
                    file_rel=file_name,
                    record_id=str(key[0]),
                    label=key[1],
                    field_name=target_field,
                    source_text="",
                    translate_text=translate_text,
                    status=STATUS_EXTRA,
                    ref=UnitRef(
                        kind="dbt",
                        source_doc=source_doc,
                        target_doc=target_doc,
                        source_row=None,
                        target_row=target_row,
                        row_key=key,
                        source_field="",
                        target_field=target_field,
                        display_order=target_row.line_index,
                        field_order=field_order,
                    ),
                    initial_issues=initial_issues,
                )
            )
    return units


def build_plain_text_unit(
    file_rel: str,
    text_doc: PlainTextDocument,
    codec: Guild2Codec,
    source_doc: PlainTextDocument | None,
) -> TranslationUnit:
    initial_issues: list[ValidationIssue] = []
    try:
        translate_text = codec.decode(text_doc.text)
    except CodecError as exc:
        translate_text = text_doc.text
        initial_issues.append(ValidationIssue("error", str(exc)))
    source_text = source_doc.text if source_doc is not None else ""
    if source_doc is None:
        status = STATUS_TRANSLATION_ONLY
    elif source_text == "" and translate_text == "":
        status = STATUS_IGNORED
    elif translate_text == "":
        status = STATUS_EMPTY
    elif translate_text == source_text:
        status = STATUS_SAME
    else:
        status = STATUS_TRANSLATED
    return TranslationUnit(
        uid=f"text:{file_rel}",
        file_rel=file_rel,
        record_id="",
        label=file_rel,
        field_name="body",
        source_text=source_text,
        translate_text=translate_text,
        status=status,
        ref=UnitRef(kind="text", target_doc=text_doc, source_doc=source_doc, display_order=0, field_order=0),
        initial_issues=initial_issues,
    )
