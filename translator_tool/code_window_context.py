from __future__ import annotations

from dataclasses import dataclass
import re

from .code_index import CodeReference, LABEL_RE, normalize_label


PARCHMENT_TEXT = (55, 38, 24, 255)
DARK_PANEL_TEXT = (245, 239, 216, 255)


@dataclass(frozen=True)
class PreviewWindowButton:
    identifier: str
    label: str = ""
    text: str = ""


@dataclass(frozen=True)
class PreviewWindowContext:
    kind: str
    background: str
    default_color: tuple[int, int, int, int]
    header_label: str = ""
    body_label: str = ""
    buttons: tuple[PreviewWindowButton, ...] = ()

    @property
    def labels(self) -> tuple[str, ...]:
        values: list[str] = []
        for label in (self.header_label, self.body_label):
            if label and label not in values:
                values.append(label)
        for button in self.buttons:
            if button.label and button.label not in values:
                values.append(button.label)
        return tuple(values)


BUTTON_RE = re.compile(r"@B\[(?P<body>[^\]]*)\]", re.IGNORECASE | re.DOTALL)
STRING_LITERAL_RE = re.compile(r"""(?:"([^"\\]*(?:\\.[^"\\]*)*)"|'([^'\\]*(?:\\.[^'\\]*)*)')""")


def window_context_for_reference(reference: CodeReference, current_label: str = "") -> PreviewWindowContext | None:
    call_name = (reference.call_name or "").casefold()
    if not call_name:
        return None
    if not _is_window_call(call_name):
        return None
    arguments = tuple(str(argument) for argument in reference.arguments)
    buttons = _buttons_from_arguments(arguments)
    labels_by_arg = _labels_by_argument(arguments)
    button_label_set = {button.label for button in buttons if button.label}
    header_label, body_label = _header_body_labels(call_name, labels_by_arg, button_label_set, current_label)
    if not header_label and not body_label and not buttons:
        return None
    background = _background_for_call(call_name)
    return PreviewWindowContext(
        kind=_kind_for_call(call_name),
        background=background,
        default_color=DARK_PANEL_TEXT if background == "dark_panel" else PARCHMENT_TEXT,
        header_label=header_label,
        body_label=body_label,
        buttons=buttons,
    )


def best_window_context(references: tuple[CodeReference, ...], current_label: str = "") -> PreviewWindowContext | None:
    normalized = normalize_label(current_label) if current_label else ""
    for reference in references:
        context = window_context_for_reference(reference, normalized)
        if context is not None and (not normalized or normalized in context.labels):
            return context
    for reference in references:
        context = window_context_for_reference(reference, normalized)
        if context is not None:
            return context
    return None


def _is_window_call(call_name: str) -> bool:
    return (
        call_name in {
            "msgbox",
            "msgboxnowait",
            "msgnews",
            "msgnewsnowait",
            "msgquick",
            "msgquest",
            "msgmeasure",
            "msgsay",
            "msgsaynowait",
            "msgsayinteraction",
            "showtutorialboxnowait",
        }
        or call_name.startswith("feedback_message")
    )


def _kind_for_call(call_name: str) -> str:
    if call_name.startswith("feedback_message"):
        return "feedback"
    if call_name in {"msgquick", "msgsay", "msgsaynowait", "msgsayinteraction"}:
        return "short"
    if call_name in {"msgnews", "msgnewsnowait"}:
        return "news"
    if call_name == "msgquest":
        return "quest"
    return "message"


def _background_for_call(call_name: str) -> str:
    if call_name in {"msgquick", "msgsay", "msgsaynowait", "msgsayinteraction"}:
        return "dark_panel"
    return "parchment"


def _labels_by_argument(arguments: tuple[str, ...]) -> list[tuple[int, tuple[str, ...]]]:
    labels: list[tuple[int, tuple[str, ...]]] = []
    for index, argument in enumerate(arguments):
        found = tuple(normalize_label(match.group(0)) for match in LABEL_RE.finditer(argument))
        if found:
            labels.append((index, found))
    return labels


def _buttons_from_arguments(arguments: tuple[str, ...]) -> tuple[PreviewWindowButton, ...]:
    buttons: list[PreviewWindowButton] = []
    for argument in arguments:
        for match in BUTTON_RE.finditer(argument):
            parts = _split_button_parts(match.group("body"))
            identifier = parts[0].strip() if parts else ""
            label = ""
            text = ""
            for part in parts[1:]:
                label_match = LABEL_RE.search(part)
                if label_match is not None:
                    label = normalize_label(label_match.group(0))
                    break
                literal = _literal_text(part)
                if literal:
                    text = literal
            buttons.append(PreviewWindowButton(identifier=identifier, label=label, text=text))
    return tuple(buttons)


def _split_button_parts(value: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
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
            continue
        if char in "([":
            depth += 1
            continue
        if char in ")]" and depth:
            depth -= 1
            continue
        if char == "," and depth == 0:
            parts.append(value[start:index].strip())
            start = index + 1
    parts.append(value[start:].strip())
    return parts


def _literal_text(value: str) -> str:
    match = STRING_LITERAL_RE.search(value.strip())
    if match is None:
        return ""
    return (match.group(1) or match.group(2) or "").strip()


def _header_body_labels(
    call_name: str,
    labels_by_arg: list[tuple[int, tuple[str, ...]]],
    button_labels: set[str],
    current_label: str,
) -> tuple[str, str]:
    candidates: list[tuple[int, str]] = []
    for argument_index, labels in labels_by_arg:
        for label in labels:
            if label not in button_labels:
                candidates.append((argument_index, label))
    if not candidates:
        return "", ""
    if call_name.startswith("feedback_message"):
        return _labels_from_first_two(candidates)
    if call_name in {"msgquick", "msgsay", "msgsaynowait", "msgsayinteraction", "msgmeasure"}:
        return "", _nearest_or_first_label(candidates, current_label)
    if call_name in {"msgbox", "msgboxnowait", "msgnews", "msgnewsnowait", "msgquest", "showtutorialboxnowait"}:
        return _labels_from_first_two(candidates)
    return _labels_from_first_two(candidates)


def _labels_from_first_two(candidates: list[tuple[int, str]]) -> tuple[str, str]:
    unique: list[str] = []
    for _, label in sorted(candidates, key=lambda item: item[0]):
        if label not in unique:
            unique.append(label)
        if len(unique) >= 2:
            break
    if len(unique) == 1:
        if _looks_like_head(unique[0]):
            return unique[0], ""
        return "", unique[0]
    return unique[0], unique[1]


def _nearest_or_first_label(candidates: list[tuple[int, str]], current_label: str) -> str:
    if current_label:
        for _, label in candidates:
            if label == current_label:
                return label
    return candidates[0][1]


def _looks_like_head(label: str) -> bool:
    return "_head" in label or label.endswith("head")
