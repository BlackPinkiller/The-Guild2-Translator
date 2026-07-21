from __future__ import annotations

from dataclasses import dataclass
import re

from .code_index import CodeReference, LABEL_RE, dynamic_label_patterns, normalize_label
from .script_semantics import call_contract


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
    argument_labels: tuple[str, ...] = ()

    @property
    def labels(self) -> tuple[str, ...]:
        values: list[str] = []
        for label in (self.header_label, self.body_label):
            if label and label not in values:
                values.append(label)
        for button in self.buttons:
            if button.label and button.label not in values:
                values.append(button.label)
        for label in self.argument_labels:
            if label and label not in values:
                values.append(label)
        return tuple(values)


BUTTON_RE = re.compile(r"@B\[(?P<body>[^\]]*)\]", re.IGNORECASE | re.DOTALL)
STRING_LITERAL_RE = re.compile(r"""(?:"([^"\\]*(?:\\.[^"\\]*)*)"|'([^'\\]*(?:\\.[^'\\]*)*)')""")
RESOLVED_LABEL_RE = re.compile(r"@L_[A-Za-z0-9_+*]+", re.IGNORECASE)


def window_context_for_reference(reference: CodeReference, current_label: str = "") -> PreviewWindowContext | None:
    call_name = (reference.call_name or "").casefold()
    if not call_name:
        return None
    if not _is_window_call(call_name):
        return None
    arguments = tuple(str(argument) for argument in reference.arguments)
    argument_expressions = _argument_expressions(reference, arguments)
    buttons = _buttons_from_arguments(argument_expressions)
    labels_by_arg = _labels_by_argument(argument_expressions)
    button_label_set = {button.label for button in buttons if button.label}
    header_label, body_label = _header_body_labels(call_name, labels_by_arg, button_label_set, current_label)
    referenced_label = _context_label(current_label or reference.label)
    if referenced_label:
        if reference.role == "header" and not header_label:
            header_label = referenced_label
        elif reference.role in {"body", "template"} and not body_label:
            body_label = referenced_label
        elif reference.role == "button" and not any(
            _equivalent_label(button.label, referenced_label) for button in buttons
        ):
            buttons = (*buttons, PreviewWindowButton(identifier="", label=referenced_label))
    argument_labels = _runtime_argument_labels(labels_by_arg, (header_label, body_label), button_label_set)
    if (
        referenced_label
        and reference.role == "runtime_label"
        and not any(_equivalent_label(label, referenced_label) for label in argument_labels)
    ):
        argument_labels = (*argument_labels, referenced_label)
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
        argument_labels=argument_labels,
    )


def best_window_context(references: tuple[CodeReference, ...], current_label: str = "") -> PreviewWindowContext | None:
    normalized = _context_label(current_label) if current_label else ""
    for reference in references:
        context = window_context_for_reference(reference, normalized)
        if context is not None and (not normalized or _context_has_label(context, normalized)):
            return context
    if normalized:
        return None
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


def _argument_expressions(
    reference: CodeReference,
    arguments: tuple[str, ...],
) -> tuple[tuple[str, ...], ...]:
    resolved = reference.resolved_arguments
    return tuple(
        tuple(dict.fromkeys((*(resolved[index] if index < len(resolved) else ()), argument)))
        for index, argument in enumerate(arguments)
    )


def _labels_by_argument(
    arguments: tuple[tuple[str, ...], ...],
) -> list[tuple[int, tuple[str, ...]]]:
    labels: list[tuple[int, tuple[str, ...]]] = []
    for index, expressions in enumerate(arguments):
        found: list[str] = []
        for argument in expressions:
            dynamic = dynamic_label_patterns(argument)
            candidates = dynamic or tuple(
                normalize_label(match.group(0)) for match in LABEL_RE.finditer(argument)
            )
            for label in candidates:
                if label not in found:
                    found.append(label)
        if found:
            labels.append((index, tuple(found)))
    return labels


def _context_label(label: str) -> str:
    return normalize_label(label).lstrip("_")


def _context_has_label(context: PreviewWindowContext, label: str) -> bool:
    return any(_equivalent_label(candidate, label) for candidate in context.labels)


def context_has_label(context: PreviewWindowContext, label: str) -> bool:
    return _context_has_label(context, _context_label(label))


def _buttons_from_arguments(
    arguments: tuple[tuple[str, ...], ...],
) -> tuple[PreviewWindowButton, ...]:
    buttons: list[PreviewWindowButton] = []
    for expressions in arguments:
        for argument in expressions:
            buttons.extend(_buttons_from_expression(argument))
    unique: list[PreviewWindowButton] = []
    seen: set[tuple[str, str, str]] = set()
    for button in buttons:
        key = (button.identifier, button.label, button.text)
        if key not in seen:
            seen.add(key)
            unique.append(button)
    return tuple(unique)


def _buttons_from_expression(expression: str) -> tuple[PreviewWindowButton, ...]:
    buttons: list[PreviewWindowButton] = []
    for part in _concat_parts(expression):
        buttons.extend(_direct_buttons_from_text(part))
    return tuple(buttons)


def _direct_buttons_from_text(text: str) -> tuple[PreviewWindowButton, ...]:
    buttons: list[PreviewWindowButton] = []
    for match in BUTTON_RE.finditer(text):
        body = match.group("body")
        if ".." in body:
            continue
        parts = _split_button_parts(body)
        identifier = parts[0].strip() if parts else ""
        label = ""
        text_value = ""
        for part in parts[1:]:
            label_match = RESOLVED_LABEL_RE.search(part) or LABEL_RE.search(part)
            if label_match is not None:
                label = normalize_label(label_match.group(0))
                break
            literal = _literal_text(part)
            if literal:
                text_value = literal
        buttons.append(PreviewWindowButton(identifier=identifier, label=label, text=text_value))
    return tuple(buttons)


def _concat_parts(expression: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in expression.split("..") if part.strip())


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
    contract = call_contract(call_name)
    if contract is not None:
        role_by_index = dict(contract.label_roles)
        header_candidates = [
            (index, label)
            for index, label in candidates
            if role_by_index.get(index) == "header"
        ]
        body_candidates = [
            (index, label)
            for index, label in candidates
            if role_by_index.get(index) == "body"
        ]
        header_values = (
            _specialize_candidates(header_candidates, current_label, minimum_argument_index=0)
            if header_candidates
            else []
        )
        body_values = (
            _specialize_candidates(body_candidates, current_label, minimum_argument_index=0)
            if body_candidates
            else []
        )
        if header_values or body_values:
            header = _nearest_or_first_label(header_values, current_label) if header_values else ""
            body = _nearest_or_first_label(body_values, current_label) if body_values else ""
            return header, body
    if call_name.startswith("feedback_message"):
        return _labels_from_first_two(_specialize_candidates(candidates, current_label, minimum_argument_index=1))
    if call_name == "msgsayinteraction":
        return _labels_from_first_two(_specialize_candidates(candidates, current_label, minimum_argument_index=4))
    if call_name in {"msgquick", "msgsay", "msgsaynowait", "msgmeasure"}:
        return "", _nearest_or_first_label(_specialize_candidates(candidates, current_label, minimum_argument_index=0), current_label)
    if call_name in {"msgnews", "msgnewsnowait"}:
        return _labels_from_first_two(_specialize_candidates(candidates, current_label, minimum_argument_index=5))
    if call_name in {"msgbox", "msgboxnowait", "msgquest", "showtutorialboxnowait"}:
        return _labels_from_first_two(_specialize_candidates(candidates, current_label, minimum_argument_index=2))
    return _labels_from_first_two(_specialize_candidates(candidates, current_label, minimum_argument_index=0))


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


def _specialize_candidates(
    candidates: list[tuple[int, str]],
    current_label: str,
    *,
    minimum_argument_index: int,
) -> list[tuple[int, str]]:
    suffix = _numeric_suffix(current_label)
    narrowed: list[tuple[int, str]] = []
    for argument_index, label in candidates:
        if argument_index < minimum_argument_index:
            continue
        if suffix and label.endswith("_+*"):
            label = f"{label[:-3]}{suffix}"
        narrowed.append((argument_index, label))
    return narrowed or candidates


def _runtime_argument_labels(
    labels_by_arg: list[tuple[int, tuple[str, ...]]],
    window_labels: tuple[str, str],
    button_labels: set[str],
) -> tuple[str, ...]:
    last_window_label_index = -1
    for argument_index, labels in labels_by_arg:
        for label in labels:
            if any(_equivalent_label(label, window_label) for window_label in window_labels if window_label):
                last_window_label_index = max(last_window_label_index, argument_index)
    if last_window_label_index < 0:
        return ()
    values: list[str] = []
    for argument_index, labels in labels_by_arg:
        if argument_index <= last_window_label_index:
            continue
        for label in labels:
            if label in button_labels:
                continue
            if any(_equivalent_label(label, window_label) for window_label in window_labels if window_label):
                continue
            if label not in values:
                values.append(label)
    return tuple(values)


def _equivalent_label(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left_value = left.lstrip("_")
    right_value = right.lstrip("_")
    if left_value == right_value:
        return True
    if _wildcard_label_matches(left_value, right_value):
        return True
    if _wildcard_label_matches(right_value, left_value):
        return True
    if left_value.endswith("_+*") and right_value.startswith(left_value[:-1]):
        return True
    if right_value.endswith("_+*") and left_value.startswith(right_value[:-1]):
        return True
    return False


def _wildcard_label_matches(pattern: str, label: str) -> bool:
    if "*" not in pattern:
        return False
    regex = "^" + re.escape(pattern).replace("\\*", "[a-z0-9_]+") + "$"
    return re.match(regex, label) is not None


def _numeric_suffix(label: str) -> str:
    match = re.search(r"_\+\d+$", label)
    return match.group(0) if match is not None else ""


def _nearest_or_first_label(candidates: list[tuple[int, str]], current_label: str) -> str:
    if current_label:
        for _, label in candidates:
            if label == current_label:
                return label
    return candidates[0][1]


def _looks_like_head(label: str) -> bool:
    return "_head" in label or label.endswith("head")
