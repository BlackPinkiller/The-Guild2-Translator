from __future__ import annotations

from dataclasses import dataclass
import bisect
from pathlib import Path
import re

from .script_semantics import (
    ExternalCallFlow,
    FunctionReturnLabel,
    ScriptSemanticFacts,
    analyze_script_facts,
)


LABEL_RE = re.compile(
    r"@L_[A-Za-z0-9_]+_\+(?![A-Za-z0-9])|"
    r"@L_[A-Za-z0-9_]+_\+[A-Za-z0-9]+|"
    r"@L_[A-Za-z0-9_]+"
)
LABEL_EXPRESSION_TOKEN = (
    r"(?:\"[^\"\\]*(?:\\.[^\"\\]*)*\"|'[^'\\]*(?:\\.[^'\\]*)*'|"
    r"@L_[A-Za-z0-9_+*]*|_[A-Za-z0-9_+*]*|"
    r"[A-Za-z_][A-Za-z0-9_]*(?:\([^()\r\n]*\))?)"
)
CONCAT_LABEL_EXPRESSION_RE = re.compile(
    rf"(?P<expr>{LABEL_EXPRESSION_TOKEN}\s*(?:\.\.\s*{LABEL_EXPRESSION_TOKEN}\s*)+)"
)
STRING_LITERAL_RE = re.compile(r"""^(?:"([^"\\]*(?:\\.[^"\\]*)*)"|'([^'\\]*(?:\\.[^'\\]*)*)')$""")
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
    role: str = "unattached"
    runtime_arguments: tuple[str, ...] = ()
    runtime_argument_values: tuple[tuple[str, ...], ...] = ()
    resolved_arguments: tuple[tuple[str, ...], ...] = ()
    match_kind: str = "exact"
    confidence: int = 0
    binary: bool = False

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


@dataclass(frozen=True)
class CodeFileSpec:
    path: Path
    source: str


@dataclass(frozen=True)
class CodeReturnLabel:
    path: Path
    source: str
    alias: str
    cross_file: bool
    label: str
    match_kind: str
    confidence: int


@dataclass(frozen=True)
class CodeExternalFlow:
    path: Path
    source: str
    alias: str
    line: int
    column: int
    call_name: str
    argument_index: int
    arguments: tuple[str, ...]
    role: str
    runtime_arguments: tuple[str, ...]
    runtime_argument_values: tuple[tuple[str, ...], ...]
    resolved_arguments: tuple[tuple[str, ...], ...]
    confidence: int


@dataclass(frozen=True)
class CodeFileAnalysis:
    index: CodeReferenceIndex
    return_labels: tuple[CodeReturnLabel, ...] = ()
    external_flows: tuple[CodeExternalFlow, ...] = ()


class CodeReferenceIndex:
    def __init__(
        self,
        project_references: dict[str, tuple[CodeReference, ...]] | None = None,
        vanilla_references: dict[str, tuple[CodeReference, ...]] | None = None,
    ) -> None:
        self.project_references = project_references or {}
        self.vanilla_references = vanilla_references or {}
        self._lookup_cache: dict[str, CodeReferenceSet] = {}

    def references_for(self, label: str) -> CodeReferenceSet:
        cached = self._lookup_cache.get(label)
        if cached is not None:
            return cached
        labels = lookup_labels(label)
        result = CodeReferenceSet(
            _matching_references(self.project_references, labels),
            _matching_references(self.vanilla_references, labels),
        )
        if len(self._lookup_cache) >= 4096:
            self._lookup_cache.clear()
        self._lookup_cache[label] = result
        return result

    def merge(self, other: CodeReferenceIndex) -> None:
        _merge_reference_maps(self.project_references, other.project_references)
        _merge_reference_maps(self.vanilla_references, other.vanilla_references)
        self._lookup_cache.clear()

    @property
    def is_empty(self) -> bool:
        return not self.project_references and not self.vanilla_references


class CrossFileSemanticLinker:
    """Incrementally joins returned labels to real UI-call data-flow dependencies."""

    def __init__(self) -> None:
        self._returns: dict[tuple[str, str, Path | None], list[CodeReturnLabel]] = {}
        self._flows: dict[tuple[str, str, Path | None], list[CodeExternalFlow]] = {}
        self._emitted: set[tuple[object, ...]] = set()

    def add(self, analysis: CodeFileAnalysis) -> CodeReferenceIndex:
        result = CodeReferenceIndex()
        result.merge(analysis.index)
        for returned in analysis.return_labels:
            key = (
                returned.source,
                returned.alias.casefold(),
                None if returned.cross_file else returned.path,
            )
            self._returns.setdefault(key, []).append(returned)
            for flow in self._flows.get(key, ()):
                self._emit(result, returned, flow)
        for flow in analysis.external_flows:
            keys = (
                (flow.source, flow.alias.casefold(), flow.path),
                (flow.source, flow.alias.casefold(), None),
            )
            for key in keys:
                self._flows.setdefault(key, []).append(flow)
                for returned in self._returns.get(key, ()):
                    self._emit(result, returned, flow)
        return result

    def _emit(
        self,
        result: CodeReferenceIndex,
        returned: CodeReturnLabel,
        flow: CodeExternalFlow,
    ) -> None:
        if not returned.cross_file and returned.path != flow.path:
            return
        identity = (
            returned.source,
            returned.alias.casefold(),
            returned.label,
            flow.path,
            flow.line,
            flow.call_name,
            flow.argument_index,
        )
        if identity in self._emitted:
            return
        self._emitted.add(identity)
        reference = CodeReference(
            label=returned.label,
            path=flow.path,
            line=flow.line,
            column=flow.column,
            call_name=flow.call_name,
            argument_index=flow.argument_index,
            arguments=flow.arguments,
            source=flow.source,
            role=flow.role,
            runtime_arguments=flow.runtime_arguments,
            runtime_argument_values=flow.runtime_argument_values,
            resolved_arguments=flow.resolved_arguments,
            match_kind=returned.match_kind,
            confidence=min(returned.confidence, flow.confidence),
        )
        target = (
            result.vanilla_references
            if flow.source == "vanilla"
            else result.project_references
        )
        target[reference.label] = _dedupe_references(
            [*target.get(reference.label, ()), reference]
        )


def build_code_reference_index(
    game_root: Path | None,
    project_root: Path | None,
    *,
    vanilla_project_name: str = "Vanilla",
) -> CodeReferenceIndex:
    if game_root is None or project_root is None:
        return CodeReferenceIndex()
    files, label_catalog = code_index_inputs(
        game_root,
        project_root,
        vanilla_project_name=vanilla_project_name,
    )
    linker = CrossFileSemanticLinker()
    result = CodeReferenceIndex()
    for spec in files:
        partial = linker.add(analyze_code_file(spec, label_catalog=label_catalog))
        result.merge(partial)
    return result


def code_index_inputs(
    game_root: Path,
    project_root: Path,
    *,
    vanilla_project_name: str = "Vanilla",
) -> tuple[tuple[CodeFileSpec, ...], frozenset[str]]:
    game_root = game_root.expanduser().resolve()
    project_root = project_root.expanduser().resolve()
    label_catalog = _project_label_catalog(project_root)
    vanilla_project_root = project_root.parent / vanilla_project_name
    if vanilla_project_root != project_root:
        label_catalog = frozenset((*label_catalog, *_project_label_catalog(vanilla_project_root)))

    roots: list[tuple[Path, str]] = []
    if project_root.name.casefold() == vanilla_project_name.casefold():
        roots.extend(((game_root / "Scripts", "project"), (game_root / "GUI", "project")))
    else:
        mod_root = game_root / "mods" / project_root.name
        roots.extend(((mod_root / "Scripts", "project"), (mod_root / "GUI", "project")))
        roots.extend(((game_root / "Scripts", "vanilla"), (game_root / "GUI", "vanilla")))

    files = tuple(
        CodeFileSpec(path, source)
        for root, source in roots
        for path in _script_files(root)
    )
    return files, label_catalog


def index_code_file(
    spec: CodeFileSpec,
    *,
    label_catalog: frozenset[str] = frozenset(),
    raw: bytes | None = None,
) -> CodeReferenceIndex:
    return analyze_code_file(
        spec,
        label_catalog=label_catalog,
        raw=raw,
    ).index


def analyze_code_file(
    spec: CodeFileSpec,
    *,
    label_catalog: frozenset[str] = frozenset(),
    raw: bytes | None = None,
) -> CodeFileAnalysis:
    if raw is None:
        try:
            raw = spec.path.read_bytes()
        except OSError:
            return CodeFileAnalysis(CodeReferenceIndex())
    references, facts = _scan_code_file(
        spec.path,
        source=spec.source,
        label_catalog=label_catalog,
        raw=raw,
    )
    index = (
        CodeReferenceIndex(vanilla_references=references)
        if spec.source == "vanilla"
        else CodeReferenceIndex(project_references=references)
    )
    if facts is None:
        return CodeFileAnalysis(index)
    line_starts = _line_starts(raw.decode("utf-8-sig", errors="ignore"))
    return CodeFileAnalysis(
        index,
        _code_return_labels(spec, facts.return_labels),
        _code_external_flows(spec, facts.external_flows, line_starts),
    )


def scan_code_roots(
    roots: tuple[Path, ...],
    *,
    source: str = "project",
    label_catalog: frozenset[str] = frozenset(),
) -> dict[str, tuple[CodeReference, ...]]:
    merged: dict[str, list[CodeReference]] = {}
    for root in roots:
        for label, references in scan_scripts_root(
            root,
            source=source,
            label_catalog=label_catalog,
        ).items():
            merged.setdefault(label, []).extend(references)
    return {label: _dedupe_references(items) for label, items in merged.items()}


def scan_scripts_root(
    root: Path,
    *,
    source: str = "project",
    label_catalog: frozenset[str] = frozenset(),
) -> dict[str, tuple[CodeReference, ...]]:
    root = root.expanduser()
    if not root.is_dir():
        return {}
    grouped: dict[str, list[CodeReference]] = {}
    for path in _script_files(root):
        for label, references in scan_code_file(
            path,
            source=source,
            label_catalog=label_catalog,
        ).items():
            grouped.setdefault(label, []).extend(references)
    return {label: _dedupe_references(items) for label, items in grouped.items()}


def scan_code_file(
    path: Path,
    *,
    source: str = "project",
    label_catalog: frozenset[str] = frozenset(),
    raw: bytes | None = None,
) -> dict[str, tuple[CodeReference, ...]]:
    references, _facts = _scan_code_file(
        path,
        source=source,
        label_catalog=label_catalog,
        raw=raw,
    )
    return references


def _scan_code_file(
    path: Path,
    *,
    source: str,
    label_catalog: frozenset[str],
    raw: bytes | None,
) -> tuple[dict[str, tuple[CodeReference, ...]], ScriptSemanticFacts | None]:
    if raw is None:
        try:
            raw = path.read_bytes()
        except OSError:
            return {}, None
    grouped: dict[str, list[CodeReference]] = {}
    if path.suffix.casefold() == ".gui" and _looks_binary(raw):
        for label, position in _binary_gui_labels(raw):
            grouped.setdefault(label, []).append(
                CodeReference(
                    label=label,
                    path=path,
                    line=1,
                    column=position + 1,
                    source=source,
                    role="gui_resource",
                    match_kind="exact",
                    confidence=25,
                    binary=True,
                )
            )
        return {label: tuple(items) for label, items in grouped.items()}, None

    text = raw.decode("utf-8-sig", errors="ignore")
    line_starts = _line_starts(text)
    facts = analyze_script_facts(text, path, label_catalog=label_catalog)
    for use in facts.uses:
        line_number, column = _line_column(line_starts, use.position)
        label = normalize_label(use.label)
        reference = CodeReference(
            label=label,
            path=path,
            line=line_number,
            column=column,
            call_name=use.call_name,
            argument_index=use.argument_index,
            arguments=use.arguments,
            source=source,
            role=use.role,
            runtime_arguments=use.runtime_arguments,
            runtime_argument_values=use.runtime_argument_values,
            resolved_arguments=use.resolved_arguments,
            match_kind=use.match_kind,
            confidence=use.confidence,
        )
        grouped.setdefault(label, []).append(reference)
    return {label: _dedupe_references(items) for label, items in grouped.items()}, facts


def _code_return_labels(
    spec: CodeFileSpec,
    values: tuple[FunctionReturnLabel, ...],
) -> tuple[CodeReturnLabel, ...]:
    returned: list[CodeReturnLabel] = []
    for value in values:
        for index, alias in enumerate(value.aliases):
            returned.append(
                CodeReturnLabel(
                    path=spec.path,
                    source=spec.source,
                    alias=alias.casefold(),
                    cross_file=index > 0,
                    label=normalize_label(value.label),
                    match_kind=value.match_kind,
                    confidence=value.confidence,
                )
            )
    return tuple(dict.fromkeys(returned))


def _code_external_flows(
    spec: CodeFileSpec,
    values: tuple[ExternalCallFlow, ...],
    line_starts: list[int],
) -> tuple[CodeExternalFlow, ...]:
    flows: list[CodeExternalFlow] = []
    for value in values:
        line, column = _line_column(line_starts, value.position)
        flows.append(
            CodeExternalFlow(
                path=spec.path,
                source=spec.source,
                alias=value.alias.casefold(),
                line=line,
                column=column,
                call_name=value.call_name,
                argument_index=value.argument_index,
                arguments=value.arguments,
                role=value.role,
                runtime_arguments=value.runtime_arguments,
                runtime_argument_values=value.runtime_argument_values,
                resolved_arguments=value.resolved_arguments,
                confidence=value.confidence,
            )
        )
    return tuple(dict.fromkeys(flows))


def _dedupe_references(references: list[CodeReference]) -> tuple[CodeReference, ...]:
    values: list[CodeReference] = []
    seen: set[tuple[object, ...]] = set()
    for reference in references:
        key = (
            reference.path,
            reference.line,
            reference.call_name,
            reference.argument_index,
            reference.arguments,
            reference.role,
            reference.runtime_arguments,
            reference.runtime_argument_values,
            reference.resolved_arguments,
            reference.match_kind,
        )
        if key in seen:
            continue
        seen.add(key)
        values.append(reference)
    return tuple(values)


def _merge_reference_maps(
    target: dict[str, tuple[CodeReference, ...]],
    incoming: dict[str, tuple[CodeReference, ...]],
) -> None:
    for label, references in incoming.items():
        target[label] = _dedupe_references([*target.get(label, ()), *references])


def dynamic_label_matches(text: str) -> tuple[tuple[str, int], ...]:
    values: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for match in CONCAT_LABEL_EXPRESSION_RE.finditer(text):
        label = _dynamic_label_from_expression(match.group("expr"), normalized=True)
        if not label:
            continue
        value = (label, match.start("expr"))
        if value not in seen:
            seen.add(value)
            values.append(value)
    return tuple(values)


def dynamic_label_patterns(text: str, *, normalized: bool = True) -> tuple[str, ...]:
    values: list[str] = []
    for match in CONCAT_LABEL_EXPRESSION_RE.finditer(text):
        label = _dynamic_label_from_expression(match.group("expr"), normalized=normalized)
        if label and label not in values:
            values.append(label)
    return tuple(values)


def _dynamic_label_from_expression(expression: str, *, normalized: bool) -> str:
    parts = [part.strip() for part in expression.split("..") if part.strip()]
    if len(parts) < 2:
        return ""
    fragments: list[str] = []
    has_wildcard = False
    for part in parts:
        literal = _string_literal_text(part)
        if literal is None and _looks_like_unquoted_label_fragment(part):
            literal = part
        if literal is None:
            fragments.append("*")
            has_wildcard = True
        else:
            fragments.append(literal)
    if not has_wildcard:
        return ""
    label = "".join(fragments)
    if label.endswith("_+"):
        label += "*"
    if not (label.startswith("@L_") or label.startswith("_")):
        return ""
    if "_+" not in label:
        return ""
    return normalize_label(label) if normalized else label


def _string_literal_text(value: str) -> str | None:
    match = STRING_LITERAL_RE.match(value.strip())
    if match is None:
        return None
    return match.group(1) if match.group(1) is not None else match.group(2) or ""


def _looks_like_unquoted_label_fragment(value: str) -> bool:
    stripped = value.strip()
    return bool(re.match(r"^(?:@L_[A-Za-z0-9_+*]*|_[A-Za-z0-9_+*]*)$", stripped))


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
    base = _family_base(normalized)
    if base:
        candidates.append(base)
    alternate_base = _family_base(alternate)
    if alternate_base:
        candidates.append(alternate_base)
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


def _matching_references(
    references: dict[str, tuple[CodeReference, ...]],
    labels: tuple[str, ...],
) -> tuple[CodeReference, ...]:
    family_bases = {
        base
        for candidate in labels[:1]
        for base in (_family_base(candidate), _family_base(_alternate_label(candidate)))
        if base
    }
    for label in labels:
        found = references.get(label, ())
        if label in family_bases:
            found = tuple(
                reference
                for reference in found
                if reference.match_kind in {"family", "dynamic"}
            )
        if found:
            return _rank_references(found)
    matches: list[tuple[int, tuple[CodeReference, ...]]] = []
    concrete_labels = tuple(label for label in labels if "*" not in label)
    for pattern, found in references.items():
        if "*" not in pattern:
            continue
        if any(_wildcard_matches(pattern, label) for label in concrete_labels):
            specificity = len(pattern.replace("*", "")) - pattern.count("*") * 8
            matches.append((specificity, found))
    if not matches:
        return ()
    best_specificity = max(item[0] for item in matches)
    combined = [
        reference
        for specificity, found in matches
        if specificity == best_specificity
        for reference in found
    ]
    return _rank_references(tuple(combined))


def _rank_references(references: tuple[CodeReference, ...]) -> tuple[CodeReference, ...]:
    role_score = {
        "body": 6,
        "header": 6,
        "template": 5,
        "runtime_label": 3,
        "button": 2,
        "control_label": 1,
        "assignment": 0,
        "gui_resource": -2,
        "unattached": -3,
    }
    return tuple(
        sorted(
            references,
            key=lambda reference: (
                -reference.confidence,
                -role_score.get(reference.role, 0),
                not bool(reference.runtime_arguments),
                reference.path.as_posix().casefold(),
                reference.line,
                reference.column,
            ),
        )
    )


def _family_base(label: str) -> str:
    match = re.match(r"^(.*)_\+[A-Za-z0-9]+$", normalize_label(label))
    return match.group(1) if match is not None else ""


def _alternate_label(label: str) -> str:
    normalized = normalize_label(label)
    return normalized[1:] if normalized.startswith("_") else "_" + normalized


def _wildcard_matches(pattern: str, label: str) -> bool:
    regex = "^" + re.escape(pattern).replace(r"\*", "[a-z0-9_+]+") + "$"
    return re.match(regex, label) is not None


def _looks_binary(raw: bytes) -> bool:
    if not raw:
        return False
    sample = raw[:4096]
    return b"\0" in sample or sum(byte < 9 or 13 < byte < 32 for byte in sample) > len(sample) // 20


def _binary_gui_labels(raw: bytes) -> tuple[tuple[str, int], ...]:
    values: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for match in re.finditer(
        rb"@L_[A-Za-z0-9_]+_\+[A-Za-z0-9]+|@L_[A-Za-z0-9_]+",
        raw,
    ):
        value = (normalize_label(match.group(0).decode("ascii")), match.start())
        if value not in seen:
            seen.add(value)
            values.append(value)
    return tuple(values)


def _project_label_catalog(project_root: Path) -> frozenset[str]:
    languages_root = project_root / "languages"
    if not languages_root.is_dir():
        return frozenset()
    try:
        from .format_io import load_dbt
    except ImportError:
        return frozenset()
    values: set[str] = set()
    for path in languages_root.glob("*.dbt"):
        try:
            document = load_dbt(path)
        except (OSError, UnicodeError, ValueError):
            continue
        for row in document.rows:
            label = normalize_label(row.get("label"))
            if label:
                values.add(label)
                values.add(label.lstrip("_"))
    return frozenset(values)


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
