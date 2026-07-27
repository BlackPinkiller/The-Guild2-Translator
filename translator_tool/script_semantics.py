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
    runtime_argument_kinds: tuple[tuple[str, ...], ...] = ()
    resolved_arguments: tuple[tuple[str, ...], ...] = ()
    match_kind: str = "exact"
    confidence: int = 0


@dataclass(frozen=True)
class FunctionReturnLabel:
    aliases: tuple[str, ...]
    label: str
    position: int
    match_kind: str
    confidence: int = 62


@dataclass(frozen=True)
class ExternalCallFlow:
    alias: str
    position: int
    call_name: str
    argument_index: int
    arguments: tuple[str, ...]
    role: str
    runtime_arguments: tuple[str, ...]
    runtime_argument_values: tuple[tuple[str, ...], ...]
    runtime_argument_kinds: tuple[tuple[str, ...], ...]
    resolved_arguments: tuple[tuple[str, ...], ...]
    confidence: int = 76


@dataclass(frozen=True)
class SemanticValue:
    kind: str
    text: str


@dataclass(frozen=True)
class FunctionValueSummary:
    aliases: tuple[str, ...]
    parameters: tuple[str, ...]
    return_values: tuple[tuple[SemanticValue, ...], ...]


@dataclass(frozen=True)
class ScriptSemanticFacts:
    uses: tuple[SemanticLabelUse, ...]
    return_labels: tuple[FunctionReturnLabel, ...]
    external_flows: tuple[ExternalCallFlow, ...]
    function_summaries: tuple[FunctionValueSummary, ...]


@dataclass
class _Analysis:
    text: str
    path: Path
    tokens: tuple[Token, ...]
    token_starts: tuple[int, ...]
    functions: tuple[ScriptFunction, ...]
    calls: tuple[ScriptCall, ...]
    call_starts: tuple[int, ...]
    assignments: tuple[Assignment, ...]
    assignments_by_name: dict[tuple[int | None, str], tuple[Assignment, ...]]
    calls_by_alias: dict[str, tuple[int, ...]]
    functions_by_alias: dict[str, tuple[int, ...]]
    returns_by_function: dict[int, tuple[tuple[int, int, int], ...]]
    branch_paths: dict[int, tuple[tuple[int, int], ...]]
    alias_type_events: dict[
        tuple[int | None, str],
        tuple[tuple[int, int, str], ...],
    ]


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
SUMMARY_PARAMETER_PREFIX = "\x1f"
_SEMANTIC_KIND_PREFIX = "\x1e"
SEMANTIC_EXPRESSION = "expression"
SEMANTIC_BUILDING = "building"
SEMANTIC_CHARACTER = "character"
SEMANTIC_DYNASTY = "dynasty"
SEMANTIC_LABEL = "label"
SEMANTIC_NUMBER = "number"
SEMANTIC_SETTLEMENT = "settlement"
SEMANTIC_STRUCTURE = "structure"
SEMANTIC_TEXT = "text"

_NATIVE_OBJECT_RETURN_KINDS = {
    "getdynastyid": SEMANTIC_DYNASTY,
    "gethomebuildingid": SEMANTIC_BUILDING,
    "getinsidebuildingid": SEMANTIC_BUILDING,
    "getsettlementid": SEMANTIC_SETTLEMENT,
    "scenariogetimperialcapitalid": SEMANTIC_SETTLEMENT,
    "simgetid": SEMANTIC_CHARACTER,
    "simgetservantdynastyid": SEMANTIC_DYNASTY,
    "simgetworkingplaceid": SEMANTIC_BUILDING,
    "squadgetleaderid": SEMANTIC_CHARACTER,
}

_NATIVE_ALIAS_OUTPUT_KINDS = {
    "buildinggetcity": ((1, SEMANTIC_SETTLEMENT),),
    "citygetrandombuilding": ((6, SEMANTIC_BUILDING),),
    "dynastygetmember": ((2, SEMANTIC_CHARACTER),),
    "getdynasty": ((1, SEMANTIC_DYNASTY),),
    "gethomebuilding": ((1, SEMANTIC_BUILDING),),
    "getinsidebuilding": ((1, SEMANTIC_BUILDING),),
    "getsettlement": ((1, SEMANTIC_SETTLEMENT),),
}

_FIXED_ALIAS_KINDS = {
    "building": SEMANTIC_BUILDING,
    "city": SEMANTIC_SETTLEMENT,
    "dynasty": SEMANTIC_DYNASTY,
    "settlement": SEMANTIC_SETTLEMENT,
    "sim": SEMANTIC_CHARACTER,
    "workbuilding": SEMANTIC_BUILDING,
}


def analyze_script(
    text: str,
    path: Path,
    *,
    label_catalog: frozenset[str] = frozenset(),
) -> tuple[SemanticLabelUse, ...]:
    return analyze_script_facts(text, path, label_catalog=label_catalog).uses


def analyze_script_facts(
    text: str,
    path: Path,
    *,
    label_catalog: frozenset[str] = frozenset(),
) -> ScriptSemanticFacts:
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
    functions_by_alias: dict[str, list[int]] = {}
    for index, function in enumerate(functions):
        for alias in function.aliases:
            functions_by_alias.setdefault(alias.casefold(), []).append(index)
    token_starts = tuple(token.start for token in tokens)
    branch_path_tokens = frozenset(
        (
            *(assignment.token_start for assignment in assignments),
            *(
                bisect.bisect_left(token_starts, call.start)
                for call in calls
            ),
        )
    )
    analysis = _Analysis(
        text,
        path,
        tokens,
        token_starts,
        functions,
        calls,
        tuple(call.start for call in calls),
        assignments,
        {key: tuple(values) for key, values in assignments_by_name.items()},
        {name: tuple(indices) for name, indices in calls_by_alias.items()},
        {name: tuple(indices) for name, indices in functions_by_alias.items()},
        _return_expressions_by_function(tokens, functions),
        _conditional_branch_paths(tokens, branch_path_tokens),
        _native_alias_type_events(tokens, calls, token_starts),
    )
    uses: list[SemanticLabelUse] = []
    external_flows: list[ExternalCallFlow] = []
    claimed_ranges: list[tuple[int, int]] = []
    for call in calls:
        contract = call_contract(call.name)
        resolved_arguments = _resolved_call_arguments(
            analysis,
            call,
            label_catalog,
        )
        for argument_index, ((start, end), expression) in enumerate(zip(call.argument_spans, call.arguments)):
            values = _label_values_for_argument(
                analysis,
                call,
                argument_index,
                start,
                end,
                label_catalog,
            )
            role = (
                contract.role_for(argument_index, expression)
                if contract is not None
                else ("button" if "@B[" in expression else "template")
            )
            runtime_start = contract.runtime_start if contract is not None else argument_index + 1
            runtime_arguments = call.arguments[runtime_start:]
            runtime_argument_values, runtime_argument_kinds = (
                _runtime_argument_semantics(analysis, call, runtime_start)
            )
            path_sensitive = bool(values and runtime_arguments) and (
                _argument_has_conditional_assignments(
                    analysis,
                    start,
                    end,
                    call.start,
                    call.function_index,
                )
            )
            if contract is not None or role == "button":
                for alias, _dependency_position in _external_calls_for_argument(
                    analysis,
                    start,
                    end,
                    call.start,
                    call.function_index,
                    set(),
                ):
                    external_flows.append(
                        ExternalCallFlow(
                            alias=alias,
                            position=call.start,
                            call_name=call.name,
                            argument_index=argument_index,
                            arguments=call.arguments,
                            role=role,
                            runtime_arguments=runtime_arguments,
                            runtime_argument_values=runtime_argument_values,
                            runtime_argument_kinds=runtime_argument_kinds,
                            resolved_arguments=resolved_arguments,
                        )
                    )
            origin_paths_by_label = (
                _label_origin_branch_map(
                    analysis,
                    call,
                    argument_index,
                    label_catalog,
                )
                if path_sensitive
                else {}
            )
            for label, position, match_kind, confidence in values:
                if path_sensitive:
                    origin_paths = origin_paths_by_label.get(label, ())
                    (
                        contextual_runtime_values,
                        contextual_runtime_kinds,
                    ) = _runtime_argument_semantics_for_paths(
                        analysis,
                        call,
                        runtime_start,
                        origin_paths
                        or (_branch_path_at_position(analysis, call.start),),
                    )
                else:
                    contextual_runtime_values = runtime_argument_values
                    contextual_runtime_kinds = runtime_argument_kinds
                uses.append(
                    SemanticLabelUse(
                        label=label,
                        position=position,
                        call_name=call.name,
                        argument_index=argument_index,
                        arguments=call.arguments,
                        role=role,
                        runtime_arguments=runtime_arguments,
                        runtime_argument_values=contextual_runtime_values,
                        runtime_argument_kinds=contextual_runtime_kinds,
                        resolved_arguments=resolved_arguments,
                        match_kind=match_kind,
                        confidence=confidence,
                    )
                )
            if values:
                claimed_ranges.append((start, end))

    return_labels = _function_return_labels(analysis, label_catalog)
    for returned in return_labels:
        uses.append(
            SemanticLabelUse(
                label=returned.label,
                position=returned.position,
                role="return_value",
                match_kind=returned.match_kind,
                confidence=45,
            )
        )
    claimed_ranges.extend(_return_expression_ranges(analysis))

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
    return ScriptSemanticFacts(
        _dedupe_uses(uses),
        return_labels,
        tuple(dict.fromkeys(external_flows)),
        _function_value_summaries(analysis),
    )


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


def _conditional_branch_paths(
    tokens: tuple[Token, ...],
    tracked_indices: frozenset[int],
) -> dict[int, tuple[tuple[int, int], ...]]:
    """Map tokens to lexical conditional arms for lightweight path-sensitive flow."""
    stack: list[list[object]] = []
    paths: dict[int, tuple[tuple[int, int], ...]] = {}
    for index, token in enumerate(tokens):
        value = token.value.casefold() if token.kind == "identifier" else ""
        if value in {"elseif", "else"}:
            if stack and stack[-1][0] == "if":
                stack[-1][2] = int(stack[-1][2]) + 1
        elif value == "end":
            if stack:
                stack.pop()
        elif value == "until":
            if stack and stack[-1][0] == "repeat":
                stack.pop()
        elif value == "if":
            stack.append(["if", index, 0, False])
        elif value in {"for", "while"}:
            stack.append([value, index, 0, True])
        elif value == "function":
            stack.append(["function", index, 0, False])
        elif value == "repeat":
            stack.append(["repeat", index, 0, False])
        elif value == "do":
            if stack and stack[-1][3] is True:
                stack[-1][3] = False
            else:
                stack.append(["do", index, 0, False])
        if index in tracked_indices:
            paths[index] = tuple(
                (int(frame[1]), int(frame[2]))
                for frame in stack
                if frame[0] == "if"
            )
    return paths


def _branch_path_for_token(
    analysis: _Analysis,
    token_index: int,
) -> tuple[tuple[int, int], ...]:
    return analysis.branch_paths.get(token_index, ())


def _branch_path_at_position(
    analysis: _Analysis,
    position: int,
) -> tuple[tuple[int, int], ...]:
    token_index = bisect.bisect_right(analysis.token_starts, position) - 1
    return _branch_path_for_token(analysis, token_index)


def _branches_compatible(
    left: tuple[tuple[int, int], ...],
    right: tuple[tuple[int, int], ...],
) -> bool:
    right_arms = dict(right)
    return all(
        branch not in right_arms or right_arms[branch] == arm
        for branch, arm in left
    )


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
            if (token.kind == "symbol" and token.value == ";") or (
                token.kind == "identifier" and value in {
                "then",
                "do",
                "end",
                "elseif",
                "else",
                "until",
                "local",
                "return",
                }
            ):
                break
            if token.line > base_line and previous != "..":
                break
        if token.kind == "symbol" and token.value in {"(", "[", "{"}:
            depth += 1
        elif token.kind == "symbol" and token.value in {")", "]", "}"}:
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
                candidates.append(
                    (
                        _semantic_label_identity(label, kind, catalog),
                        start + relative,
                        kind,
                        confidence,
                    )
                )
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
                kind = _literal_match_kind(label, catalog)
                candidates.append(
                    (
                        _semantic_label_identity(label, kind, catalog),
                        token.start + relative,
                        kind,
                        88 if kind == "family" else 100,
                    )
                )
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


def _label_origin_branch_map(
    analysis: _Analysis,
    call: ScriptCall,
    argument_index: int,
    catalog: frozenset[str],
) -> dict[str, tuple[tuple[tuple[int, int], ...], ...]]:
    if not (0 <= argument_index < len(call.argument_spans)):
        return {}
    start, end = call.argument_spans[argument_index]
    token_indices = _tokens_in_span(analysis, start, end)
    if not token_indices:
        return {}
    found: dict[str, list[tuple[tuple[int, int], ...]]] = {}
    _collect_label_origin_paths(
        analysis,
        token_indices[0],
        token_indices[-1] + 1,
        call.start,
        call.function_index,
        catalog,
        set(),
        found,
    )
    return {
        label: tuple(dict.fromkeys(paths))
        for label, paths in found.items()
    }


def _argument_has_conditional_assignments(
    analysis: _Analysis,
    start: int,
    end: int,
    position: int,
    function_index: int | None,
) -> bool:
    token_indices = _tokens_in_span(analysis, start, end)
    if not token_indices:
        return False
    for name in _dependency_names(
        analysis.tokens,
        token_indices[0],
        token_indices[-1] + 1,
    ):
        for assignment in analysis.assignments_by_name.get(
            (function_index, name.casefold()),
            (),
        ):
            if (
                assignment.position < position
                and _branch_path_for_token(analysis, assignment.token_start)
            ):
                return True
    return False


def _collect_label_origin_paths(
    analysis: _Analysis,
    start: int,
    end: int,
    position: int,
    function_index: int | None,
    catalog: frozenset[str],
    resolving: set[tuple[str, int | None]],
    found: dict[str, list[tuple[tuple[int, int], ...]]],
) -> None:
    for name in _dependency_names(analysis.tokens, start, end):
        key = (name.casefold(), function_index)
        if key in resolving or len(resolving) >= 8:
            continue
        next_resolving = set(resolving)
        next_resolving.add(key)
        for assignment in analysis.assignments_by_name.get(
            (function_index, name.casefold()),
            (),
        ):
            if assignment.position >= position:
                continue
            resolved = _evaluate_tokens(
                analysis,
                assignment.token_start,
                assignment.token_end,
                assignment.position,
                function_index,
                next_resolving,
            )
            assignment_path = _branch_path_for_token(
                analysis,
                assignment.token_start,
            )
            for value in resolved:
                for candidate, _relative in _literal_labels(
                    value,
                    catalog,
                    allow_patterns=True,
                ):
                    kind = (
                        "dynamic"
                        if "*" in candidate
                        else _literal_match_kind(candidate, catalog)
                    )
                    identity = _semantic_label_identity(
                        candidate,
                        kind,
                        catalog,
                    )
                    found.setdefault(identity, []).append(assignment_path)
            _collect_label_origin_paths(
                analysis,
                assignment.token_start,
                assignment.token_end,
                assignment.position,
                function_index,
                catalog,
                next_resolving,
                found,
            )


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


def _semantic_label_identity(
    label: str,
    kind: str,
    catalog: frozenset[str],
) -> str:
    if kind != "family" or not catalog or "_+" in label:
        return label
    variants = {label, label.lstrip("_")}
    if variants & _catalog_family_bases(catalog):
        return label + "_+*"
    return label


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
    parameter_bindings: dict[tuple[int, str], tuple[str, ...]] | None = None,
    required_branches: tuple[tuple[int, int], ...] = (),
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
            part_values = _evaluate_tokens(
                analysis,
                part_start,
                part_end,
                position,
                function_index,
                resolving,
                parameter_bindings,
                required_branches,
            )
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
    call = _call_for_token_range(analysis, start, end)
    if call is not None:
        return _evaluate_local_function_call(
            analysis,
            call,
            position,
            function_index,
            resolving,
            parameter_bindings,
            required_branches,
        )
    if end - start == 1:
        token = analysis.tokens[start]
        if token.kind in {"string", "number"}:
            return (token.value,)
        if token.kind == "identifier":
            if token.value.casefold() in {"true", "false"}:
                return (token.value.casefold(),)
            return _resolve_variable(
                analysis,
                token.value,
                position,
                function_index,
                resolving,
                parameter_bindings,
                required_branches,
            )
    lvalue = _lvalue_name(analysis.tokens, start, end)
    if lvalue:
        return _resolve_variable(
            analysis,
            lvalue,
            position,
            function_index,
            resolving,
            parameter_bindings,
            required_branches,
        )
    return ()


def _call_for_token_range(
    analysis: _Analysis,
    start: int,
    end: int,
) -> ScriptCall | None:
    if start >= end:
        return None
    expression_start = analysis.tokens[start].start
    expression_end = analysis.tokens[end - 1].end
    call_index = bisect.bisect_left(analysis.call_starts, expression_start)
    if call_index >= len(analysis.calls):
        return None
    call = analysis.calls[call_index]
    return call if call.start == expression_start and call.end == expression_end else None


def _evaluate_local_function_call(
    analysis: _Analysis,
    call: ScriptCall,
    position: int,
    function_index: int | None,
    resolving: set[tuple[str, int | None]],
    parameter_bindings: dict[tuple[int, str], tuple[str, ...]] | None,
    required_branches: tuple[tuple[int, int], ...],
) -> tuple[str, ...]:
    if re.split(r"[.:]", call.name)[-1].casefold() == "getid":
        object_kinds = _getid_object_kinds(
            analysis,
            call,
            required_branches,
        )
        if object_kinds:
            return tuple(
                _semantic_candidate(SemanticValue(kind, ""))
                for kind in object_kinds
            )
    target_indices = analysis.functions_by_alias.get(call.name.casefold(), ())
    if not target_indices and native_semantic_function_name(call.name) is None:
        return ()
    argument_values: list[tuple[str, ...]] = []
    for start, end in call.argument_spans:
        token_indices = _tokens_in_span(analysis, start, end)
        if not token_indices:
            argument_values.append(())
            continue
        argument_values.append(
            _evaluate_tokens(
                analysis,
                token_indices[0],
                token_indices[-1] + 1,
                position,
                function_index,
                resolving,
                parameter_bindings,
                required_branches,
            )
        )
    if not target_indices:
        semantic_arguments = tuple(
            tuple(semantic_literal(value) for value in candidates)
            if candidates
            else (SemanticValue(SEMANTIC_EXPRESSION, "*"),)
            for candidates in argument_values
        )
        resolved = resolve_native_semantic_function(call.name, semantic_arguments)
        if not resolved:
            return ()
        return tuple(_semantic_candidate(value) for value in resolved[0])

    values: list[str] = []
    for target_index in target_indices:
        target = analysis.functions[target_index]
        recursion_key = (f"@call:{target.name.casefold()}", target_index)
        if recursion_key in resolving or len(resolving) >= 8:
            continue
        next_resolving = set(resolving)
        next_resolving.add(recursion_key)
        next_bindings = dict(parameter_bindings or {})
        for parameter_index, parameter in enumerate(target.parameters):
            next_bindings[(target_index, parameter.casefold())] = (
                argument_values[parameter_index]
                if parameter_index < len(argument_values)
                else ()
            )
        for return_start, return_end, return_position in analysis.returns_by_function.get(target_index, ()):
            parts = _split_token_range(analysis.tokens, return_start, return_end, ",")
            if not parts:
                continue
            first_start, first_end = parts[0]
            values.extend(
                _evaluate_tokens(
                    analysis,
                    first_start,
                    first_end,
                    return_position,
                    target_index,
                    next_resolving,
                    next_bindings,
                    (),
                )
            )
    return tuple(dict.fromkeys(values))[:64]


def _getid_object_kinds(
    analysis: _Analysis,
    call: ScriptCall,
    required_branches: tuple[tuple[int, int], ...],
) -> tuple[str, ...]:
    if not call.argument_spans:
        return ()
    start, end = call.argument_spans[0]
    token_indices = _tokens_in_span(analysis, start, end)
    if len(token_indices) != 1:
        return ()
    token = analysis.tokens[token_indices[0]]
    if token.kind != "string":
        return ()
    alias = token.value.casefold()
    kinds: list[str] = []
    fixed = _FIXED_ALIAS_KINDS.get(alias)
    if fixed is not None:
        kinds.append(fixed)
    for position, producer_token, kind in analysis.alias_type_events.get(
        (call.function_index, alias),
        (),
    ):
        if position >= call.start:
            break
        if required_branches and not _branches_compatible(
            _branch_path_for_token(analysis, producer_token),
            required_branches,
        ):
            continue
        kinds.append(kind)
    return tuple(dict.fromkeys(kinds))[:64]


def _native_alias_type_events(
    tokens: tuple[Token, ...],
    calls: tuple[ScriptCall, ...],
    token_starts: tuple[int, ...],
) -> dict[tuple[int | None, str], tuple[tuple[int, int, str], ...]]:
    grouped: dict[
        tuple[int | None, str],
        list[tuple[int, int, str]],
    ] = {}
    for call in calls:
        name = re.split(r"[.:]", call.name)[-1].casefold()
        for argument_index, kind in _NATIVE_ALIAS_OUTPUT_KINDS.get(name, ()):
            if argument_index >= len(call.argument_spans):
                continue
            start, end = call.argument_spans[argument_index]
            first = bisect.bisect_left(token_starts, start)
            last = bisect.bisect_left(token_starts, end)
            indices = tuple(
                index
                for index in range(first, last)
                if tokens[index].end <= end
            )
            if len(indices) != 1 or tokens[indices[0]].kind != "string":
                continue
            grouped.setdefault(
                (call.function_index, tokens[indices[0]].value.casefold()),
                [],
            ).append(
                (
                    call.start,
                    bisect.bisect_left(token_starts, call.start),
                    kind,
                )
            )
    return {key: tuple(events) for key, events in grouped.items()}


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
                    candidates.append(
                        (
                            _semantic_label_identity(label, kind, catalog),
                            assignment.position + relative,
                            kind,
                            82,
                        )
                    )
            for token_index in range(assignment.token_start, assignment.token_end):
                token = analysis.tokens[token_index]
                if token.kind != "string":
                    continue
                for label, relative in _literal_labels(token.value, catalog):
                    kind = "dynamic" if "*" in label else _literal_match_kind(label, catalog)
                    candidates.append(
                        (
                            _semantic_label_identity(label, kind, catalog),
                            token.start + relative,
                            kind,
                            82,
                        )
                    )
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


def _external_calls_for_argument(
    analysis: _Analysis,
    start: int,
    end: int,
    position: int,
    function_index: int | None,
    resolving: set[tuple[str, int | None]],
) -> tuple[tuple[str, int], ...]:
    values: list[tuple[str, int]] = []
    first_call = bisect.bisect_left(analysis.call_starts, start)
    last_call = bisect.bisect_left(analysis.call_starts, end)
    values.extend(
        (analysis.calls[index].name.casefold(), analysis.calls[index].start)
        for index in range(first_call, last_call)
    )
    token_indices = _tokens_in_span(analysis, start, end)
    if not token_indices:
        return tuple(dict.fromkeys(values))
    for name in _dependency_names(analysis.tokens, token_indices[0], token_indices[-1] + 1):
        key = (name.casefold(), function_index)
        if key in resolving or len(resolving) >= 8:
            continue
        next_resolving = set(resolving)
        next_resolving.add(key)
        for assignment in analysis.assignments_by_name.get((function_index, name.casefold()), ()):
            if assignment.position >= position:
                continue
            assignment_start = analysis.tokens[assignment.token_start].start
            assignment_end = analysis.tokens[assignment.token_end - 1].end
            values.extend(
                _external_calls_for_argument(
                    analysis,
                    assignment_start,
                    assignment_end,
                    assignment.position,
                    function_index,
                    next_resolving,
                )
            )
    return tuple(dict.fromkeys(values))


def _function_return_labels(
    analysis: _Analysis,
    catalog: frozenset[str],
) -> tuple[FunctionReturnLabel, ...]:
    values: list[FunctionReturnLabel] = []
    for function_index, start, end, position in _return_expressions(analysis):
        function = analysis.functions[function_index]
        for part_start, part_end in _split_token_range(analysis.tokens, start, end, ","):
            resolved = _evaluate_tokens(
                analysis,
                part_start,
                part_end,
                position,
                function_index,
                set(),
            )
            expression = analysis.text[
                analysis.tokens[part_start].start : analysis.tokens[part_end - 1].end
            ]
            dynamic = ".." in expression
            for resolved_value in resolved:
                for label, _relative in _literal_labels(
                    resolved_value,
                    catalog,
                    allow_patterns=True,
                ):
                    match_kind = (
                        "dynamic"
                        if dynamic or "*" in label
                        else _literal_match_kind(label, catalog)
                    )
                    values.append(
                        FunctionReturnLabel(
                            aliases=function.aliases,
                            label=label,
                            position=position,
                            match_kind=match_kind,
                        )
                    )
    return tuple(dict.fromkeys(values))


def _return_expression_ranges(analysis: _Analysis) -> tuple[tuple[int, int], ...]:
    return tuple(
        (
            analysis.tokens[start].start,
            analysis.tokens[end - 1].end,
        )
        for _function_index, start, end, _position in _return_expressions(analysis)
        if start < end
    )


def _return_expressions(
    analysis: _Analysis,
) -> tuple[tuple[int, int, int, int], ...]:
    return tuple(
        (function_index, start, end, position)
        for function_index, expressions in analysis.returns_by_function.items()
        for start, end, position in expressions
    )


def _return_expressions_by_function(
    tokens: tuple[Token, ...],
    functions: tuple[ScriptFunction, ...],
) -> dict[int, tuple[tuple[int, int, int], ...]]:
    values: list[tuple[int, int, int, int]] = []
    for index, token in enumerate(tokens[:-1]):
        if token.kind != "identifier" or token.value.casefold() != "return":
            continue
        function_index = _function_at(functions, token.start)
        if function_index is None:
            continue
        start = index + 1
        end = _expression_end(tokens, start)
        if start < end:
            values.append((function_index, start, end, token.start))
    grouped: dict[int, list[tuple[int, int, int]]] = {}
    for function_index, start, end, position in values:
        grouped.setdefault(function_index, []).append((start, end, position))
    return {function_index: tuple(expressions) for function_index, expressions in grouped.items()}


def _function_value_summaries(
    analysis: _Analysis,
) -> tuple[FunctionValueSummary, ...]:
    summaries: list[FunctionValueSummary] = []
    for function_index, function in enumerate(analysis.functions):
        returns = analysis.returns_by_function.get(function_index, ())
        if not returns:
            continue
        bindings = {
            (function_index, parameter.casefold()): (
                f"{SUMMARY_PARAMETER_PREFIX}{parameter_index}{SUMMARY_PARAMETER_PREFIX}",
            )
            for parameter_index, parameter in enumerate(function.parameters)
        }
        positions: list[list[SemanticValue]] = []
        for return_start, return_end, return_position in returns:
            return_parts = _split_token_range(
                analysis.tokens,
                return_start,
                return_end,
                ",",
            )
            for return_index, (part_start, part_end) in enumerate(return_parts):
                while len(positions) <= return_index:
                    positions.append([])
                expression = analysis.text[
                    analysis.tokens[part_start].start : analysis.tokens[part_end - 1].end
                ]
                resolved = _evaluate_tokens(
                    analysis,
                    part_start,
                    part_end,
                    return_position,
                    function_index,
                    set(),
                    bindings,
                )
                if resolved and not (
                    return_index == len(return_parts) - 1
                    and _CALL_EXPRESSION_RE.fullmatch(expression.strip())
                ):
                    candidates = tuple(
                        semantic_literal(value)
                        for value in resolved
                    )
                else:
                    for parameter_index, parameter in enumerate(function.parameters):
                        expression = re.sub(
                            rf"\b{re.escape(parameter)}\b",
                            f"{SUMMARY_PARAMETER_PREFIX}{parameter_index}{SUMMARY_PARAMETER_PREFIX}",
                            expression,
                        )
                    candidates = (
                        SemanticValue(SEMANTIC_EXPRESSION, expression),
                    )
                positions[return_index].extend(candidates)
        summaries.append(
            FunctionValueSummary(
                aliases=function.aliases,
                parameters=function.parameters,
                return_values=tuple(
                    tuple(dict.fromkeys(values))[:64]
                    for values in positions
                ),
            )
        )
    return tuple(summaries)


def semantic_literal(value: str) -> SemanticValue:
    marker_kind = _semantic_marker_kind(value)
    if marker_kind:
        return SemanticValue(marker_kind, "")
    stripped = value.strip()
    if value in {"", "$N"}:
        kind = SEMANTIC_STRUCTURE
    elif stripped.startswith(("@L_", "_")):
        kind = SEMANTIC_LABEL
    elif re.fullmatch(r"[-+]?\d+(?:\.\d+)?|true|false", stripped, re.IGNORECASE):
        kind = SEMANTIC_NUMBER
    else:
        kind = SEMANTIC_TEXT
    return SemanticValue(kind, value)


def _semantic_candidate(value: SemanticValue) -> str:
    if value.kind in _NATIVE_OBJECT_RETURN_KINDS.values() and not value.text:
        return _SEMANTIC_KIND_PREFIX + value.kind
    return value.text


def _semantic_marker_kind(value: str) -> str:
    if not value.startswith(_SEMANTIC_KIND_PREFIX):
        return ""
    kind = value[len(_SEMANTIC_KIND_PREFIX) :]
    return kind if kind in _NATIVE_OBJECT_RETURN_KINDS.values() else ""


def native_semantic_function_name(alias: str) -> str | None:
    name = re.split(r"[.:]", alias)[-1].casefold().split("_")[-1]
    if name in _NATIVE_OBJECT_RETURN_KINDS:
        return name
    if name in {
        "citylevel2label",
        "generateprivilegelistlabels",
        "getnobilitytitlelabel",
        "itemgetlabel",
    }:
        return name
    return None


def resolve_native_semantic_function(
    alias: str,
    argument_values: tuple[tuple[SemanticValue, ...], ...],
) -> tuple[tuple[SemanticValue, ...], ...] | None:
    """Apply engine function contracts shared by local and cross-file evaluation."""
    name = native_semantic_function_name(alias)
    object_kind = _NATIVE_OBJECT_RETURN_KINDS.get(name or "")
    if object_kind is not None:
        return ((SemanticValue(object_kind, ""),),)
    if name == "itemgetlabel":
        item_values = argument_values[0] if argument_values else ()
        singular_values = argument_values[1] if len(argument_values) > 1 else ()
        item_names = tuple(
            value.text
            for value in item_values
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value.text)
            and value.text.casefold() not in {"true", "false"}
        ) or ("*",)
        singular_text = {value.text.casefold() for value in singular_values}
        if singular_text and singular_text <= {"true", "1"}:
            suffixes = ("0",)
        elif singular_text and singular_text <= {"false", "0"}:
            suffixes = ("1",)
        else:
            suffixes = ("0", "1")
        return (
            tuple(
                semantic_literal(f"_ITEM_{item_name}_NAME_+{suffix}")
                for item_name in item_names
                for suffix in suffixes
            )[:64],
        )
    if name == "citylevel2label":
        levels = _semantic_integer_candidates(argument_values, 0)
        return (
            tuple(
                semantic_literal(f"_GENERAL_INFORMATION_CITY_LEVEL_NAME_+{level}")
                for level in levels
            )
            or (semantic_literal("_GENERAL_INFORMATION_CITY_LEVEL_NAME_+*"),),
        )
    if name == "getnobilitytitlelabel":
        # The engine also selects a gendered title variant. The title number
        # proves the label family, but not one exact localized member.
        return ((semantic_literal("_CHARACTERS_3_TITLES_NAME_+*"),),)
    if name != "generateprivilegelistlabels":
        return None
    positions: list[tuple[SemanticValue, ...]] = []
    for candidates in argument_values:
        privileges = tuple(
            value.text
            for value in candidates
            if value.text
            and value.text != "*"
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value.text)
        )
        if not privileges:
            continue
        positions.append(
            tuple(
                semantic_literal(f"_PRIVILEGE_{privilege}_MESSAGETEXT_+0")
                for privilege in privileges
            )
        )
        positions.append((semantic_literal("$N"),))
    if not positions:
        return None
    while len(positions) < 21:
        positions.append((semantic_literal(""),))
    return tuple(positions[:21])


def _semantic_integer_candidates(
    argument_values: tuple[tuple[SemanticValue, ...], ...],
    index: int,
) -> tuple[int, ...]:
    if not (0 <= index < len(argument_values)):
        return ()
    values: list[int] = []
    for candidate in argument_values[index]:
        if re.fullmatch(r"[-+]?\d+", candidate.text):
            value = int(candidate.text)
            if value not in values:
                values.append(value)
    return tuple(values)


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
    parameter_bindings: dict[tuple[int, str], tuple[str, ...]] | None = None,
    required_branches: tuple[tuple[int, int], ...] = (),
) -> tuple[str, ...]:
    key = (name.casefold(), function_index)
    if key in resolving or len(resolving) >= 8:
        return ()
    next_resolving = set(resolving)
    next_resolving.add(key)
    values: list[str] = list(
        _accumulated_variable_values(
            analysis,
            name,
            position,
            function_index,
            next_resolving,
            parameter_bindings,
            required_branches,
        )
    )
    for assignment in analysis.assignments_by_name.get((function_index, name.casefold()), ()):
        if (
            assignment.position < position
            and (
                not required_branches
                or _branches_compatible(
                    _branch_path_for_token(analysis, assignment.token_start),
                    required_branches,
                )
            )
        ):
            values.extend(
                _evaluate_tokens(
                    analysis,
                    assignment.token_start,
                    assignment.token_end,
                    assignment.position,
                    function_index,
                    next_resolving,
                    parameter_bindings,
                    required_branches,
                )
            )
    if values:
        return tuple(dict.fromkeys(values))[:64]
    if function_index is None or not (0 <= function_index < len(analysis.functions)):
        return ()
    bound = (parameter_bindings or {}).get((function_index, name.casefold()))
    if bound is not None:
        return bound
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
                        parameter_bindings,
                        (),
                    )
                )
    return tuple(dict.fromkeys(values))[:64]


def _accumulated_variable_values(
    analysis: _Analysis,
    name: str,
    position: int,
    function_index: int | None,
    resolving: set[tuple[str, int | None]],
    parameter_bindings: dict[tuple[int, str], tuple[str, ...]] | None = None,
    required_branches: tuple[tuple[int, int], ...] = (),
) -> tuple[str, ...]:
    current: tuple[str, ...] = ()
    accumulated = False
    for assignment in analysis.assignments_by_name.get((function_index, name.casefold()), ()):
        if (
            assignment.position >= position
            or (
                required_branches
                and not _branches_compatible(
                    _branch_path_for_token(analysis, assignment.token_start),
                    required_branches,
                )
            )
        ):
            continue
        parts = _split_token_range(
            analysis.tokens,
            assignment.token_start,
            assignment.token_end,
            "..",
        )
        self_append = bool(
            len(parts) > 1
            and _lvalue_name(analysis.tokens, parts[0][0], parts[0][1]).casefold()
            == name.casefold()
        )
        if self_append:
            suffix = _evaluate_tokens(
                analysis,
                parts[1][0],
                assignment.token_end,
                assignment.position,
                function_index,
                resolving,
                parameter_bindings,
                required_branches,
            )
            if not suffix:
                suffix = ("*",)
            bases = current or ("",)
            current = tuple(
                dict.fromkeys(left + right for left in bases for right in suffix)
            )[:64]
            accumulated = True
            continue
        resolved = _evaluate_tokens(
            analysis,
            assignment.token_start,
            assignment.token_end,
            assignment.position,
            function_index,
            resolving,
            parameter_bindings,
            required_branches,
        )
        if resolved:
            current = tuple(dict.fromkeys((*current, *resolved)))[:64]
    return current if accumulated else ()


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
            use.runtime_argument_kinds,
            use.resolved_arguments,
        )
        if key not in seen:
            seen.add(key)
            values.append(use)
    return tuple(values)


def _runtime_argument_semantics(
    analysis: _Analysis,
    call: ScriptCall,
    runtime_start: int,
    *,
    required_branches: tuple[tuple[int, int], ...] = (),
) -> tuple[tuple[tuple[str, ...], ...], tuple[tuple[str, ...], ...]]:
    values: list[tuple[str, ...]] = []
    kinds: list[tuple[str, ...]] = []
    for start, end in call.argument_spans[runtime_start:]:
        token_indices = _tokens_in_span(analysis, start, end)
        if not token_indices:
            values.append(())
            kinds.append(())
            continue
        resolved = _evaluate_tokens(
            analysis,
            token_indices[0],
            token_indices[-1] + 1,
            call.start,
            call.function_index,
            set(),
            required_branches=required_branches,
        )
        candidates = tuple(dict.fromkeys(resolved))[:64]
        values.append(
            tuple("" if _semantic_marker_kind(value) else value for value in candidates)
        )
        kinds.append(
            tuple(
                _semantic_marker_kind(value) or semantic_literal(value).kind
                for value in candidates
            )
        )
    return tuple(values), tuple(kinds)


def _runtime_argument_semantics_for_paths(
    analysis: _Analysis,
    call: ScriptCall,
    runtime_start: int,
    paths: tuple[tuple[tuple[int, int], ...], ...],
) -> tuple[tuple[tuple[str, ...], ...], tuple[tuple[str, ...], ...]]:
    merged_values: list[list[str]] = []
    merged_kinds: list[list[str]] = []
    for path in paths:
        values, kinds = _runtime_argument_semantics(
            analysis,
            call,
            runtime_start,
            required_branches=path,
        )
        while len(merged_values) < len(values):
            merged_values.append([])
            merged_kinds.append([])
        for index, candidates in enumerate(values):
            for candidate_index, candidate in enumerate(candidates):
                kind = (
                    kinds[index][candidate_index]
                    if index < len(kinds) and candidate_index < len(kinds[index])
                    else semantic_literal(candidate).kind
                )
                pair = (candidate, kind)
                existing = tuple(zip(merged_values[index], merged_kinds[index]))
                if pair not in existing and len(merged_values[index]) < 64:
                    merged_values[index].append(candidate)
                    merged_kinds[index].append(kind)
    return (
        tuple(tuple(candidates) for candidates in merged_values),
        tuple(tuple(candidates) for candidates in merged_kinds),
    )


def _semantic_value_kinds(
    values: tuple[tuple[str, ...], ...],
) -> tuple[tuple[str, ...], ...]:
    return tuple(
        tuple(semantic_literal(value).kind for value in candidates)
        for candidates in values
    )


def _resolved_call_arguments(
    analysis: _Analysis,
    call: ScriptCall,
    catalog: frozenset[str],
) -> tuple[tuple[str, ...], ...]:
    values: list[tuple[str, ...]] = []
    for start, end in call.argument_spans:
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
        values.append(
            tuple(
                dict.fromkeys(
                    _semantic_resolved_argument(value, catalog)
                    for value in resolved
                )
            )[:64]
        )
    return tuple(values)


def _semantic_resolved_argument(
    value: str,
    catalog: frozenset[str],
) -> str:
    stripped = value.strip()
    if not re.fullmatch(r"(?:@L_)?_[A-Za-z0-9_+*]+|@L_[A-Za-z0-9_+*]+", stripped):
        return value
    label = _normalize_label(stripped)
    kind = "dynamic" if "*" in label else _literal_match_kind(label, catalog)
    identity = _semantic_label_identity(label, kind, catalog)
    return "@L_" + identity.lstrip("_") if identity != label else value


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
