from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable

from .cache import ignored_uids, set_ignored_many
from .codec_adapter import CodecError, Guild2Codec, load_codec_for_language
from .format_io import (
    DbtDocument,
    DbtRow,
    PlainTextDocument,
    load_dbt,
    load_plain_text,
    load_plain_text_bytes,
    make_virtual_translation_dbt,
    make_inserted_line,
    matching_source_field,
    row_key,
    translatable_fields,
)
from .i18n import translate
from .validation import ValidationIssue, issue_summary, validate_translation
from .validation import normalize_color_token_spacing


STATUS_MISSING_ROW = "译文缺行"
STATUS_EMPTY = "译文为空"
STATUS_SAME = "未翻译(同原文)"
STATUS_TRANSLATED = "已翻译"
STATUS_MODIFIED = "已修改"
STATUS_REVIEW = "需审核"
STATUS_EXTRA = "译文多余"
STATUS_TRANSLATION_ONLY = "仅译文文件"
STATUS_IGNORED = "无需翻译"
STATUS_PENDING_DELETE = "待删除"
MISSING_WORK_STATUSES = {STATUS_MISSING_ROW, STATUS_EMPTY, STATUS_SAME}
NON_TRANSLATION_DBT_FILES = {"tables.dbt"}
# Internal compatibility switch.  Other game adapters can disable this until
# they provide a font/character codec with equivalent coverage.
ENABLE_FONT_GLYPH_VALIDATION = True


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
    deleted_units: tuple["TranslationUnit", ...] = ()


@dataclass
class UnitRef:
    kind: str
    target_doc: DbtDocument | PlainTextDocument
    source_doc: DbtDocument | PlainTextDocument | None = None
    source_row: DbtRow | None = None
    target_row: DbtRow | None = None
    suggested_row: DbtRow | None = None
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
    font_codec: Guild2Codec | None = field(default=None, repr=False)
    edited_text: str | None = None
    ignored: bool = False
    needs_review: bool = False
    pending_delete: bool = False

    @property
    def current_text(self) -> str:
        return self.translate_text if self.edited_text is None else self.edited_text

    @property
    def is_dirty(self) -> bool:
        # A label fallback is staged as a new source-row translation. It must
        # be saved into the source row's own ID, label, and physical position.
        return self.pending_delete or self.needs_review or (self.edited_text is not None and self.edited_text != self.translate_text)

    def set_text(self, text: str) -> None:
        # Label fallback is a one-time import hint. Once a translator touches
        # the text, their edit is the review decision and the hint is cleared.
        if self.needs_review and text != self.current_text:
            self.needs_review = False
        if self.pending_delete and text != self.current_text:
            self.pending_delete = False
        self.edited_text = None if text == self.translate_text else text

    def set_pending_delete(self, pending_delete: bool) -> None:
        self.pending_delete = pending_delete

    def can_delete_translation(self) -> bool:
        if self.ref.kind == "dbt":
            return self.ref.target_row is not None or self.ref.suggested_row is not None
        if self.ref.kind == "text":
            return self.ref.target_doc.path.exists() or self.status == STATUS_TRANSLATION_ONLY
        return False

    def current_status(self) -> str:
        """Classify the visible translation text, including unsaved edits."""
        if self.pending_delete:
            return STATUS_PENDING_DELETE
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
        if self.pending_delete:
            return self.initial_issues
        if self.ignored and not self.is_dirty:
            return self.initial_issues
        dbt_field = self.ref.kind == "dbt"
        return self.initial_issues + validate_translation(
            self.source_text,
            self.current_text,
            dbt_field=dbt_field,
            font_codec=self.font_codec if ENABLE_FONT_GLYPH_VALIDATION else None,
        )

    def issue_text(self) -> str:
        return issue_summary(self.issues())

    def display_status(self) -> str:
        if self.pending_delete:
            return STATUS_PENDING_DELETE
        # A label-based match can preserve useful existing translations across
        # modded files whose numeric IDs have shifted. It remains visible as a
        # review item while retaining its translated filter classification.
        if self.needs_review:
            return STATUS_REVIEW
        if self.is_dirty and self.status == STATUS_TRANSLATED:
            return STATUS_MODIFIED
        return self.current_status()

    def filter_status(self) -> str:
        return self.current_status()


@dataclass
class Project:
    root: Path
    languages_root: Path
    language: str
    codec: Guild2Codec | None
    source_docs: dict[str, DbtDocument]
    source_text_docs: dict[str, PlainTextDocument]
    target_dbt_docs: dict[str, DbtDocument]
    target_text_docs: dict[str, PlainTextDocument]
    units: list[TranslationUnit]
    source_order: dict[str, dict[tuple[int, str], int]]
    unit_index: dict[str, TranslationUnit]
    insertion_anchors: dict[str, dict[tuple[int, str], int | None]]

    @classmethod
    def load(cls, root: Path, language: str = "#chinese", codec_root: Path | None = None) -> "Project":
        root = root.resolve()
        languages_root = root / "languages"
        if not languages_root.exists():
            raise ProjectError(f"languages directory not found: {languages_root}")
        codec = load_codec_for_language(codec_root if codec_root is not None else root, language)

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
                target_doc = make_virtual_translation_dbt(source_doc, language_root / file_name, language)
                target_dbt_docs[file_name] = target_doc
            order = {row_key(file_name, row): index for index, row in enumerate(source_doc.rows)}
            source_order[file_name] = order
            units.extend(
                build_dbt_units(
                    file_name,
                    source_doc,
                    target_doc,
                    codec,
                    order,
                    label_match_first=root.name.casefold() != "vanilla",
                )
            )

        for file_rel, source_text_doc in source_text_docs.items():
            target_text_doc = target_text_docs.get(file_rel)
            if target_text_doc is None:
                target_text_doc = load_plain_text_bytes(language_root / file_rel, b"")
                target_text_doc.profile = replace(
                    source_text_doc.profile,
                    path=target_text_doc.path,
                    sha256=target_text_doc.profile.sha256,
                )
                target_text_docs[file_rel] = target_text_doc
            else:
                target_text_doc.profile = replace(
                    source_text_doc.profile,
                    path=target_text_doc.path,
                    sha256=target_text_doc.profile.sha256,
                )
            units.append(build_plain_text_unit(file_rel, target_text_doc, source_text_doc))

        for file_rel, text_doc in target_text_docs.items():
            if file_rel in source_text_docs:
                continue
            units.append(build_plain_text_unit(file_rel, text_doc, None))
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

        unit_index = {unit.uid: unit for unit in units}
        insertion_anchors = {
            file_name: _build_insertion_anchors(file_name, order, target_dbt_docs[file_name])
            for file_name, order in source_order.items()
            if file_name in target_dbt_docs
        }

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
            unit_index=unit_index,
            insertion_anchors=insertion_anchors,
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
        return self.unit_index.get(uid)

    def dirty_units(self) -> list[TranslationUnit]:
        return [unit for unit in self.units if unit.is_dirty]

    def dirty_count(self) -> int:
        return sum(unit.is_dirty for unit in self.units)

    def has_dirty_units(self) -> bool:
        return any(unit.is_dirty for unit in self.units)

    def set_unit_ignored(self, unit: TranslationUnit, ignored: bool) -> None:
        self.set_units_ignored((unit,), ignored)

    def set_units_ignored(self, units: Iterable[TranslationUnit], ignored: bool) -> None:
        selected = tuple(units)
        for unit in selected:
            unit.ignored = ignored
        if selected:
            set_ignored_many(self.root, self.language, tuple(unit.uid for unit in selected), ignored)

    def save(
        self, units: Iterable[TranslationUnit] | None = None, *, auto_space_before_color_tokens: bool = False
    ) -> SaveResult:
        supplied = tuple(units) if units is not None else None
        requested = list(self.dirty_units() if supplied is None else [unit for unit in supplied if unit.is_dirty])
        deleted_units = [unit for unit in requested if unit.pending_delete]
        touched_docs: dict[Path, DbtDocument | PlainTextDocument] = {}
        deleted_paths: set[Path] = set()
        errors: list[str] = []
        for unit in deleted_units:
            if not unit.can_delete_translation():
                errors.append(translate("project.save.no_deletable_row", file=unit.file_rel, record_id=unit.record_id))
                continue
            ref = unit.ref
            if ref.kind == "dbt":
                assert isinstance(ref.target_doc, DbtDocument)
                row = ref.target_row or ref.suggested_row
                assert row is not None
                row.delete()
                touched_docs[ref.target_doc.path] = ref.target_doc
            elif ref.kind == "text":
                deleted_paths.add(ref.target_doc.path)
        selected = [unit for unit in requested if not unit.pending_delete]
        if errors:
            raise SaveValidationError(errors)
        if not selected and not touched_docs and not deleted_paths:
            return SaveResult((), (), tuple(deleted_units))

        prepared_values: dict[str, str] = {}
        for unit in selected:
            blocking = [issue.message for issue in unit.issues() if issue.blocks_save]
            if blocking:
                errors.append(f"{unit.file_rel} #{unit.record_id} {unit.field_name}: {'; '.join(blocking)}")
                continue
            text_to_save = (
                normalize_color_token_spacing(unit.current_text)
                if auto_space_before_color_tokens
                else unit.current_text
            )
            if unit.ref.kind == "text":
                prepared_values[unit.uid] = text_to_save
                continue
            try:
                prepared_values[unit.uid] = self.codec.encode(text_to_save) if self.codec is not None else text_to_save
            except CodecError as exc:
                errors.append(f"{unit.file_rel} #{unit.record_id} {unit.field_name}: {exc}")
        if errors:
            raise SaveValidationError(errors)

        missing_groups: dict[tuple[str, tuple[int, str]], list[TranslationUnit]] = {}
        for unit in selected:
            ref = unit.ref
            if ref.kind == "text":
                assert isinstance(ref.target_doc, PlainTextDocument)
                ref.target_doc.set_raw_text(prepared_values[unit.uid])
                touched_docs[ref.target_doc.path] = ref.target_doc
                continue
            if ref.target_row is not None:
                assert isinstance(ref.target_doc, DbtDocument)
                ref.target_row.set_raw(ref.target_field, prepared_values[unit.uid])
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
                values[unit.ref.target_field] = prepared_values[unit.uid]

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
        for path in sorted(deleted_paths, key=str):
            if path.exists():
                path.unlink()
                changed_files.append(path)
        for doc in sorted(touched_docs.values(), key=lambda item: str(item.path)):
            if doc.path in deleted_paths:
                continue
            new_bytes = doc.render_bytes()
            if new_bytes == doc.raw:
                continue
            atomic_write(doc.path, new_bytes)
            changed_files.append(doc.path)
        return SaveResult(tuple(changed_files), tuple(selected), tuple(deleted_units))

    def _insertion_line_index(self, file_rel: str, missing_key: tuple[int, str]) -> int | None:
        return self.insertion_anchors.get(file_rel, {}).get(missing_key)


def atomic_write(path: Path, data: bytes) -> None:
    """Replace a project file without ever exposing a partially written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _build_insertion_anchors(
    file_name: str, order_map: dict[tuple[int, str], int], target_doc: DbtDocument
) -> dict[tuple[int, str], int | None]:
    existing_rows = sorted(
        (
            (row_order, row.line_index)
            for row in target_doc.rows
            if (row_order := order_map.get(row_key(file_name, row))) is not None
        ),
        key=lambda item: item[0],
    )
    anchors: dict[tuple[int, str], int | None] = {}
    next_line_index: int | None = None
    cursor = len(existing_rows) - 1
    for key, source_row_order in sorted(order_map.items(), key=lambda item: item[1], reverse=True):
        while cursor >= 0 and existing_rows[cursor][0] > source_row_order:
            next_line_index = existing_rows[cursor][1]
            cursor -= 1
        anchors[key] = next_line_index
    return anchors


def build_dbt_units(
    file_name: str,
    source_doc: DbtDocument,
    target_doc: DbtDocument,
    codec: Guild2Codec | None,
    order: dict[tuple[int, str], int],
    *,
    label_match_first: bool = False,
) -> list[TranslationUnit]:
    units: list[TranslationUnit] = []
    target_index = target_doc.row_index
    target_by_label: dict[str, list[DbtRow]] = {}
    if label_match_first:
        for row in target_doc.rows:
            target_by_label.setdefault(row_key(file_name, row)[1], []).append(row)
    target_fields = translatable_fields(file_name, target_doc.string_columns)
    matched_target_keys: set[tuple[int, str]] = set()

    for source_row in source_doc.rows:
        key = row_key(file_name, source_row)
        label_matches = target_by_label.get(key[1], ()) if label_match_first and key[1] else ()
        target_row = target_index.get(key)
        label_row = label_matches[0] if len(label_matches) == 1 else None
        matched_by_label = label_row is not None and label_row.row_id != source_row.row_id
        # Label matching is only a translation suggestion. It never reuses
        # the legacy target row as the save target: saving inserts a new row
        # built from the source language's original layout and source key.
        translation_row = label_row if matched_by_label else target_row
        if target_row is not None:
            matched_target_keys.add(row_key(file_name, target_row))
        display_order = target_row.line_index if target_row is not None else source_row.line_index
        for field_order, target_field in enumerate(target_fields):
            source_field = matching_source_field(target_field, source_doc.string_columns)
            source_text = source_row.get(source_field)
            initial_issues: list[ValidationIssue] = []
            if source_text == "" and translation_row is None:
                translate_text = ""
                status = STATUS_IGNORED
            elif translation_row is None:
                translate_text = ""
                status = STATUS_MISSING_ROW
            else:
                raw_value = translation_row.get(target_field)
                if codec is None:
                    translate_text = raw_value
                else:
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
                        suggested_row=label_row if matched_by_label else None,
                        row_key=key,
                        source_field=source_field,
                        target_field=target_field,
                        source_order=order.get(key, -1),
                        display_order=display_order,
                        field_order=field_order,
                    ),
                    initial_issues=initial_issues,
                    font_codec=codec,
                    needs_review=matched_by_label,
                )
            )

    for key, target_row in target_index.items():
        if key in matched_target_keys:
            continue
        for field_order, target_field in enumerate(target_fields):
            raw_value = target_row.get(target_field)
            initial_issues = []
            if codec is None:
                translate_text = raw_value
            else:
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
                    font_codec=codec,
                )
            )
    return units


def build_plain_text_unit(
    file_rel: str,
    text_doc: PlainTextDocument,
    source_doc: PlainTextDocument | None,
) -> TranslationUnit:
    initial_issues: list[ValidationIssue] = []
    translate_text = text_doc.text
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
        font_codec=None,
    )
