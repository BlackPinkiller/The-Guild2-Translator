from __future__ import annotations

from dataclasses import dataclass

from .code_index import CodeReference
from .code_window_context import (
    PreviewWindowContext,
    context_has_label,
    window_context_for_reference,
)
from .preview_placeholders import (
    placeholder_reference_complete,
    placeholder_reference_score,
)


@dataclass(frozen=True)
class PreviewContextSelection:
    references: tuple[CodeReference, ...] = ()
    window: PreviewWindowContext | None = None


def select_preview_context(
    text: str,
    references: tuple[CodeReference, ...],
    current_label: str = "",
) -> PreviewContextSelection:
    """Choose one coherent call site for placeholders and window presentation."""
    if not references:
        return PreviewContextSelection()
    ranked: list[
        tuple[
            tuple[int, int, int, int, int, int],
            int,
            CodeReference,
            PreviewWindowContext | None,
        ]
    ] = []
    for index, reference in enumerate(references):
        window = window_context_for_reference(reference, current_label)
        relevant_window = window is not None and (
            not current_label or context_has_label(window, current_label)
        )
        complete = placeholder_reference_complete(text, reference)
        context_detail = _window_context_detail(window) if relevant_window else 0
        key = (
            int(relevant_window),
            int(complete),
            placeholder_reference_score(text, reference),
            _surface_selection_priority(window) if relevant_window else 0,
            context_detail,
            int(reference.confidence),
        )
        ranked.append((key, -index, reference, window if relevant_window else None))
    _key, _stable_order, reference, window = max(ranked, key=lambda item: (item[0], item[1]))
    return PreviewContextSelection((reference,), window)


def rank_preview_references(
    text: str,
    references: tuple[CodeReference, ...],
    current_label: str = "",
) -> tuple[CodeReference, ...]:
    selection = select_preview_context(text, references, current_label)
    if not selection.references:
        return references
    selected = selection.references[0]
    return (selected, *(reference for reference in references if reference != selected))


def _window_context_detail(context: PreviewWindowContext | None) -> int:
    if context is None:
        return 0
    score = 0
    if context.header_label:
        score += 2
    if context.body_label:
        score += 2
    if context.header_label and context.body_label:
        score += 2
    score += min(3, len(context.buttons))
    return score


def _surface_selection_priority(context: PreviewWindowContext | None) -> int:
    if context is None:
        return 0
    if context.surface in {"measure_choice", "measure_help", "questbook"}:
        return 1
    return 2
