from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


LABEL_RE = re.compile(
    r"@L_[A-Za-z0-9_]+_\+(?![A-Za-z0-9])|"
    r"@L_[A-Za-z0-9_]+_\+[A-Za-z0-9]+|"
    r"@L_[A-Za-z0-9_]+"
)
CALL_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")
SCRIPT_SUFFIXES = {".lua", ".ms"}


@dataclass(frozen=True)
class CodeReference:
    label: str
    path: Path
    line: int
    column: int
    call_name: str | None = None
    argument_index: int | None = None
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
        return CodeReferenceIndex(scan_scripts_root(game_root / "Scripts", source="project"))

    project_scripts = game_root / "mods" / project_root.name / "Scripts"
    project_references = scan_scripts_root(project_scripts, source="project")
    vanilla_references = scan_scripts_root(game_root / "Scripts", source="vanilla")
    return CodeReferenceIndex(project_references, vanilla_references)


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
        for line_number, line in enumerate(text.splitlines(), start=1):
            for match in LABEL_RE.finditer(line):
                label = normalize_label(match.group(0))
                call_name, argument_index = _line_call_context(line, match.start())
                reference = CodeReference(
                    label=label,
                    path=path,
                    line=line_number,
                    column=match.start() + 1,
                    call_name=call_name,
                    argument_index=argument_index,
                    source=source,
                )
                grouped.setdefault(label, []).append(reference)
                group_label = label_group_key(label)
                if group_label is not None and group_label != label:
                    grouped.setdefault(group_label, []).append(reference)
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
    if normalized.startswith("_"):
        alternate = normalized[1:]
        candidates.append(alternate)
    else:
        alternate = "_" + normalized
        candidates.append(alternate)
    alternate_group = label_group_key(alternate)
    if alternate_group is not None and alternate_group != alternate:
        candidates.append(alternate_group)
    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)
    return tuple(unique)


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
                if path.is_file() and path.suffix.casefold() in SCRIPT_SUFFIXES
            ),
            key=lambda path: path.as_posix().casefold(),
        )
    except OSError:
        return []


def _line_call_context(line: str, column: int) -> tuple[str | None, int | None]:
    before = line[:column]
    matches = list(CALL_RE.finditer(before))
    if not matches:
        return None, None
    match = matches[-1]
    argument_index = before[match.end() :].count(",")
    return match.group(1), argument_index
