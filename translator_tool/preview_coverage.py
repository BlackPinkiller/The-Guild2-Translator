from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

from .code_index import CodeReference, CodeReferenceIndex
from .engine_semantics import engine_format_argument_kind
from .preview_placeholders import (
    placeholder_arguments,
    placeholder_reference_complete,
    placeholder_reference_score,
)
from .script_semantics import call_contract


@dataclass(frozen=True)
class PreviewReferenceCoverage:
    indexed_labels: int = 0
    semantic_display_labels: int = 0
    gui_resource_labels: int = 0
    non_display_labels: int = 0
    low_confidence_labels: int = 0
    runtime_argument_positions: int = 0
    resolved_runtime_positions: int = 0
    unresolved_runtime_positions: int = 0

    def metrics(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class PlaceholderPreviewCoverage:
    labels_with_placeholders: int = 0
    placeholder_positions: int = 0
    concrete_positions: int = 0
    semantic_type_positions: int = 0
    suffix_format_positions: int = 0
    structural_positions: int = 0
    incompatible_positions: int = 0
    expression_only_positions: int = 0
    missing_positions: int = 0

    def metrics(self) -> dict[str, int]:
        return asdict(self)


_SUFFIX_TYPED_FORMATS = frozenset(
    {
        "SN",
        "SV",
        "SZ",
        "SK",
        "ST",
        "SA",
        "SD",
        "SB",
        "SL",
        "GG",
        "GN",
        "GT",
        "DN",
        "DS",
        "n",
        "i",
        "f",
        "t",
        "c",
        "z",
        "j",
    }
)


def _suffix_has_intrinsic_format(suffix: str) -> bool:
    if suffix.upper() in _SUFFIX_TYPED_FORMATS:
        return True
    return suffix in _SUFFIX_TYPED_FORMATS


def preview_reference_coverage(index: CodeReferenceIndex) -> PreviewReferenceCoverage:
    """Summarize indexed preview evidence without scanning translation rows."""
    labels = set(index.project_references) | set(index.vanilla_references)
    semantic_display_labels = 0
    gui_resource_labels = 0
    non_display_labels = 0
    low_confidence_labels = 0
    runtime_argument_positions = 0
    resolved_runtime_positions = 0
    measured_calls: set[tuple[object, ...]] = set()
    for label in labels:
        references = (
            index.project_references.get(label)
            or index.vanilla_references.get(label)
            or ()
        )
        semantic = tuple(reference for reference in references if _is_display_reference(reference))
        gui = tuple(reference for reference in references if reference.role == "gui_resource")
        if semantic:
            semantic_display_labels += 1
        if gui:
            gui_resource_labels += 1
        if not semantic and not gui:
            non_display_labels += 1
        if references and max(reference.confidence for reference in references) < 50:
            low_confidence_labels += 1
        for reference in semantic:
            call_identity = (
                reference.source,
                reference.path,
                reference.line,
                reference.column,
                reference.call_name,
                reference.arguments,
                reference.runtime_arguments,
            )
            if call_identity in measured_calls:
                continue
            measured_calls.add(call_identity)
            runtime_argument_positions += len(reference.runtime_arguments)
            resolved_runtime_positions += sum(
                1
                for position in range(len(reference.runtime_arguments))
                if position < len(reference.runtime_argument_values)
                and bool(reference.runtime_argument_values[position])
            )
    return PreviewReferenceCoverage(
        indexed_labels=len(labels),
        semantic_display_labels=semantic_display_labels,
        gui_resource_labels=gui_resource_labels,
        non_display_labels=non_display_labels,
        low_confidence_labels=low_confidence_labels,
        runtime_argument_positions=runtime_argument_positions,
        resolved_runtime_positions=resolved_runtime_positions,
        unresolved_runtime_positions=max(
            0,
            runtime_argument_positions - resolved_runtime_positions,
        ),
    )


def preview_placeholder_coverage(
    index: CodeReferenceIndex,
    label_texts: Iterable[tuple[str, str]],
) -> PlaceholderPreviewCoverage:
    """Measure evidence for placeholders that localization text actually uses."""
    labels_with_placeholders = 0
    placeholder_positions = 0
    concrete_positions = 0
    semantic_type_positions = 0
    suffix_format_positions = 0
    structural_positions = 0
    incompatible_positions = 0
    expression_only_positions = 0
    missing_positions = 0
    seen_labels: set[str] = set()
    for label, text in label_texts:
        placeholders = placeholder_arguments(text)
        normalized_label = label.strip().lstrip("_").casefold()
        if not normalized_label or not placeholders or normalized_label in seen_labels:
            continue
        seen_labels.add(normalized_label)
        labels_with_placeholders += 1
        references = index.references_for(label).active
        selected = _metric_references(text, references)
        for number, suffix in placeholders:
            placeholder_positions += 1
            token = f"%{number}{suffix}"
            aligned = tuple(
                reference
                for reference in selected
                if placeholder_reference_complete(token, reference)
            )
            if not aligned:
                if engine_format_argument_kind(label, number):
                    semantic_type_positions += 1
                elif _suffix_has_intrinsic_format(suffix):
                    suffix_format_positions += 1
                else:
                    missing_positions += 1
                continue
            values, kinds = _runtime_evidence(aligned, number)
            if any(value not in {"", "$N"} for value in values):
                concrete_positions += 1
            elif kinds and all(kind == "structure" for kind in kinds):
                structural_positions += 1
            elif not _placeholder_kinds_compatible(suffix, kinds):
                incompatible_positions += 1
            elif any(kind and kind != "structure" for kind in kinds):
                semantic_type_positions += 1
            elif _suffix_has_intrinsic_format(suffix):
                suffix_format_positions += 1
            else:
                expression_only_positions += 1
    return PlaceholderPreviewCoverage(
        labels_with_placeholders=labels_with_placeholders,
        placeholder_positions=placeholder_positions,
        concrete_positions=concrete_positions,
        semantic_type_positions=semantic_type_positions,
        suffix_format_positions=suffix_format_positions,
        structural_positions=structural_positions,
        incompatible_positions=incompatible_positions,
        expression_only_positions=expression_only_positions,
        missing_positions=missing_positions,
    )


def _placeholder_kinds_compatible(
    suffix: str,
    kinds: tuple[str, ...],
) -> bool:
    """Reject only caller evidence that cannot satisfy the requested format."""
    meaningful = {kind for kind in kinds if kind and kind != "structure"}
    if not meaningful:
        return True
    if suffix == "l" and meaningful == {"number"}:
        return False
    if suffix.upper() == "NAME" and meaningful <= {"label", "number", "text"}:
        return False
    return True


def _metric_references(
    text: str,
    references: tuple[CodeReference, ...],
) -> tuple[CodeReference, ...]:
    if not references:
        return ()
    best = max(
        enumerate(references),
        key=lambda item: (
            placeholder_reference_complete(text, item[1]),
            placeholder_reference_score(text, item[1]),
            item[1].confidence,
            -item[0],
        ),
    )[1]
    return (best,)


def _runtime_evidence(
    references: tuple[CodeReference, ...],
    number: int,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    values: list[str] = []
    kinds: list[str] = []
    position = number - 1
    for reference in references:
        if position < len(reference.runtime_argument_values):
            values.extend(str(value) for value in reference.runtime_argument_values[position])
        if position < len(reference.runtime_argument_kinds):
            kinds.extend(str(kind) for kind in reference.runtime_argument_kinds[position])
    return tuple(dict.fromkeys(values)), tuple(dict.fromkeys(kinds))


def _is_display_reference(reference: CodeReference) -> bool:
    call_name = (reference.call_name or "").casefold()
    contract = call_contract(call_name)
    return bool(
        (contract is not None and contract.surface)
        or call_name.startswith("feedback_message")
    )
