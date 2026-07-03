from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from pathlib import Path


STRING_COLUMN_RE = re.compile(r'"([^"]+)"\s+(INT|STRING)', re.IGNORECASE)
ROW_ID_RE = re.compile(r"\s*(\d+)\b")


@dataclass(frozen=True)
class FileProfile:
    path: Path
    encoding: str
    newline: str
    final_newline: bool
    sha256: str


@dataclass(frozen=True)
class QuotedField:
    name: str
    start: int
    end: int
    value: str


@dataclass
class DbtRow:
    line_index: int
    line_end_index: int
    row_id: int
    original_line: str
    fields: list[QuotedField]
    parse_error: str = ""
    updates: dict[str, str] = field(default_factory=dict)
    deleted: bool = False

    @property
    def string_names(self) -> list[str]:
        return [item.name for item in self.fields]

    def get(self, field_name: str, default: str = "") -> str:
        if field_name in self.updates:
            return self.updates[field_name]
        for item in self.fields:
            if item.name == field_name:
                return item.value
        return default

    def set_raw(self, field_name: str, value: str) -> None:
        if field_name not in self.string_names:
            raise KeyError(f"field not found in row {self.row_id}: {field_name}")
        self.updates[field_name] = value

    def delete(self) -> None:
        self.deleted = True

    def render(self) -> str:
        if self.deleted:
            return ""
        if not self.updates:
            return self.original_line
        line = self.original_line
        for item in sorted(self.fields, key=lambda field: field.start, reverse=True):
            if item.name in self.updates:
                line = line[: item.start] + self.updates[item.name] + line[item.end :]
        return line


@dataclass
class DbtDocument:
    path: Path
    raw: bytes
    text: str
    profile: FileProfile
    lines: list[str]
    columns: list[tuple[str, str]]
    string_columns: list[str]
    data_line_index: int
    rows: list[DbtRow]
    parse_errors: list[str] = field(default_factory=list)
    insertions: list[tuple[int | None, str]] = field(default_factory=list)

    @property
    def rows_by_line(self) -> dict[int, DbtRow]:
        return {row.line_index: row for row in self.rows}

    @property
    def row_index(self) -> dict[tuple[int, str], DbtRow]:
        return {row_key(self.path.name, row): row for row in self.rows}

    def render_text(self) -> str:
        lines: list[str] = []
        pending: dict[int, list[str]] = {}
        append: list[str] = []
        for before_index, line in self.insertions:
            if before_index is None:
                append.append(line)
            else:
                pending.setdefault(before_index, []).append(line)

        rows_by_line = self.rows_by_line
        skip_until = -1
        for index, line in enumerate(self.lines):
            if index <= skip_until:
                continue
            lines.extend(pending.pop(index, []))
            row = rows_by_line.get(index)
            if row:
                lines.append(row.render())
                skip_until = row.line_end_index
            else:
                lines.append(line)
        for index in sorted(pending):
            lines.extend(pending[index])
        if append and lines:
            _body, ending = split_line_ending(lines[-1])
            if not ending:
                lines[-1] = lines[-1] + self.profile.newline
        lines.extend(append)
        return "".join(lines)

    def render_bytes(self) -> bytes:
        return encode_text(self.render_text(), self.profile.encoding)

    def is_changed(self) -> bool:
        return self.render_bytes() != self.raw


@dataclass
class PlainTextDocument:
    path: Path
    raw: bytes
    text: str
    profile: FileProfile
    replacement_text: str | None = None

    def set_raw_text(self, text: str) -> None:
        self.replacement_text = text

    def render_text(self) -> str:
        text = self.text if self.replacement_text is None else self.replacement_text
        return normalize_plain_text_layout(text, self.profile.newline, self.profile.final_newline)

    def render_bytes(self) -> bytes:
        return encode_text(self.render_text(), self.profile.encoding)

    def is_changed(self) -> bool:
        return self.render_bytes() != self.raw


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def detect_encoding(raw: bytes) -> str:
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return "utf-16"
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    return "utf-8"


def decode_bytes(raw: bytes, encoding: str) -> str:
    return raw.decode(encoding)


def encode_text(text: str, encoding: str) -> bytes:
    return text.encode(encoding)


def detect_newline(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    if crlf >= lf and crlf >= cr and crlf:
        return "\r\n"
    if cr >= lf and cr:
        return "\r"
    return "\n"


def split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def normalize_plain_text_layout(text: str, newline: str, final_newline: bool) -> str:
    if not text:
        return ""
    parts = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if text.endswith(("\r\n", "\n", "\r")):
        parts = parts[:-1]
    rendered = newline.join(parts)
    if final_newline:
        return rendered + newline
    return rendered


def read_profile(path: Path) -> tuple[bytes, str, FileProfile]:
    return read_profile_bytes(path, path.read_bytes())


def read_profile_bytes(path: Path, raw: bytes) -> tuple[bytes, str, FileProfile]:
    encoding = detect_encoding(raw)
    text = decode_bytes(raw, encoding)
    profile = FileProfile(
        path=path,
        encoding=encoding,
        newline=detect_newline(text),
        final_newline=text.endswith(("\n", "\r")),
        sha256=sha256_bytes(raw),
    )
    return raw, text, profile


def load_plain_text(path: Path) -> PlainTextDocument:
    return load_plain_text_bytes(path, path.read_bytes())


def load_plain_text_bytes(path: Path, raw: bytes) -> PlainTextDocument:
    raw, text, profile = read_profile_bytes(path, raw)
    return PlainTextDocument(path=path, raw=raw, text=text, profile=profile)


def parse_columns(lines: list[str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for line in lines:
        if line.strip().lower().startswith("data:"):
            break
        for match in STRING_COLUMN_RE.finditer(line):
            found.append((match.group(1), match.group(2).upper()))
    return found


def parse_fields(line: str, string_columns: list[str]) -> tuple[list[QuotedField], str]:
    quote_positions = [match.start() for match in re.finditer('"', line)]
    if len(quote_positions) % 2:
        return [], "odd number of quotes"
    fields: list[QuotedField] = []
    for index in range(0, len(quote_positions), 2):
        column_index = index // 2
        if column_index >= len(string_columns):
            name = f"extra_{column_index + 1}"
        else:
            name = string_columns[column_index]
        start = quote_positions[index] + 1
        end = quote_positions[index + 1]
        fields.append(QuotedField(name=name, start=start, end=end, value=line[start:end]))
    if len(fields) != len(string_columns):
        return fields, f"expected {len(string_columns)} string fields, found {len(fields)}"
    return fields, ""


def is_complete_row(buffer: str) -> bool:
    return buffer.count('"') % 2 == 0 and buffer.rstrip().endswith("|")


def load_dbt(path: Path) -> DbtDocument:
    return load_dbt_bytes(path, path.read_bytes())


def load_dbt_bytes(path: Path, raw: bytes) -> DbtDocument:
    raw, text, profile = read_profile_bytes(path, raw)
    lines = text.splitlines(keepends=True)
    columns = parse_columns(lines)
    string_columns = [name for name, type_name in columns if type_name == "STRING"]
    data_line_index = -1
    for index, line in enumerate(lines):
        if line.strip().lower().startswith("data:"):
            data_line_index = index
            break

    rows: list[DbtRow] = []
    parse_errors: list[str] = []
    index = data_line_index + 1 if data_line_index >= 0 else 0
    while index < len(lines):
        line = lines[index]
        match = ROW_ID_RE.match(line)
        if not match:
            index += 1
            continue
        start_index = index
        buffer = line
        while not is_complete_row(buffer) and index + 1 < len(lines):
            index += 1
            buffer += lines[index]
        end_index = index
        row_id = int(match.group(1))
        fields, error = parse_fields(buffer, string_columns)
        if error:
            parse_errors.append(f"line {start_index + 1}: {error}")
        rows.append(
            DbtRow(
                line_index=start_index,
                line_end_index=end_index,
                row_id=row_id,
                original_line=buffer,
                fields=fields,
                parse_error=error,
            )
        )
        index += 1

    return DbtDocument(
        path=path,
        raw=raw,
        text=text,
        profile=profile,
        lines=lines,
        columns=columns,
        string_columns=string_columns,
        data_line_index=data_line_index,
        rows=rows,
        parse_errors=parse_errors,
    )


def key_field_name(file_name: str, string_columns: list[str]) -> str:
    lowered = {name.lower(): name for name in string_columns}
    if file_name.lower() == "tooltips.dbt" and "key" in lowered:
        return lowered["key"]
    for candidate in ("label", "key", "name"):
        if candidate in lowered:
            return lowered[candidate]
    return string_columns[0] if string_columns else ""


def row_key(file_name: str, row: DbtRow) -> tuple[int, str]:
    if file_name.lower() == "tables.dbt":
        return (row.row_id, "")
    key_name = key_field_name(file_name, row.string_names)
    return (row.row_id, row.get(key_name) if key_name else "")


def translatable_fields(file_name: str, string_columns: list[str]) -> list[str]:
    lowered = {name.lower(): name for name in string_columns}
    if file_name.lower() == "tooltips.dbt":
        return [lowered[name] for name in ("title", "description") if name in lowered]
    if "chinese" in lowered:
        return [lowered["chinese"]]
    if "english" in lowered:
        return [lowered["english"]]
    if "name" in lowered:
        return [lowered["name"]]
    if string_columns:
        return [string_columns[-1]]
    return []


def translation_string_column_name(language: str) -> str:
    stripped = language.lstrip("#").strip().casefold()
    return stripped or "translation"


def matching_source_field(target_field: str, source_columns: list[str]) -> str:
    lowered = {name.lower(): name for name in source_columns}
    target_lower = target_field.lower()
    if target_lower in lowered:
        return lowered[target_lower]
    if target_lower == "chinese" and "english" in lowered:
        return lowered["english"]
    if "english" in lowered:
        return lowered["english"]
    if source_columns:
        return source_columns[-1]
    return target_field


def make_virtual_translation_dbt(source_doc: DbtDocument, path: Path, language: str) -> DbtDocument:
    target_translation_name = translation_string_column_name(language)
    source_string_columns = [name for name, type_name in source_doc.columns if type_name == "STRING"]
    source_target_fields = set(translatable_fields(source_doc.path.name, source_string_columns))

    def rename_column(name: str, type_name: str) -> str:
        if type_name.upper() != "STRING":
            return name
        if name not in source_target_fields:
            return name
        if name.casefold() != "english":
            return name
        return target_translation_name

    renamed_columns = [(rename_column(name, type_name), type_name) for name, type_name in source_doc.columns]
    rename_map = {name: renamed for (name, _type_name), (renamed, _target_type) in zip(source_doc.columns, renamed_columns) if renamed != name}
    column_name_re = re.compile(r'"([^"]+)"(\s+)(INT|STRING)', re.IGNORECASE)

    def rewrite_header_line(line: str) -> str:
        return column_name_re.sub(
            lambda match: f'"{rename_map.get(match.group(1), match.group(1))}"{match.group(2)}{match.group(3)}',
            line,
        )

    header_end = source_doc.rows[0].line_index if source_doc.rows else len(source_doc.lines)
    header_lines = [rewrite_header_line(line) for line in source_doc.lines[:header_end]]
    header_text = "".join(header_lines)
    profile = FileProfile(
        path=path,
        encoding=source_doc.profile.encoding,
        newline=source_doc.profile.newline,
        final_newline=header_text.endswith(("\n", "\r")),
        sha256=sha256_bytes(b""),
    )
    return DbtDocument(
        path=path,
        raw=b"",
        text=header_text,
        profile=profile,
        lines=header_lines,
        columns=renamed_columns,
        string_columns=[name for name, type_name in renamed_columns if type_name.upper() == "STRING"],
        data_line_index=source_doc.data_line_index,
        rows=[],
    )


def make_inserted_line(source_row: DbtRow, source_doc: DbtDocument, target_doc: DbtDocument, field_values: dict[str, str]) -> str:
    line = source_row.original_line
    body, ending = split_line_ending(line)
    # A newly translated row follows the source-language file byte layout.
    # The target file may have a different historical newline convention.
    line = body + (ending or source_doc.profile.newline)
    target_names = target_doc.string_columns
    source_names = source_doc.string_columns
    replacements: list[tuple[int, int, str]] = []

    for target_name, raw_value in field_values.items():
        try:
            target_index = target_names.index(target_name)
        except ValueError:
            continue
        if target_index >= len(source_row.fields):
            continue
        source_field = source_row.fields[target_index]
        replacements.append((source_field.start, source_field.end, raw_value))

    for start, end, value in sorted(replacements, reverse=True):
        line = line[:start] + value + line[end:]

    if len(source_names) != len(target_names):
        # The row may still be usable, but make the mismatch explicit to callers.
        raise ValueError(
            f"cannot create inserted row for {target_doc.path.name}: "
            f"source has {len(source_names)} string fields, target has {len(target_names)}"
        )
    return line
