from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import bisect
import re


@dataclass(frozen=True)
class CallContract:
    label_roles: tuple[tuple[int, str], ...]
    runtime_start: int
    button_arguments: tuple[int, ...] = ()

    def role_for(self, argument_index: int, expression: str) -> str:
        if argument_index in self.button_arguments or "@B[" in expression:
            return "button"
        for index, role in self.label_roles:
            if index == argument_index:
                return role
        if argument_index >= self.runtime_start:
            return "runtime_label"
        return "control_label"


_FIXED_CALL_CONTRACTS: dict[str, CallContract] = {
    "msgbox": CallContract(((3, "header"), (4, "body")), 5, (2,)),
    "msgboxnowait": CallContract(((2, "header"), (3, "body")), 4),
    "msgnews": CallContract(((6, "header"), (7, "body")), 8, (2,)),
    "msgnewsnowait": CallContract(((5, "header"), (6, "body")), 7, (2,)),
    "msgquick": CallContract(((1, "body"),), 2),
    "msgmeasure": CallContract(((1, "body"),), 2),
    "msgsay": CallContract(((1, "body"),), 2),
    "msgsaynowait": CallContract(((1, "body"),), 2),
    "msgsayinteraction": CallContract(((4, "header"), (5, "body")), 6, (3,)),
    "showtutorialboxnowait": CallContract(((2, "header"), (3, "body")), 4),
    "msgquest": CallContract(((2, "header"), (3, "body")), 4),
    "oshsetmeasurecost": CallContract(((0, "body"),), 1),
    "oshsetmeasureruntime": CallContract(((0, "body"),), 1),
    "oshsetmeasurerepeat": CallContract(((0, "body"),), 1),
    "feedback_overheadskill": CallContract(((1, "body"),), 3),
    "feedback_overheadcomment": CallContract(((1, "body"),), 4),
    "showoverheadsymbol": CallContract(((4, "body"),), 5),
    "simadddatebookentry": CallContract(((4, "body"),), 5),
    "cityschedulecutsceneevent": CallContract(((6, "body"),), 7),
    "setquesttitle": CallContract(((1, "body"),), 2),
    "setquestdescription": CallContract(((1, "body"),), 2),
    "setmainquesttitle": CallContract(((1, "body"),), 2),
    "setmainquestdescription": CallContract(((1, "body"),), 2),
    "initdata": CallContract(((2, "header"), (3, "body")), 4, (0,)),
}


def call_contract(call_name: str) -> CallContract | None:
    normalized = call_name.casefold()
    if normalized.startswith("feedback_message"):
        return CallContract(((1, "header"), (2, "body")), 3)
    return _FIXED_CALL_CONTRACTS.get(normalized)


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    start: int
    end: int
    line: int
    column: int


@dataclass(frozen=True)
class ScriptFunction:
    name: str
    aliases: tuple[str, ...]
    parameters: tuple[str, ...]
    start: int
    end: int


@dataclass(frozen=True)
class ScriptCall:
    name: str
    arguments: tuple[str, ...]
    argument_spans: tuple[tuple[int, int], ...]
    start: int
    end: int
    line: int
    column: int
    function_index: int | None


@dataclass(frozen=True)
class Assignment:
    name: str
    token_start: int
    token_end: int
    position: int
    function_index: int | None


@dataclass(frozen=True)
class SemanticLabelUse:
    label: str
    position: int
    call_name: str | None = None
    argument_index: int | None = None
    arguments: tuple[str, ...] = ()
    role: str = "unattached"
    runtime_arguments: tuple[str, ...] = ()
    runtime_argument_values: tuple[tuple[str, ...], ...] = ()
    match_kind: str = "exact"
    confidence: int = 0


@dataclass
class _Analysis:
    text: str
    path: Path
    tokens: tuple[Token, ...]
    token_starts: tuple[int, ...]
    functions: tuple[ScriptFunction, ...]
    calls: tuple[ScriptCall, ...]
    assignments: tuple[Assignment, ...]
    assignments_by_name: dict[tuple[int | None, str], tuple[Assignment, ...]]
    calls_by_alias: dict[str, tuple[int, ...]]


LABEL_RE = re.compile(
    r"@L_[A-Za-z0-9_]+_\+(?![A-Za-z0-9])|"
    r"@L_[A-Za-z0-9_]+_\+[A-Za-z0-9]+|"
    r"@L_[A-Za-z0-9_]+"
)
RAW_LABEL_RE = re.compile(r"^_[A-Za-z0-9_]+(?:_\+[A-Za-z0-9]+)?$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BLOCK_OPENERS = {"function", "if", "for", "while", "repeat"}
_VARIADIC_RETURN_FUNCTIONS = {
    "generateprivilegelistlabels",
    "unpacktable",
}
_CALL_EXPRESSION_RE = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_.:]*)\s*\((?P<arguments>.*)\)$",
    re.DOTALL,
)


def analyze_script(
    text: str,
    path: Path,
    *,
    label_catalog: frozenset[str] = frozenset(),
) -> tuple[SemanticLabelUse, ...]:
    tokens = tokenize_lua(text)
    functions = _functions(tokens, path)
    calls = _calls(text, tokens, functions)
    assignments = _assignments(tokens, functions)
    assignments_by_name: dict[tuple[int | None, str], list[Assignment]] = {}
    for assignment in assignments:
        assignments_by_name.setdefault(
            (assignment.function_index, assignment.name.casefold()),
            [],
        ).append(assignment)
    calls_by_alias: dict[str, list[int]] = {}
    for index, call in enumerate(calls):
        calls_by_alias.setdefault(call.name.casefold(), []).append(index)
    analysis = _Analysis(
        text,
        path,
        tokens,
        tuple(token.start for token in tokens),
        functions,
        calls,
        assignments,
        {key: tuple(values) for key, values in assignments_by_name.items()},
        {name: tuple(indices) for name, indices in calls_by_alias.items()},
    )
    uses: list[SemanticLabelUse] = []
    claimed_ranges: list[tuple[int, int]] = []
    for call in calls:
        contract = call_contract(call.name)
        for argument_index, ((start, end), expression) in enumerate(zip(call.argument_spans, call.arguments)):
            values = _label_values_for_argument(
                analysis,
                call,
                argument_index,
                start,
                end,
                label_catalog,
            )
            if not values:
                continue
            role = (
                contract.role_for(argument_index, expression)
                if contract is not None
                else ("button" if "@B[" in expression else "template")
            )
            runtime_start = contract.runtime_start if contract is not None else argument_index + 1
            runtime_arguments = call.arguments[runtime_start:]
            runtime_argument_values = _runtime_argument_values(analysis, call, runtime_start)
            for label, position, match_kind, confidence in values:
                uses.append(
                    SemanticLabelUse(
                        label=label,
                        position=position,
                        call_name=call.name,
                        argument_index=argument_index,
                        arguments=call.arguments,
                        role=role,
                        runtime_arguments=runtime_arguments,
                        runtime_argument_values=runtime_argument_values,
                        match_kind=match_kind,
                        confidence=confidence,
                    )
                )
            claimed_ranges.append((start, end))

    claimed_ranges.sort()
    claimed_starts = tuple(start for start, _ in claimed_ranges)
    for token in tokens:
        if token.kind != "string" or _position_in_ranges(token.start, claimed_ranges, claimed_starts):
            continue
        for label, relative in _literal_labels(token.value, label_catalog):
            uses.append(
                SemanticLabelUse(
                    label=label,
                    position=token.start + relative,
                    role="assignment",
                    match_kind="exact" if "*" not in label else "dynamic",
                    confidence=35,
                )
            )
    return _dedupe_uses(uses)


def variadic_argument_pack(expression: str) -> str:
    """Return the symbolic sequence expanded by a known multi-return helper."""
    match = _CALL_EXPRESSION_RE.match(expression.strip())
    if match is None:
        return ""
    name = re.split(r"[.:]", match.group("name"))[-1].casefold()
    normalized = name.split("_")[-1]
    if normalized not in _VARIADIC_RETURN_FUNCTIONS:
        return ""
    arguments = match.group("arguments").strip()
    if normalized == "unpacktable" and arguments:
        return arguments.split(",", 1)[0].strip()
    return expression.strip()


def tokenize_lua(text: str) -> tuple[Token, ...]:
    tokens: list[Token] = []
    line_starts = _line_starts(text)
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if text.startswith("--", index):
            long_end = _long_bracket_end(text, index + 2)
            if long_end is not None:
                index = long_end
            else:
                newline = text.find("\n", index + 2)
                index = len(text) if newline < 0 else newline + 1
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            index = len(text) if newline < 0 else newline + 1
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
            continue
        if char in {'"', "'"}:
            end = _quoted_string_end(text, index, char)
            raw = text[index + 1 : max(index + 1, end - 1)]
            value = _decode_quoted(raw, char)
            line, column = _line_column(line_starts, index)
            tokens.append(Token("string", value, index, end, line, column))
            index = end
            continue
        if char == "[":
            end = _long_bracket_end(text, index)
            if end is not None:
                opener = re.match(r"\[(=*)\[", text[index:])
                assert opener is not None
                content_start = index + len(opener.group(0))
                content_end = end - len("]" + opener.group(1) + "]")
                line, column = _line_column(line_starts, index)
                tokens.append(Token("string", text[content_start:content_end], index, end, line, column))
                index = end
                continue
        identifier = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[index:])
        if identifier is not None:
            end = index + len(identifier.group(0))
            line, column = _line_column(line_starts, index)
            tokens.append(Token("identifier", identifier.group(0), index, end, line, column))
            index = end
            continue
        number = re.match(r"(?:\d+(?:\.\d*)?|\.\d+)", text[index:])
        if number is not None:
            end = index + len(number.group(0))
            line, column = _line_column(line_starts, index)
            tokens.append(Token("number", number.group(0), index, end, line, column))
            index = end
            continue
        symbol = next((value for value in ("...", "..", "==", "~=", "<=", ">=", "::") if text.startswith(value, index)), char)
        line, column = _line_column(line_starts, index)
        tokens.append(Token("symbol", symbol, index, index + len(symbol), line, column))
        index += len(symbol)
    return tuple(tokens)


def _functions(tokens: tuple[Token, ...], path: Path) -> tuple[ScriptFunction, ...]:
    stack: list[tuple[str, int, tuple[str, ...], tuple[str, ...]]] = []
    found: list[ScriptFunction] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        value = token.value.casefold() if token.kind == "identifier" else ""
        if value == "function":
            name, parameters, next_index = _function_header(tokens, index, path)
            aliases = _function_aliases(name, path)
            stack.append(("function", token.start, aliases, parameters))
            index = next_index
            continue
        if value in _BLOCK_OPENERS - {"function"}:
            stack.append((value, token.start, (), ()))
        elif value == "end":
            if stack:
                kind, start, aliases, parameters = stack.pop()
                if kind == "function":
                    found.append(ScriptFunction(aliases[0] if aliases else "", aliases, parameters, start, token.end))
        elif value == "until":
            for stack_index in range(len(stack) - 1, -1, -1):
                if stack[stack_index][0] == "repeat":
                    del stack[stack_index]
                    break
        index += 1
    for kind, start, aliases, parameters in stack:
        if kind == "function":
            found.append(ScriptFunction(aliases[0] if aliases else "", aliases, parameters, start, tokens[-1].end if tokens else 0))
    return tuple(sorted(found, key=lambda item: (item.start, -item.end)))


def _function_header(
    tokens: tuple[Token, ...],
    function_index: int,
    path: Path,
) -> tuple[str, tuple[str, ...], int]:
    index = function_index + 1
    name_parts: list[str] = []
    while index < len(tokens) and tokens[index].value != "(":
        if tokens[index].kind == "identifier":
            name_parts.append(tokens[index].value)
        index += 1
    name = name_parts[-1] if name_parts else f"anonymous_{tokens[function_index].line}"
    if index >= len(tokens):
        return name, (), index
    close = _matching_token(tokens, index, "(", ")")
    if close is None:
        return name, (), index + 1
    parameters = tuple(
        token.value
        for token in tokens[index + 1 : close]
        if token.kind == "identifier"
    )
    return name, parameters, close + 1


def _function_aliases(name: str, path: Path) -> tuple[str, ...]:
    base = name.casefold()
    stem = path.stem.casefold()
    values = [base, f"{stem}_{base}"]
    return tuple(dict.fromkeys(values))


def _calls(
    text: str,
    tokens: tuple[Token, ...],
    functions: tuple[ScriptFunction, ...],
) -> tuple[ScriptCall, ...]:
    calls: list[ScriptCall] = []
    for index, token in enumerate(tokens[:-1]):
        if token.kind != "identifier":
            continue
        if index > 0 and tokens[index - 1].kind == "identifier" and tokens[index - 1].value.casefold() == "function":
            continue
        if index > 0 and tokens[index - 1].value in {".", ":"}:
            continue
        name_index = index
        while name_index + 2 < len(tokens) and tokens[name_index + 1].value in {".", ":"} and tokens[name_index + 2].kind == "identifier":
            name_index += 2
        if name_index + 1 >= len(tokens) or tokens[name_index + 1].value != "(":
            continue
        open_index = name_index + 1
        close_index = _matching_token(tokens, open_index, "(", ")")
        if close_index is None:
            continue
        spans = _argument_spans(tokens, open_index, close_index)
        arguments = tuple(text[start:end].strip() for start, end in spans)
        function_index = _function_at(functions, token.start)
        calls.append(
            ScriptCall(
                name=tokens[name_index].value,
                arguments=arguments,
                argument_spans=spans,
                start=token.start,
                end=tokens[close_index].end,
                line=token.line,
                column=token.column,
                function_index=function_index,
            )
        )
    return tuple(calls)


def _argument_spans(tokens: tuple[Token, ...], open_index: int, close_index: int) -> tuple[tuple[int, int], ...]:
    if close_index == open_index + 1:
        return ()
    spans: list[tuple[int, int]] = []
    start_index = open_index + 1
    depth = 0
    for index in range(start_index, close_index):
        value = tokens[index].value
        if value in {"(", "[", "{"}:
            depth += 1
        elif value in {")", "]", "}"}:
            depth = max(0, depth - 1)
        elif value == "," and depth == 0:
            spans.append(_token_span(tokens, start_index, index, tokens[index].start))
            start_index = index + 1
    spans.append(_token_span(tokens, start_index, close_index, tokens[close_index].start))
    return tuple(spans)


def _token_span(tokens: tuple[Token, ...], start: int, end: int, empty_position: int) -> tuple[int, int]:
    if start >= end:
        return empty_position, empty_position
    return tokens[start].start, tokens[end - 1].end


def _assignments(tokens: tuple[Token, ...], functions: tuple[ScriptFunction, ...]) -> tuple[Assignment, ...]:
    values: list[Assignment] = []
    for index, token in enumerate(tokens[:-1]):
        if token.kind != "identifier":
            continue
        if index > 0 and tokens[index - 1].value in {".", ":", "["}:
            continue
        lvalue_end = _lvalue_end(tokens, index)
        if lvalue_end >= len(tokens) or tokens[lvalue_end].value != "=":
            continue
        name = _lvalue_name(tokens, index, lvalue_end)
        if not name:
            continue
        end = _expression_end(tokens, lvalue_end + 1)
        values.append(
            Assignment(
                name,
                lvalue_end + 1,
                end,
                token.start,
                _function_at(functions, token.start),
            )
        )
        values.extend(
            _table_field_assignments(
                tokens,
                name,
                lvalue_end + 1,
                end,
                token.start,
                _function_at(functions, token.start),
            )
        )
    return tuple(values)


def _table_field_assignments(
    tokens: tuple[Token, ...],
    name: str,
    start: int,
    end: int,
    position: int,
    function_index: int | None,
) -> tuple[Assignment, ...]:
    if start >= end or tokens[start].value != "{":
        return ()
    close = _matching_token(tokens, start, "{", "}")
    if close is None or close >= end:
        return ()
    values: list[Assignment] = []
    for ordinal, (field_start, field_end) in enumerate(
        _argument_spans(tokens, start, close),
        start=1,
    ):
        token_indices = tuple(
            index
            for index in range(start + 1, close)
            if tokens[index].start >= field_start and tokens[index].end <= field_end
        )
        if not token_indices:
            continue
        value_start = token_indices[0]
        field_name = f"{name}[{ordinal}]"
        if (
            len(token_indices) >= 3
            and tokens[token_indices[0]].kind == "identifier"
            and tokens[token_indices[1]].value == "="
        ):
            field_name = f"{name}.{tokens[token_indices[0]].value}"
            value_start = token_indices[2]
        values.append(
            Assignment(
                field_name,
                value_start,
                token_indices[-1] + 1,
                position,
                function_index,
            )
        )
    return tuple(values)


def _lvalue_end(tokens: tuple[Token, ...], start: int) -> int:
    index = start + 1
    while index < len(tokens):
        if (
            tokens[index].value == "."
            and index + 1 < len(tokens)
            and tokens[index + 1].kind == "identifier"
        ):
            index += 2
            continue
        if tokens[index].value == "[":
            close = _matching_token(tokens, index, "[", "]")
            if close is None:
                break
            index = close + 1
            continue
        break
    return index


def _lvalue_name(tokens: tuple[Token, ...], start: int, end: int) -> str:
    if start >= end or tokens[start].kind != "identifier":
        return ""
    index = start + 1
    while index < end:
        if tokens[index].value == "." and index + 1 < end and tokens[index + 1].kind == "identifier":
            index += 2
            continue
        if tokens[index].value == "[":
            close = _matching_token(tokens, index, "[", "]")
            if close is None or close >= end:
                return ""
            index = close + 1
            continue
        return ""
    return "".join(token.value for token in tokens[start:end])


def _expression_end(tokens: tuple[Token, ...], start: int) -> int:
    if start >= len(tokens):
        return start
    depth = 0
    index = start
    base_line = tokens[start].line
    while index < len(tokens):
        token = tokens[index]
        value = token.value.casefold() if token.kind == "identifier" else token.value
        if index > start and depth == 0:
            previous = tokens[index - 1].value
            if token.value == ";" or value in {"then", "do", "end", "elseif", "else", "until"}:
                break
            if token.line > base_line and previous != "..":
                break
        if token.value in {"(", "[", "{"}:
            depth += 1
        elif token.value in {")", "]", "}"}:
            if depth == 0:
                break
            depth -= 1
        index += 1
    return index


def _label_values_for_argument(
    analysis: _Analysis,
    call: ScriptCall,
    argument_index: int,
    start: int,
    end: int,
    catalog: frozenset[str],
) -> tuple[tuple[str, int, str, int], ...]:
    token_indices = _tokens_in_span(analysis, start, end)
    if not token_indices:
        return ()
    values = _evaluate_tokens(
        analysis,
        token_indices[0],
        token_indices[-1] + 1,
        call.start,
        call.function_index,
        set(),
    )
    candidates: list[tuple[str, int, str, int]] = []
    dynamic_expression = any("*" in value for value in values) or any(".." in value for value in (analysis.text[start:end],))
    if values:
        for value in values:
            for label, relative in _literal_labels(value, catalog, allow_patterns=True):
                kind = (
                    "dynamic"
                    if dynamic_expression or "*" in label
                    else _literal_match_kind(label, catalog)
                )
                confidence = 78 if kind == "dynamic" and "*" not in label else 88 if kind == "family" else 100
                candidates.append((label, start + relative, kind, confidence))
    if not candidates:
        depth = 0
        for index in token_indices:
            token = analysis.tokens[index]
            if token.value in {"(", "[", "{"}:
                depth += 1
                continue
            if token.value in {")", "]", "}"}:
                depth = max(0, depth - 1)
                continue
            if token.kind != "string" or depth:
                continue
            for label, relative in _literal_labels(token.value, catalog):
                candidates.append((label, token.start + relative, "exact", 100))
    candidates.extend(
        _dependent_label_values(
            analysis,
            token_indices[0],
            token_indices[-1] + 1,
            call.start,
            call.function_index,
            catalog,
            set(),
        )
    )
    return _best_label_candidates(candidates)


def _best_label_candidates(
    candidates: list[tuple[str, int, str, int]],
) -> tuple[tuple[str, int, str, int], ...]:
    best: dict[str, tuple[str, int, str, int]] = {}
    for candidate in candidates:
        label = candidate[0]
        current = best.get(label)
        if current is None or candidate[3] > current[3]:
            best[label] = candidate
    return tuple(best.values())


def _literal_match_kind(label: str, catalog: frozenset[str]) -> str:
    if "_+" in label:
        return "exact"
    if not catalog:
        return "family"
    variants = {label, label.lstrip("_")}
    if variants & catalog:
        return "exact"
    if variants & _catalog_family_bases(catalog):
        return "family"
    return "exact"


@lru_cache(maxsize=8)
def _catalog_family_bases(catalog: frozenset[str]) -> frozenset[str]:
    """Cache family bases by the complete immutable project-label catalog."""
    bases: set[str] = set()
    for label in catalog:
        match = re.match(r"^(.*)_\+[A-Za-z0-9]+$", label)
        if match is not None:
            bases.add(match.group(1))
    return frozenset(bases)


def _evaluate_tokens(
    analysis: _Analysis,
    start: int,
    end: int,
    position: int,
    function_index: int | None,
    resolving: set[tuple[str, int | None]],
) -> tuple[str, ...]:
    while start < end and analysis.tokens[start].value == "(":
        close = _matching_token(analysis.tokens, start, "(", ")")
        if close == end - 1:
            start += 1
            end -= 1
        else:
            break
    parts = _split_token_range(analysis.tokens, start, end, "..")
    if len(parts) > 1:
        combined = ("",)
        for part_start, part_end in parts:
            part_values = _evaluate_tokens(analysis, part_start, part_end, position, function_index, resolving)
            if not part_values:
                part_values = ("*",)
            combined = tuple(
                dict.fromkeys(
                    left + right
                    for left in combined
                    for right in part_values
                )
            )[:64]
        return combined
    if end - start == 1:
        token = analysis.tokens[start]
        if token.kind == "string":
            return (token.value,)
        if token.kind == "identifier":
            return _resolve_variable(analysis, token.value, position, function_index, resolving)
    lvalue = _lvalue_name(analysis.tokens, start, end)
    if lvalue:
        return _resolve_variable(analysis, lvalue, position, function_index, resolving)
    return ()


def _dependent_label_values(
    analysis: _Analysis,
    start: int,
    end: int,
    position: int,
    function_index: int | None,
    catalog: frozenset[str],
    resolving: set[tuple[str, int | None]],
) -> tuple[tuple[str, int, str, int], ...]:
    candidates: list[tuple[str, int, str, int]] = []
    for name in _dependency_names(analysis.tokens, start, end):
        key = (name.casefold(), function_index)
        if key in resolving or len(resolving) >= 8:
            continue
        next_resolving = set(resolving)
        next_resolving.add(key)
        for assignment in analysis.assignments_by_name.get((function_index, name.casefold()), ()):
            if assignment.position >= position:
                continue
            values = _evaluate_tokens(
                analysis,
                assignment.token_start,
                assignment.token_end,
                assignment.position,
                function_index,
                next_resolving,
            )
            for value in values:
                for label, relative in _literal_labels(value, catalog, allow_patterns=True):
                    kind = "dynamic" if "*" in label else _literal_match_kind(label, catalog)
                    candidates.append((label, assignment.position + relative, kind, 82))
            for token_index in range(assignment.token_start, assignment.token_end):
                token = analysis.tokens[token_index]
                if token.kind != "string":
                    continue
                for label, relative in _literal_labels(token.value, catalog):
                    kind = "dynamic" if "*" in label else _literal_match_kind(label, catalog)
                    candidates.append((label, token.start + relative, kind, 82))
            candidates.extend(
                _dependent_label_values(
                    analysis,
                    assignment.token_start,
                    assignment.token_end,
                    assignment.position,
                    function_index,
                    catalog,
                    next_resolving,
                )
            )
    return tuple(dict.fromkeys(candidates))


def _dependency_names(tokens: tuple[Token, ...], start: int, end: int) -> tuple[str, ...]:
    names: list[str] = []
    index = start
    while index < end:
        token = tokens[index]
        if token.kind != "identifier" or token.value.casefold() in {
            "and",
            "false",
            "local",
            "nil",
            "not",
            "or",
            "true",
        }:
            index += 1
            continue
        lvalue_end = min(_lvalue_end(tokens, index), end)
        if lvalue_end < len(tokens) and tokens[lvalue_end].value == "(":
            index += 1
            continue
        name = _lvalue_name(tokens, index, lvalue_end)
        if name and name not in names:
            names.append(name)
        index = max(index + 1, lvalue_end)
    return tuple(names)


def _resolve_variable(
    analysis: _Analysis,
    name: str,
    position: int,
    function_index: int | None,
    resolving: set[tuple[str, int | None]],
) -> tuple[str, ...]:
    key = (name.casefold(), function_index)
    if key in resolving or len(resolving) >= 8:
        return ()
    next_resolving = set(resolving)
    next_resolving.add(key)
    values: list[str] = []
    for assignment in analysis.assignments_by_name.get((function_index, name.casefold()), ()):
        if assignment.position < position:
            values.extend(
                _evaluate_tokens(
                    analysis,
                    assignment.token_start,
                    assignment.token_end,
                    assignment.position,
                    function_index,
                    next_resolving,
                )
            )
    if values:
        return tuple(dict.fromkeys(values))[:64]
    if function_index is None or not (0 <= function_index < len(analysis.functions)):
        return ()
    function = analysis.functions[function_index]
    parameter_index = next(
        (index for index, parameter in enumerate(function.parameters) if parameter.casefold() == name.casefold()),
        None,
    )
    if parameter_index is None:
        return ()
    for alias in function.aliases:
        for call_index in analysis.calls_by_alias.get(alias.casefold(), ()):
            call = analysis.calls[call_index]
            if call.start == function.start or parameter_index >= len(call.argument_spans):
                continue
            span_start, span_end = call.argument_spans[parameter_index]
            token_indices = _tokens_in_span(analysis, span_start, span_end)
            if token_indices:
                values.extend(
                    _evaluate_tokens(
                        analysis,
                        token_indices[0],
                        token_indices[-1] + 1,
                        call.start,
                        call.function_index,
                        next_resolving,
                    )
                )
    return tuple(dict.fromkeys(values))[:64]


def _literal_labels(
    value: str,
    catalog: frozenset[str],
    *,
    allow_patterns: bool = False,
) -> tuple[tuple[str, int], ...]:
    labels: list[tuple[str, int]] = []
    stripped = value.strip()
    if allow_patterns and stripped.startswith("@L_") and "*" in stripped:
        label = _normalize_label(stripped)
        if label:
            return ((label, value.find(stripped)),)
    if allow_patterns and stripped.startswith("_") and "*" in stripped:
        return ((stripped.casefold(), value.find(stripped)),)
    for match in LABEL_RE.finditer(value):
        label = _normalize_label(match.group(0))
        if label:
            labels.append((label, match.start()))
    if labels:
        return tuple(labels)
    if stripped.startswith("@L_") and (allow_patterns or "*" not in stripped):
        label = _normalize_label(stripped)
        if label:
            return ((label, value.find(stripped)),)
    if stripped.startswith("_") and (RAW_LABEL_RE.match(stripped) or (allow_patterns and "*" in stripped)):
        normalized = stripped.casefold()
        catalog_values = {normalized, normalized.lstrip("_")}
        if (
            catalog_values & catalog
            or (not catalog and "_+" in normalized)
            or (allow_patterns and "*" in normalized)
        ):
            return ((normalized, value.find(stripped)),)
    return ()


def _normalize_label(label: str) -> str:
    value = label.strip()
    if value.startswith("@L_"):
        value = value[3:]
    if value.endswith("_+"):
        value += "*"
    return value.casefold()


def _dedupe_uses(uses: list[SemanticLabelUse]) -> tuple[SemanticLabelUse, ...]:
    values: list[SemanticLabelUse] = []
    seen: set[tuple[object, ...]] = set()
    for use in uses:
        key = (
            use.label,
            use.position,
            use.call_name,
            use.argument_index,
            use.arguments,
            use.role,
            use.runtime_arguments,
            use.runtime_argument_values,
        )
        if key not in seen:
            seen.add(key)
            values.append(use)
    return tuple(values)


def _runtime_argument_values(
    analysis: _Analysis,
    call: ScriptCall,
    runtime_start: int,
) -> tuple[tuple[str, ...], ...]:
    values: list[tuple[str, ...]] = []
    for start, end in call.argument_spans[runtime_start:]:
        token_indices = _tokens_in_span(analysis, start, end)
        if not token_indices:
            values.append(())
            continue
        resolved = _evaluate_tokens(
            analysis,
            token_indices[0],
            token_indices[-1] + 1,
            call.start,
            call.function_index,
            set(),
        )
        values.append(tuple(dict.fromkeys(resolved))[:64])
    return tuple(values)


def _tokens_in_span(analysis: _Analysis, start: int, end: int) -> tuple[int, ...]:
    first = bisect.bisect_left(analysis.token_starts, start)
    last = bisect.bisect_left(analysis.token_starts, end)
    return tuple(
        index
        for index in range(first, last)
        if analysis.tokens[index].end <= end
    )


def _position_in_ranges(
    position: int,
    ranges: list[tuple[int, int]],
    starts: tuple[int, ...],
) -> bool:
    index = bisect.bisect_right(starts, position) - 1
    return index >= 0 and ranges[index][0] <= position < ranges[index][1]


def _split_token_range(
    tokens: tuple[Token, ...],
    start: int,
    end: int,
    delimiter: str,
) -> tuple[tuple[int, int], ...]:
    parts: list[tuple[int, int]] = []
    depth = 0
    part_start = start
    for index in range(start, end):
        value = tokens[index].value
        if value in {"(", "[", "{"}:
            depth += 1
        elif value in {")", "]", "}"}:
            depth = max(0, depth - 1)
        elif value == delimiter and depth == 0:
            parts.append((part_start, index))
            part_start = index + 1
    parts.append((part_start, end))
    return tuple(parts)


def _matching_token(
    tokens: tuple[Token, ...],
    open_index: int,
    opener: str,
    closer: str,
) -> int | None:
    depth = 0
    for index in range(open_index, len(tokens)):
        if tokens[index].value == opener:
            depth += 1
        elif tokens[index].value == closer:
            depth -= 1
            if depth == 0:
                return index
    return None


def _function_at(functions: tuple[ScriptFunction, ...], position: int) -> int | None:
    candidates = [
        (index, function)
        for index, function in enumerate(functions)
        if function.start <= position <= function.end
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[1].end - item[1].start)[0]


def _quoted_string_end(text: str, start: int, quote: str) -> int:
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == quote:
            return index + 1
        index += 1
    return len(text)


def _decode_quoted(value: str, quote: str) -> str:
    return value.replace("\\" + quote, quote).replace("\\\\", "\\")


def _long_bracket_end(text: str, start: int) -> int | None:
    match = re.match(r"\[(=*)\[", text[start:])
    if match is None:
        return None
    closer = "]" + match.group(1) + "]"
    end = text.find(closer, start + len(match.group(0)))
    return len(text) if end < 0 else end + len(closer)


def _line_starts(text: str) -> tuple[int, ...]:
    starts = [0]
    starts.extend(match.end() for match in re.finditer(r"\n", text))
    return tuple(starts)


def _line_column(line_starts: tuple[int, ...], position: int) -> tuple[int, int]:
    line_index = max(0, bisect.bisect_right(line_starts, position) - 1)
    return line_index + 1, position - line_starts[line_index] + 1
