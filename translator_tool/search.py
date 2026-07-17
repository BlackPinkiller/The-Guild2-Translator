from __future__ import annotations

from dataclasses import dataclass

from .i18n import status_text, todo_reason_text
from .project import TranslationUnit


@dataclass(frozen=True)
class SearchClause:
    field: str
    needle: str
    excluded: bool = False


SEARCH_FIELD_ALIASES = {
    "label": "label",
    "标签": "label",
    "id": "id",
    "source": "source",
    "src": "source",
    "原文": "source",
    "translation": "translation",
    "target": "translation",
    "译文": "translation",
    "file": "file",
    "文件": "file",
    "status": "status",
    "状态": "status",
}


def parse_search_query(query: str, *, case_sensitive: bool) -> tuple[SearchClause, ...]:
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    escaped = False
    bracket_depth = 0
    for char in query:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\" and quoted:
            escaped = True
            continue
        if char == '"':
            quoted = not quoted
            continue
        if not quoted:
            if char == "[":
                bracket_depth += 1
            elif char == "]" and bracket_depth:
                bracket_depth -= 1
            elif char in {",", "，"} and bracket_depth == 0:
                parts.append("".join(current))
                current = []
                continue
        current.append(char)
    if escaped:
        current.append("\\")
    parts.append("".join(current))

    clauses: list[SearchClause] = []
    for raw_part in parts:
        part = raw_part.strip()
        if not part:
            continue
        excluded = part.startswith("-") and len(part) > 1
        if excluded:
            part = part[1:].lstrip()
        field = ""
        for separator in (":", "："):
            prefix, found, value = part.partition(separator)
            alias = SEARCH_FIELD_ALIASES.get(prefix.strip().casefold())
            if found and alias:
                field = alias
                part = value.strip()
                break
        if not part:
            continue
        needle = part if case_sensitive else part.casefold()
        clauses.append(SearchClause(field=field, needle=needle, excluded=excluded))
    return tuple(clauses)


def search_blob(unit: TranslationUnit) -> str:
    todo_reason = todo_reason_text(unit.todo_reason) if unit.todo_reason else ""
    return "\n".join(
        (
            unit.file_rel,
            unit.record_id,
            unit.label,
            unit.field_name,
            unit.source_text,
            unit.current_text,
            unit.status,
            unit.filter_status(),
            status_text(unit.display_status()),
            todo_reason,
        )
    )


def search_field_values(unit: TranslationUnit, field: str) -> tuple[str, ...]:
    if field == "label":
        return (unit.label,)
    if field == "id":
        return (unit.record_id,)
    if field == "source":
        return (unit.source_text,)
    if field == "translation":
        return (unit.current_text,)
    if field == "file":
        return (unit.file_rel,)
    if field == "status":
        todo_reason = todo_reason_text(unit.todo_reason) if unit.todo_reason else ""
        return (unit.status, unit.filter_status(), status_text(unit.display_status()), todo_reason)
    return ()
