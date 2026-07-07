from __future__ import annotations

from dataclasses import dataclass
import bisect
from pathlib import Path
import re


LABEL_RE = re.compile(
    r"@L_[A-Za-z0-9_]+_\+(?![A-Za-z0-9])|"
    r"@L_[A-Za-z0-9_]+_\+[A-Za-z0-9]+|"
    r"@L_[A-Za-z0-9_]+"
)
DYNAMIC_LABEL_RE = re.compile(
    r"@L_(?P<prefix>[A-Za-z0-9_]+)_\s*\"\s*\.\.\s*.+?\s*\.\.\s*\"\s*_\+(?P<suffix>[A-Za-z0-9]*)",
    re.DOTALL,
)
CODE_SUFFIXES = {".lua", ".ms", ".gui"}


@dataclass(frozen=True)
class CodeReference:
    label: str
    path: Path
    line: int
    column: int
    call_name: str | None = None
    argument_index: int | None = None
    arguments: tuple[str, ...] = ()
    source: str = "project"

    @property
    def display_name(self) -> str:
        return f"{self.path.name}:{self.line}"


@dataclass(frozen=True)
class CodeReferenceSet:
    project: tuple[CodeReference, ...] = ()
    vanilla: tuple[CodeReference, ...] = ()

    @property
    def active(self) -> tuple[CodeReference, ...]:
        return self.project if self.project else self.vanilla

    @property
    def project_count(self) -> int:
        return len(self.project)

    @property
    def vanilla_count(self) -> int:
        return len(self.vanilla)


class CodeReferenceIndex:
    def __init__(
        self,
        project_references: dict[str, tuple[CodeReference, ...]] | None = None,
        vanilla_references: dict[str, tuple[CodeReference, ...]] | None = None,
    ) -> None:
        self.project_references = project_references or {}
        self.vanilla_references = vanilla_references or {}

    def references_for(self, label: str) -> CodeReferenceSet:
        labels = lookup_labels(label)
        return CodeReferenceSet(
            _first_references(self.project_references, labels),
            _first_references(self.vanilla_references, labels),
        )


def build_code_reference_index(
    game_root: Path | None,
    project_root: Path | None,
    *,
    vanilla_project_name: str = "Vanilla",
) -> CodeReferenceIndex:
    if game_root is None or project_root is None:
        return CodeReferenceIndex()
    game_root = game_root.expanduser().resolve()
    project_root = project_root.expanduser().resolve()
    if project_root.name.casefold() == vanilla_project_name.casefold():
        return CodeReferenceIndex(scan_code_roots((game_root / "Scripts", game_root / "GUI"), source="project"))

    mod_root = game_root / "mods" / project_root.name
    project_references = scan_code_roots((mod_root / "Scripts", mod_root / "GUI"), source="project")
    vanilla_references = scan_code_roots((game_root / "Scripts", game_root / "GUI"), source="vanilla")
    return CodeReferenceIndex(project_references, vanilla_references)


def scan_code_roots(roots: tuple[Path, ...], *, source: str = "project") -> dict[str, tuple[CodeReference, ...]]:
    merged: dict[str, list[CodeReference]] = {}
    for root in roots:
        for label, references in scan_scripts_root(root, source=source).items():
            merged.setdefault(label, []).extend(references)
    return {label: tuple(items) for label, items in merged.items()}


def scan_scripts_root(root: Path, *, source: str = "project") -> dict[str, tuple[CodeReference, ...]]:
    root = root.expanduser()
    if not root.is_dir():
        return {}
    grouped: dict[str, list[CodeReference]] = {}
    for path in _script_files(root):
        try:
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            continue
        line_starts = _line_starts(text)
        for match in LABEL_RE.finditer(text):
            line_number, column = _line_column(line_starts, match.start())
            label = normalize_label(match.group(0))
            call_name, argument_index, arguments = _call_context(text, match.start())
            reference = CodeReference(
                label=label,
                path=path,
                line=line_number,
                column=column,
                call_name=call_name,
                argument_index=argument_index,
                arguments=arguments,
                source=source,
            )
            grouped.setdefault(label, []).append(reference)
            group_label = label_group_key(label)
            if group_label is not None and group_label != label:
                grouped.setdefault(group_label, []).append(reference)
        for match in DYNAMIC_LABEL_RE.finditer(text):
            line_number, column = _line_column(line_starts, match.start())
            suffix = match.group("suffix") or "*"
            label = normalize_label(f"{match.group('prefix')}_*_+{suffix}")
            call_name, argument_index, arguments = _call_context(text, match.start())
            reference = CodeReference(
                label=label,
                path=path,
                line=line_number,
                column=column,
                call_name=call_name,
                argument_index=argument_index,
                arguments=arguments,
                source=source,
            )
            grouped.setdefault(label, []).append(reference)
    return {label: tuple(items) for label, items in grouped.items()}


def normalize_label(label: str) -> str:
    value = label.strip()
    if value.startswith("@L_"):
        value = value[3:]
    if value.endswith("_+"):
        value += "*"
    return value.casefold()


def label_group_key(label: str) -> str | None:
    normalized = normalize_label(label)
    match = re.match(r"^(.*_\+)[A-Za-z0-9]+$", normalized)
    if match is not None:
        return match.group(1) + "*"
    if normalized.endswith("_+*"):
        return normalized
    return None


def lookup_labels(label: str) -> tuple[str, ...]:
    normalized = normalize_label(label)
    candidates = [normalized]
    group = label_group_key(normalized)
    if group is not None and group != normalized:
        candidates.append(group)
    candidates.extend(dynamic_label_keys(normalized))
    if normalized.startswith("_"):
        alternate = normalized[1:]
        candidates.append(alternate)
    else:
        alternate = "_" + normalized
        candidates.append(alternate)
    alternate_group = label_group_key(alternate)
    if alternate_group is not None and alternate_group != alternate:
        candidates.append(alternate_group)
    candidates.extend(dynamic_label_keys(alternate))
    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)
    return tuple(unique)


def dynamic_label_keys(label: str) -> tuple[str, ...]:
    normalized = normalize_label(label)
    match = re.match(r"^(?P<body>.+)_\+(?P<suffix>[A-Za-z0-9*]+)$", normalized)
    if match is None:
        return ()
    parts = match.group("body").split("_")
    if len(parts) < 2:
        return ()
    suffix = match.group("suffix")
    keys: list[str] = []
    for index in range(1, len(parts)):
        candidate_parts = list(parts)
        candidate_parts[index] = "*"
        keys.append("_".join(candidate_parts) + "_+" + suffix)
        keys.append("_".join(candidate_parts) + "_+*")
    return tuple(keys)


def _first_references(
    references: dict[str, tuple[CodeReference, ...]],
    labels: tuple[str, ...],
) -> tuple[CodeReference, ...]:
    for label in labels:
        found = references.get(label, ())
        if found:
            return found
    return ()


def _script_files(root: Path) -> list[Path]:
    try:
        return sorted(
            (
                path
                for path in root.rglob("*")
                if path.is_file() and path.suffix.casefold() in CODE_SUFFIXES
            ),
            key=lambda path: path.as_posix().casefold(),
        )
    except OSError:
        return []


def _line_starts(text: str) -> tuple[int, ...]:
    starts = [0]
    for match in re.finditer(r"\n", text):
        starts.append(match.end())
    return tuple(starts)


def _line_column(line_starts: tuple[int, ...], position: int) -> tuple[int, int]:
    line_index = max(0, bisect.bisect_right(line_starts, position) - 1)
    return line_index + 1, position - line_starts[line_index] + 1


def _call_context(text: str, position: int) -> tuple[str | None, int | None, tuple[str, ...]]:
    open_paren = _nearest_open_call_paren(text, position)
    if open_paren is None:
        return None, None, ()
    prefix = text[:open_paren].rstrip()
    match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)$", prefix)
    if match is None:
        return None, None, ()
    argument_index = _top_level_comma_count(text[open_paren + 1 : position])
    close_paren = _matching_close_paren(text, open_paren)
    arguments = _split_top_level_arguments(text[open_paren + 1 : close_paren]) if close_paren is not None else ()
    return match.group(1), argument_index, arguments


def _nearest_open_call_paren(text: str, position: int) -> int | None:
    depth = 0
    for index in range(position - 1, -1, -1):
        char = text[index]
        if char == ")":
            depth += 1
        elif char == "(":
            if depth == 0:
                return index
            depth -= 1
    return None


def _top_level_comma_count(text: str) -> int:
    depth = 0
    count = 0
    quote: str | None = None
    escaped = False
    for char in text:
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            count += 1
    return count


def _matching_close_paren(text: str, open_paren: int) -> int | None:
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(open_paren, len(text)):
        char = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _split_top_level_arguments(text: str) -> tuple[str, ...]:
    arguments: list[str] = []
    depth = 0
    quote: str | None = None
    escaped = False
    start = 0
    for index, char in enumerate(text):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            arguments.append(text[start:index].strip())
            start = index + 1
    trailing = text[start:].strip()
    if trailing or text:
        arguments.append(trailing)
    return tuple(arguments)
