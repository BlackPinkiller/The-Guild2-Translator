from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Callable

from .code_index import (
    CodeExternalFlow,
    CodeFileAnalysis,
    CodeFileSpec,
    CodeReference,
    CodeReferenceIndex,
    CodeReturnLabel,
    CrossFileSemanticLinker,
    analyze_code_file,
    code_index_inputs,
    lookup_labels,
    normalize_label,
)
from .file_utils import atomic_write
from .script_semantics import analyze_script as _semantic_analyze_script
from .settings import settings_dir


CACHE_FORMAT_VERSION = 1
LEXICAL_SCHEMA_VERSION = 2
ANALYZER_REVISION = "script-semantics-2026-07-22-1"
MAX_CACHE_MANIFESTS = 8
MAX_CACHE_BYTES = 128 * 1024 * 1024
MAX_CACHED_FILES = 8192
_LABEL_FRAGMENT_RE = re.compile(rb"@l_[a-z0-9_+*]{5,}|_[a-z][a-z0-9_+*]{5,}")
_CALL_NAME_RE = re.compile(rb"\b([a-z_][a-z0-9_.:]*)\s*\(", re.IGNORECASE)
_IMPLEMENTATION_REVISION = ""


@dataclass(frozen=True)
class LazyIndexProgress:
    analyzed: int
    total: int
    complete: bool


class CodeFactsCache:
    """Versioned per-file facts; final label-to-reference choices are never persisted."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._files: dict[str, dict[str, object]] = {}
        self._dirty = False
        self._dirty_updates = 0
        self._load()

    def lexical_blob(self, spec: CodeFileSpec) -> bytes | None:
        entry = self._valid_stat_entry(spec)
        if entry is None:
            return None
        value = entry.get("lexical")
        return value.encode("ascii") if isinstance(value, str) else None

    def verified_analysis(
        self,
        spec: CodeFileSpec,
        raw: bytes,
        catalog_digest: str,
    ) -> CodeFileAnalysis | None:
        entry = self._valid_stat_entry(spec)
        if entry is None or entry.get("sha256") != _sha256(raw):
            return None
        if entry.get("analyzer") != _analyzer_revision() or entry.get("catalog") != catalog_digest:
            return None
        serialized = entry.get("references")
        if not isinstance(serialized, list):
            return None
        references = tuple(
            reference
            for item in serialized
            if isinstance(item, dict)
            if (reference := _reference_from_json(item, spec)) is not None
        )
        serialized_returns = entry.get("return_labels")
        serialized_flows = entry.get("external_flows")
        if not isinstance(serialized_returns, list) or not isinstance(serialized_flows, list):
            return None
        return CodeFileAnalysis(
            _index_from_references(references, spec.source),
            tuple(
                returned
                for item in serialized_returns
                if isinstance(item, dict)
                if (returned := _return_from_json(item, spec)) is not None
            ),
            tuple(
                flow
                for item in serialized_flows
                if isinstance(item, dict)
                if (flow := _flow_from_json(item, spec)) is not None
            ),
        )

    def record_lexical(self, spec: CodeFileSpec, raw: bytes, lexical: bytes) -> None:
        stat = _safe_stat(spec.path)
        if stat is None:
            return
        key = _spec_key(spec)
        digest = _sha256(raw)
        current = self._files.get(key)
        unchanged = isinstance(current, dict) and current.get("sha256") == digest
        entry: dict[str, object] = {
            "path": str(spec.path),
            "source": spec.source,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": digest,
            "lexical": lexical.decode("ascii", errors="ignore"),
            "used_ns": time.time_ns(),
        }
        if unchanged:
            for name in (
                "analyzer",
                "catalog",
                "references",
                "return_labels",
                "external_flows",
            ):
                if name in current:
                    entry[name] = current[name]
        self._files[key] = entry
        self._mark_dirty()

    def record_analysis(
        self,
        spec: CodeFileSpec,
        raw: bytes,
        lexical: bytes,
        catalog_digest: str,
        analysis: CodeFileAnalysis,
    ) -> None:
        self.record_lexical(spec, raw, lexical)
        entry = self._files.get(_spec_key(spec))
        if entry is None:
            return
        references = (
            analysis.index.vanilla_references
            if spec.source == "vanilla"
            else analysis.index.project_references
        )
        entry["analyzer"] = _analyzer_revision()
        entry["catalog"] = catalog_digest
        entry["references"] = [
            _reference_to_json(reference)
            for items in references.values()
            for reference in items
        ]
        entry["return_labels"] = [_return_to_json(value) for value in analysis.return_labels]
        entry["external_flows"] = [_flow_to_json(value) for value in analysis.external_flows]
        self._mark_dirty()

    def flush_if_needed(self, *, force: bool = False) -> None:
        if not self._dirty or (not force and self._dirty_updates < 32):
            return
        self._trim_entries()
        payload = {
            "format": CACHE_FORMAT_VERSION,
            "lexical_schema": LEXICAL_SCHEMA_VERSION,
            "files": self._files,
        }
        atomic_write(
            self.path,
            (json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8"),
        )
        self._dirty = False
        self._dirty_updates = 0
        _prune_cache_manifests(self.path.parent)

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        if payload.get("format") != CACHE_FORMAT_VERSION:
            return
        if payload.get("lexical_schema") != LEXICAL_SCHEMA_VERSION:
            return
        files = payload.get("files")
        if isinstance(files, dict):
            self._files = {
                str(key): value
                for key, value in files.items()
                if isinstance(value, dict)
            }

    def _valid_stat_entry(self, spec: CodeFileSpec) -> dict[str, object] | None:
        entry = self._files.get(_spec_key(spec))
        stat = _safe_stat(spec.path)
        if entry is None or stat is None:
            return None
        if entry.get("size") != stat.st_size or entry.get("mtime_ns") != stat.st_mtime_ns:
            return None
        if entry.get("path") != str(spec.path) or entry.get("source") != spec.source:
            return None
        entry["used_ns"] = time.time_ns()
        return entry

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._dirty_updates += 1

    def _trim_entries(self) -> None:
        if len(self._files) <= MAX_CACHED_FILES:
            return
        ranked = sorted(
            self._files.items(),
            key=lambda item: int(item[1].get("used_ns", 0) or 0),
            reverse=True,
        )
        self._files = dict(ranked[:MAX_CACHED_FILES])


class LazyCodeIndexBuilder:
    """Analyze requested labels first, then fill the same index in small file batches."""

    def __init__(
        self,
        game_root: Path,
        project_root: Path,
        *,
        vanilla_project_name: str = "Vanilla",
        cache_path: Path | None = None,
    ) -> None:
        self.game_root = game_root
        self.project_root = project_root
        self.vanilla_project_name = vanilla_project_name
        self.cache_path = cache_path or default_cache_path(game_root, project_root)
        self.files: tuple[CodeFileSpec, ...] = ()
        self.label_catalog = frozenset()
        self.catalog_digest = ""
        self.cache: CodeFactsCache | None = None
        self._search_blobs: dict[str, bytes] = {}
        self._analyzed: set[str] = set()
        self._linker = CrossFileSemanticLinker()
        self._pending_aliases: set[str] = set()
        self._return_aliases: dict[str, set[str]] = {}
        self._prepared = False

    @property
    def progress(self) -> LazyIndexProgress:
        return LazyIndexProgress(len(self._analyzed), len(self.files), self.complete)

    @property
    def complete(self) -> bool:
        return self._prepared and len(self._analyzed) >= len(self.files)

    def prepare(self) -> None:
        if self._prepared:
            return
        self.files, self.label_catalog = code_index_inputs(
            self.game_root,
            self.project_root,
            vanilla_project_name=self.vanilla_project_name,
        )
        self.catalog_digest = _catalog_digest(self.label_catalog)
        self.cache = CodeFactsCache(self.cache_path)
        self._prepared = True

    def analyze_labels(
        self,
        labels: tuple[str, ...],
        *,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> CodeReferenceIndex:
        self.prepare()
        requested = tuple(dict.fromkeys(normalize_label(label) for label in labels if label.strip()))
        if not requested:
            return CodeReferenceIndex()
        requested_keys = {
            key
            for label in requested
            for key in lookup_labels(label)
        }
        processed_aliases: set[str] = set()
        self._queue_return_aliases(requested_keys, processed_aliases)
        needles = _candidate_needles(requested)
        candidates: list[CodeFileSpec] = []
        for spec in self.files:
            if cancelled():
                break
            key = _spec_key(spec)
            if key in self._analyzed:
                continue
            blob = self._lexical_blob(spec)
            if blob is not None and any(needle in blob for needle in needles):
                candidates.append(spec)
        result = CodeReferenceIndex()
        for spec in candidates:
            if cancelled():
                break
            result.merge(self._analyze_spec(spec))
            self._queue_return_aliases(requested_keys, processed_aliases)
        while self._pending_aliases and not cancelled():
            aliases = tuple(self._pending_aliases)
            self._pending_aliases.clear()
            processed_aliases.update(aliases)
            call_needles = tuple(f"c:{alias.casefold()}".encode("ascii") for alias in aliases)
            for spec in self.files:
                if cancelled():
                    break
                if _spec_key(spec) in self._analyzed:
                    continue
                blob = self._lexical_blob(spec)
                if blob is not None and any(needle in blob for needle in call_needles):
                    result.merge(self._analyze_spec(spec))
                    self._queue_return_aliases(requested_keys, processed_aliases)
        self._flush_cache(force=True)
        return result

    def analyze_next_batch(
        self,
        limit: int = 4,
        *,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> CodeReferenceIndex:
        self.prepare()
        result = CodeReferenceIndex()
        count = 0
        for spec in self.files:
            if count >= max(1, limit) or cancelled():
                break
            if _spec_key(spec) in self._analyzed:
                continue
            result.merge(self._analyze_spec(spec))
            count += 1
        self._flush_cache(force=self.complete)
        return result

    def close(self) -> None:
        self._flush_cache(force=True)

    def _flush_cache(self, *, force: bool) -> None:
        """Keep indexing usable when the optional on-disk cache is unavailable."""
        if self.cache is None:
            return
        try:
            self.cache.flush_if_needed(force=force)
        except OSError:
            pass

    def _lexical_blob(self, spec: CodeFileSpec) -> bytes | None:
        key = _spec_key(spec)
        cached = self._search_blobs.get(key)
        if cached is not None:
            return cached
        assert self.cache is not None
        blob = self.cache.lexical_blob(spec)
        if blob is None:
            try:
                raw = spec.path.read_bytes()
            except OSError:
                self._analyzed.add(key)
                return None
            blob = _lexical_blob(raw)
            self.cache.record_lexical(spec, raw, blob)
        self._search_blobs[key] = blob
        return blob

    def _analyze_spec(self, spec: CodeFileSpec) -> CodeReferenceIndex:
        key = _spec_key(spec)
        if key in self._analyzed:
            return CodeReferenceIndex()
        self._analyzed.add(key)
        try:
            raw = spec.path.read_bytes()
        except OSError:
            return CodeReferenceIndex()
        lexical = _lexical_blob(raw)
        self._search_blobs[key] = lexical
        assert self.cache is not None
        cached = self.cache.verified_analysis(spec, raw, self.catalog_digest)
        if cached is not None:
            self._remember_return_aliases(cached)
            return self._linker.add(cached)
        analysis = analyze_code_file(spec, label_catalog=self.label_catalog, raw=raw)
        self.cache.record_analysis(spec, raw, lexical, self.catalog_digest, analysis)
        self._remember_return_aliases(analysis)
        return self._linker.add(analysis)

    def _remember_return_aliases(self, analysis: CodeFileAnalysis) -> None:
        for value in analysis.return_labels:
            if value.cross_file:
                self._return_aliases.setdefault(value.label, set()).add(value.alias)

    def _queue_return_aliases(
        self,
        requested_keys: set[str],
        processed_aliases: set[str],
    ) -> None:
        for label, aliases in self._return_aliases.items():
            if label in requested_keys:
                self._pending_aliases.update(aliases - processed_aliases)


def default_cache_path(game_root: Path, project_root: Path) -> Path:
    identity = "\0".join(
        (
            str(game_root.expanduser().resolve()).casefold(),
            str(project_root.expanduser().resolve()).casefold(),
        )
    )
    name = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24] + ".json"
    return settings_dir() / "code_index" / name


def _lexical_blob(raw: bytes) -> bytes:
    lowered = raw.lower()
    fragments = [
        *(b"l:" + match.group(0) for match in _LABEL_FRAGMENT_RE.finditer(lowered)),
        *(b"c:" + match.group(1) for match in _CALL_NAME_RE.finditer(lowered)),
    ]
    return b"\n".join(dict.fromkeys(fragments))


def _candidate_needles(labels: tuple[str, ...]) -> tuple[bytes, ...]:
    values: list[bytes] = []
    for label in labels:
        normalized = normalize_label(label).lstrip("_")
        body = re.sub(r"_\+[a-z0-9*]+$", "", normalized)
        candidates = [normalized, body]
        parts = body.split("_")
        for remove in range(1, min(4, len(parts) - 1) + 1):
            prefix = "_".join(parts[:-remove])
            if len(prefix) >= 12 and prefix.count("_") >= 2:
                candidates.append(prefix + "_")
        for candidate in candidates:
            for value in (candidate, "_" + candidate, "@l_" + candidate):
                encoded = value.casefold().encode("ascii", errors="ignore")
                if len(encoded) >= 8 and encoded not in values:
                    values.append(encoded)
    return tuple(values)


def _index_from_references(
    references: tuple[CodeReference, ...],
    source: str,
) -> CodeReferenceIndex:
    grouped: dict[str, list[CodeReference]] = {}
    for reference in references:
        grouped.setdefault(reference.label, []).append(reference)
    frozen = {label: tuple(items) for label, items in grouped.items()}
    if source == "vanilla":
        return CodeReferenceIndex(vanilla_references=frozen)
    return CodeReferenceIndex(project_references=frozen)


def _reference_to_json(reference: CodeReference) -> dict[str, object]:
    return {
        "label": reference.label,
        "line": reference.line,
        "column": reference.column,
        "call": reference.call_name,
        "argument_index": reference.argument_index,
        "arguments": list(reference.arguments),
        "role": reference.role,
        "runtime_arguments": list(reference.runtime_arguments),
        "runtime_values": [list(values) for values in reference.runtime_argument_values],
        "resolved_arguments": [list(values) for values in reference.resolved_arguments],
        "match_kind": reference.match_kind,
        "confidence": reference.confidence,
        "binary": reference.binary,
    }


def _reference_from_json(item: dict[str, object], spec: CodeFileSpec) -> CodeReference | None:
    label = item.get("label")
    line = item.get("line")
    column = item.get("column")
    if not isinstance(label, str) or not isinstance(line, int) or not isinstance(column, int):
        return None
    arguments = item.get("arguments")
    runtime_arguments = item.get("runtime_arguments")
    runtime_values = item.get("runtime_values")
    resolved_arguments = item.get("resolved_arguments")
    return CodeReference(
        label=label,
        path=spec.path,
        line=line,
        column=column,
        call_name=item.get("call") if isinstance(item.get("call"), str) else None,
        argument_index=item.get("argument_index") if isinstance(item.get("argument_index"), int) else None,
        arguments=tuple(value for value in arguments if isinstance(value, str)) if isinstance(arguments, list) else (),
        source=spec.source,
        role=item.get("role") if isinstance(item.get("role"), str) else "unattached",
        runtime_arguments=(
            tuple(value for value in runtime_arguments if isinstance(value, str))
            if isinstance(runtime_arguments, list)
            else ()
        ),
        runtime_argument_values=(
            tuple(
                tuple(value for value in values if isinstance(value, str))
                for values in runtime_values
                if isinstance(values, list)
            )
            if isinstance(runtime_values, list)
            else ()
        ),
        resolved_arguments=(
            tuple(
                tuple(value for value in values if isinstance(value, str))
                for values in resolved_arguments
                if isinstance(values, list)
            )
            if isinstance(resolved_arguments, list)
            else ()
        ),
        match_kind=item.get("match_kind") if isinstance(item.get("match_kind"), str) else "exact",
        confidence=item.get("confidence") if isinstance(item.get("confidence"), int) else 0,
        binary=item.get("binary") is True,
    )


def _return_to_json(value: CodeReturnLabel) -> dict[str, object]:
    return {
        "alias": value.alias,
        "cross_file": value.cross_file,
        "label": value.label,
        "match_kind": value.match_kind,
        "confidence": value.confidence,
    }


def _return_from_json(
    item: dict[str, object],
    spec: CodeFileSpec,
) -> CodeReturnLabel | None:
    alias = item.get("alias")
    label = item.get("label")
    if not isinstance(alias, str) or not isinstance(label, str):
        return None
    return CodeReturnLabel(
        path=spec.path,
        source=spec.source,
        alias=alias,
        cross_file=item.get("cross_file") is True,
        label=label,
        match_kind=(
            item.get("match_kind") if isinstance(item.get("match_kind"), str) else "exact"
        ),
        confidence=item.get("confidence") if isinstance(item.get("confidence"), int) else 0,
    )


def _flow_to_json(value: CodeExternalFlow) -> dict[str, object]:
    return {
        "alias": value.alias,
        "line": value.line,
        "column": value.column,
        "call": value.call_name,
        "argument_index": value.argument_index,
        "arguments": list(value.arguments),
        "role": value.role,
        "runtime_arguments": list(value.runtime_arguments),
        "runtime_values": [list(values) for values in value.runtime_argument_values],
        "resolved_arguments": [list(values) for values in value.resolved_arguments],
        "confidence": value.confidence,
    }


def _flow_from_json(
    item: dict[str, object],
    spec: CodeFileSpec,
) -> CodeExternalFlow | None:
    alias = item.get("alias")
    line = item.get("line")
    column = item.get("column")
    call = item.get("call")
    argument_index = item.get("argument_index")
    if (
        not isinstance(alias, str)
        or not isinstance(line, int)
        or not isinstance(column, int)
        or not isinstance(call, str)
        or not isinstance(argument_index, int)
    ):
        return None
    arguments = item.get("arguments")
    runtime_arguments = item.get("runtime_arguments")
    runtime_values = item.get("runtime_values")
    resolved_arguments = item.get("resolved_arguments")
    return CodeExternalFlow(
        path=spec.path,
        source=spec.source,
        alias=alias,
        line=line,
        column=column,
        call_name=call,
        argument_index=argument_index,
        arguments=(
            tuple(value for value in arguments if isinstance(value, str))
            if isinstance(arguments, list)
            else ()
        ),
        role=item.get("role") if isinstance(item.get("role"), str) else "template",
        runtime_arguments=(
            tuple(value for value in runtime_arguments if isinstance(value, str))
            if isinstance(runtime_arguments, list)
            else ()
        ),
        runtime_argument_values=(
            tuple(
                tuple(value for value in values if isinstance(value, str))
                for values in runtime_values
                if isinstance(values, list)
            )
            if isinstance(runtime_values, list)
            else ()
        ),
        resolved_arguments=(
            tuple(
                tuple(value for value in values if isinstance(value, str))
                for values in resolved_arguments
                if isinstance(values, list)
            )
            if isinstance(resolved_arguments, list)
            else ()
        ),
        confidence=item.get("confidence") if isinstance(item.get("confidence"), int) else 0,
    )


def _catalog_digest(catalog: frozenset[str]) -> str:
    digest = hashlib.sha256()
    for label in sorted(catalog):
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _analyzer_revision() -> str:
    global _IMPLEMENTATION_REVISION
    if not _IMPLEMENTATION_REVISION:
        digest = hashlib.sha256()
        paths = {
            Path(__file__),
            Path(analyze_code_file.__code__.co_filename),
            Path(_semantic_analyze_script.__code__.co_filename),
        }
        for path in sorted(paths, key=lambda item: str(item).casefold()):
            try:
                digest.update(path.read_bytes())
            except OSError:
                digest.update(str(path).encode("utf-8"))
        _IMPLEMENTATION_REVISION = digest.hexdigest()[:20]
    return f"{ANALYZER_REVISION}:{_IMPLEMENTATION_REVISION}"


def _spec_key(spec: CodeFileSpec) -> str:
    identity = f"{spec.source}\0{str(spec.path.expanduser().resolve()).casefold()}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_stat(path: Path):
    try:
        return path.stat()
    except OSError:
        return None


def _prune_cache_manifests(root: Path) -> None:
    try:
        manifests = sorted(
            (path for path in root.glob("*.json") if path.is_file()),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
    except OSError:
        return
    kept_size = 0
    for index, path in enumerate(manifests):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if index < MAX_CACHE_MANIFESTS and kept_size + size <= MAX_CACHE_BYTES:
            kept_size += size
            continue
        try:
            path.unlink()
        except OSError:
            pass
