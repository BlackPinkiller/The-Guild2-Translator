from __future__ import annotations

from dataclasses import asdict, dataclass

from .code_index import CodeReference, CodeReferenceIndex
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


def _is_display_reference(reference: CodeReference) -> bool:
    call_name = (reference.call_name or "").casefold()
    contract = call_contract(call_name)
    return bool(
        (contract is not None and contract.surface)
        or call_name.startswith("feedback_message")
    )
