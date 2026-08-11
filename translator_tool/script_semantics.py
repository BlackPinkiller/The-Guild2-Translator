from __future__ import annotations

from collections.abc import Mapping
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
    surface: str = ""
    panel_argument: int | None = None
    category_argument: int | None = None
    speaker_argument: int | None = None

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
    "msgbox": CallContract(
        ((3, "header"), (4, "body")),
        5,
        (2,),
        "messagebox",
        panel_argument=2,
    ),
    "msgboxnowait": CallContract(((2, "header"), (3, "body")), 4, surface="messagebox"),
    "msgnews": CallContract(
        ((6, "header"), (7, "body")),
        8,
        (2,),
        "news",
        panel_argument=2,
        category_argument=4,
    ),
    "msgnewsnowait": CallContract(
        ((5, "header"), (6, "body")),
        7,
        (2,),
        "news",
        panel_argument=2,
        category_argument=3,
    ),
    "msgquick": CallContract(((1, "body"),), 2, surface="quick_message"),
    "msgmeasure": CallContract(((1, "body"),), 2, surface="measure_message"),
    "msgsystem": CallContract(((1, "body"),), 2, surface="system_message"),
    "msgsay": CallContract(
        ((1, "body"),),
        2,
        surface="dialog",
        speaker_argument=0,
    ),
    "msgsaynowait": CallContract(
        ((1, "body"),),
        2,
        surface="dialog",
        speaker_argument=0,
    ),
    "msgsayinteraction": CallContract(
        ((5, "body"),),
        6,
        (3,),
        "dialog",
        panel_argument=3,
        speaker_argument=1,
    ),
    "msgsayinteractionnowait": CallContract(
        ((5, "body"),),
        6,
        (3,),
        "dialog",
        panel_argument=3,
        speaker_argument=1,
    ),
    "showtutorialbox": CallContract(
        ((7, "header"), (8, "body")),
        10,
        (6,),
        "tutorial",
        panel_argument=6,
    ),
    "showtutorialboxnowait": CallContract(
        ((6, "header"), (7, "body")),
        9,
        surface="tutorial",
    ),
    "msgquest": CallContract(
        ((3, "header"), (4, "body")),
        5,
        (2,),
        "questbox",
        panel_argument=2,
    ),
    "msgquestnowait": CallContract(
        ((2, "header"), (3, "body")),
        4,
        surface="questbox",
    ),
    "msgquestintro": CallContract(((0, "body"),), 1, surface="quest_intro"),
    "oshsetmeasurecost": CallContract(((0, "body"),), 1, surface="measure_help"),
    "oshsetmeasureruntime": CallContract(((0, "body"),), 1, surface="measure_help"),
    "oshsetmeasurerepeat": CallContract(((0, "body"),), 1, surface="measure_help"),
    "feedback_overheadskill": CallContract(((1, "body"),), 3, surface="overhead"),
    "feedback_overheadcomment": CallContract(((1, "body"),), 4, surface="overhead"),
    "showoverheadsymbol": CallContract(((4, "body"),), 5, surface="overhead"),
    "simadddatebookentry": CallContract(
        ((3, "header"), (4, "body")),
        5,
        surface="datebook",
    ),
    "cityschedulecutsceneevent": CallContract(
        ((6, "body"),),
        7,
        surface="city_schedule",
    ),
    "setquesttitle": CallContract(((0, "header"),), 1, surface="questbook"),
    "setquestdescription": CallContract(((0, "body"),), 2, surface="questbook"),
    "setquestdescriptionbyquestname": CallContract(
        ((2, "body"),),
        4,
        surface="questbook",
    ),
    "setmainquesttitle": CallContract(((1, "header"),), 2, surface="questbook"),
    "setmainquestdescription": CallContract(((1, "body"),), 2, surface="questbook"),
    "initdata": CallContract(
        ((2, "header"), (3, "body")),
        4,
        (0,),
        "measure_choice",
        panel_argument=0,
    ),
    "initalias": CallContract(
        ((3, "header"),),
        5,
        surface="measure_choice",
        panel_argument=1,
    ),
    "addsheettotabgroup": CallContract(
        ((2, "header"),),
        3,
        surface="gui_embedded",
        panel_argument=1,
    ),
    "settabgroupheader": CallContract(
        ((1, "header"),),
        2,
        surface="gui_embedded",
        panel_argument=0,
    ),
    "createimportantpersonsection": CallContract(
        ((1, "header"),),
        2,
        surface="important_persons",
    ),
    "blackboardaddpamphlet": CallContract(
        ((2, "body"),),
        3,
        surface="pamphlet",
    ),
}

_FEEDBACK_MESSAGE_CONTRACT = CallContract(
    ((1, "header"), (2, "body")),
    3,
    surface="news",
)
_FEEDBACK_MESSAGE_OFFICE_CONTRACT = CallContract(
    ((2, "header"), (3, "body")),
    4,
    surface="news",
)


def call_contract(call_name: str) -> CallContract | None:
    normalized = call_name.casefold()
    fixed = _FIXED_CALL_CONTRACTS.get(normalized)
    if fixed is not None:
        return fixed
    if normalized == "feedback_messageoffice":
        return _FEEDBACK_MESSAGE_OFFICE_CONTRACT
    if normalized.startswith("feedback_message"):
        return _FEEDBACK_MESSAGE_CONTRACT
    return None


def _semantic_call_contract(
    call: ScriptCall,
    resolved_arguments: tuple[tuple[str, ...], ...],
    catalog: frozenset[str],
) -> CallContract | None:
    roles: list[tuple[int, str]] = []
    for argument_index, candidates in enumerate(resolved_arguments):
        argument_roles: set[str] = set()
        for candidate in candidates:
            labels = _literal_labels(candidate, catalog, allow_patterns=True)
            for label, _position in labels:
                role = _window_label_role(label)
                if role:
                    argument_roles.add(role)
        if len(argument_roles) == 1:
            roles.append((argument_index, next(iter(argument_roles))))
    role_names = {role for _index, role in roles}
    if not roles or (
        not call.name.casefold().startswith("feedback_message")
        and not {"header", "body"} <= role_names
    ):
        return None
    text_indices = [index for index, role in roles if role in {"header", "body"}]
    if not text_indices:
        return None
    buttons = tuple(
        index
        for index, expression in enumerate(call.arguments)
        if "@B[" in expression
    )
    return CallContract(
        tuple(roles),
        max(text_indices) + 1,
        buttons,
        "news" if call.name.casefold().startswith("feedback_message") else "",
    )


def _window_label_role(label: str) -> str:
    normalized = label.strip().lstrip("_").casefold()
    if re.search(r"(^|_)(head|header|kopf)(_|$)", normalized):
        return "header"
    if re.search(
        r"(^|_)(body|text|question|answer|rumpf)(_|$)",
        normalized,
    ):
        return "body"
    return ""


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
    table_fields_by_base: dict[tuple[int | None, str], tuple[str, ...]]
    calls_by_alias: dict[str, tuple[int, ...]]
    functions_by_alias: dict[str, tuple[int, ...]]
    indirect_calls_by_target: dict[str, tuple[int, ...]]
    returns_by_function: dict[int, tuple[tuple[int, int, int], ...]]
    branch_paths: dict[int, tuple[tuple[int, int], ...]]
    alias_type_events: dict[
        tuple[int | None, str],
        tuple[tuple[int, int, str], ...],
    ]
    value_type_events: dict[
        tuple[int | None, str],
        tuple[tuple[int, int, str], ...],
    ]
    lexical_value_constraints: dict[int, dict[str, tuple[str, ...]]]
    item_names_by_id: dict[int, str]
    database_value_domains: dict[tuple[str, str], tuple[str, ...]]


LABEL_RE = re.compile(
    r"@L_[A-Za-z0-9_]+_\+(?![A-Za-z0-9])|"
    r"@L_[A-Za-z0-9_]+_\+[A-Za-z0-9]+|"
    r"@L_[A-Za-z0-9_]+\+[A-Za-z0-9]+|"
    r"@L_[A-Za-z0-9_]+"
)
RAW_LABEL_RE = re.compile(r"^_[A-Za-z0-9_]+(?:(?:_\+|\+)[A-Za-z0-9]+)?$")
COMPACT_LABEL_RE = re.compile(
    r"^@L(?P<label>[A-Za-z_][A-Za-z0-9_]*(?:(?:_\+|\+)[A-Za-z0-9]+)?)$"
)
DYNASTY_CREST_LITERAL_RE = re.compile(r"^@L\$S\[20(?:\d+|\*)\]$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BLOCK_OPENERS = {"function", "if", "for", "while", "repeat"}
_VARIADIC_RETURN_FUNCTIONS = {
    "generateprivilegelistlabels",
    "unpacktable",
}
_MAX_VARIADIC_ARGUMENTS = 24
_CALL_EXPRESSION_RE = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_.:]*)\s*\((?P<arguments>.*)\)$",
    re.DOTALL,
)
SUMMARY_PARAMETER_PREFIX = "\x1f"
_SEMANTIC_KIND_PREFIX = "\x1e"
_DATABASE_VALUE_MARK = "\x1d"
SEMANTIC_EXPRESSION = "expression"
SEMANTIC_BUILDING = "building"
SEMANTIC_CHARACTER = "character"
SEMANTIC_DYNASTY_CREST = "dynasty_crest"
SEMANTIC_DYNASTY = "dynasty"
SEMANTIC_LABEL = "label"
SEMANTIC_LABEL_DOMAIN = "label_domain"
SEMANTIC_DATABASE_VALUE = "database_value"
SEMANTIC_NUMBER = "number"
SEMANTIC_SETTLEMENT = "settlement"
SEMANTIC_STRUCTURE = "structure"
SEMANTIC_TEXT = "text"
SEMANTIC_VEHICLE = "vehicle"

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
_SEMANTIC_MARKER_KINDS = frozenset(
    (
        *_NATIVE_OBJECT_RETURN_KINDS.values(),
        SEMANTIC_DYNASTY_CREST,
        SEMANTIC_NUMBER,
        SEMANTIC_VEHICLE,
    )
)

_NATIVE_ALIAS_OUTPUT_KINDS = {
    "buildinggetowner": ((1, SEMANTIC_CHARACTER),),
    "buildinggetsim": ((2, SEMANTIC_CHARACTER),),
    "buildinggetcity": ((1, SEMANTIC_SETTLEMENT),),
    "citygetrandombuilding": ((6, SEMANTIC_BUILDING),),
    "dynastygetmember": ((2, SEMANTIC_CHARACTER),),
    "getdynasty": ((1, SEMANTIC_DYNASTY),),
    "gethomebuilding": ((1, SEMANTIC_BUILDING),),
    "getinsidebuilding": ((1, SEMANTIC_BUILDING),),
    "getsettlement": ((1, SEMANTIC_SETTLEMENT),),
}

_NATIVE_ALIAS_INPUT_KINDS = {
    "buildinggetcity": ((0, SEMANTIC_BUILDING),),
    "buildinggetowner": ((0, SEMANTIC_BUILDING),),
    "buildinggetsim": ((0, SEMANTIC_BUILDING),),
    "citygetbuildingcount": ((0, SEMANTIC_SETTLEMENT),),
    "citygetbuildings": ((0, SEMANTIC_SETTLEMENT),),
    "citygetpenalty": ((0, SEMANTIC_SETTLEMENT),),
    "citygetrandombuilding": ((0, SEMANTIC_SETTLEMENT),),
    "cityiskontor": ((0, SEMANTIC_SETTLEMENT),),
    "cartgettype": ((0, SEMANTIC_VEHICLE),),
    "dynastygetmember": ((0, SEMANTIC_DYNASTY),),
    "getinsidebuilding": ((0, SEMANTIC_CHARACTER),),
    "kill": ((0, SEMANTIC_CHARACTER),),
    "playanimation": ((0, SEMANTIC_CHARACTER),),
    "playanimationnowait": ((0, SEMANTIC_CHARACTER),),
    "simgetclass": ((0, SEMANTIC_CHARACTER),),
    "simgetgender": ((0, SEMANTIC_CHARACTER),),
    "simgetlevel": ((0, SEMANTIC_CHARACTER),),
}

_SCENARIO_LOOKUP_VALUE_KINDS = {
    "cl_sim": SEMANTIC_CHARACTER,
}

_FIXED_ALIAS_KINDS = {
    "building": SEMANTIC_BUILDING,
    "city": SEMANTIC_SETTLEMENT,
    "dynasty": SEMANTIC_DYNASTY,
    "settlement": SEMANTIC_SETTLEMENT,
    "sim": SEMANTIC_CHARACTER,
    "workbuilding": SEMANTIC_BUILDING,
}

_ENGINE_TYPE_NAME_KINDS = {
    "building": SEMANTIC_BUILDING,
    "city": SEMANTIC_SETTLEMENT,
    "dynasty": SEMANTIC_DYNASTY,
    "settlement": SEMANTIC_SETTLEMENT,
    "sim": SEMANTIC_CHARACTER,
}


def analyze_script(
    text: str,
    path: Path,
    *,
    label_catalog: frozenset[str] = frozenset(),
    item_names_by_id: tuple[tuple[int, str], ...] = (),
    database_value_domains: tuple[tuple[str, str, tuple[str, ...]], ...] = (),
) -> tuple[SemanticLabelUse, ...]:
    return analyze_script_facts(
        text,
        path,
        label_catalog=label_catalog,
        item_names_by_id=item_names_by_id,
        database_value_domains=database_value_domains,
    ).uses


def script_calls(text: str, path: Path) -> tuple[ScriptCall, ...]:
    """Parse Lua calls without assigning localization roles."""
    tokens = tokenize_lua(text)
    return _calls(text, tokens, _functions(tokens, path))


def analyze_script_facts(
    text: str,
    path: Path,
    *,
    label_catalog: frozenset[str] = frozenset(),
    item_names_by_id: tuple[tuple[int, str], ...] = (),
    database_value_domains: tuple[tuple[str, str, tuple[str, ...]], ...] = (),
) -> ScriptSemanticFacts:
    tokens = tokenize_lua(text)
    functions = _functions(tokens, path)
    calls = _calls(text, tokens, functions)
    assignments = _assignments(tokens, functions)
    assignments_by_name: dict[tuple[int | None, str], list[Assignment]] = {}
    table_fields_by_base: dict[tuple[int | None, str], list[str]] = {}
    for assignment in assignments:
        assignments_by_name.setdefault(
            (assignment.function_index, assignment.name.casefold()),
            [],
        ).append(assignment)
        table_match = re.fullmatch(r"(.+)\[[^\]]+\]", assignment.name)
        if table_match is not None:
            key = (assignment.function_index, table_match.group(1).casefold())
            fields = table_fields_by_base.setdefault(key, [])
            if assignment.name not in fields:
                fields.append(assignment.name)
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
                bisect.bisect_left(token_starts, assignment.position)
                for assignment in assignments
            ),
            *(
                bisect.bisect_left(token_starts, call.start)
                for call in calls
            ),
        )
    )
    branch_paths = _conditional_branch_paths(tokens, branch_path_tokens)
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
        {key: tuple(values) for key, values in table_fields_by_base.items()},
        {name: tuple(indices) for name, indices in calls_by_alias.items()},
        {name: tuple(indices) for name, indices in functions_by_alias.items()},
        _indirect_function_calls(
            tokens,
            assignments_by_name,
            calls,
            calls_by_alias,
            functions_by_alias,
            token_starts,
            branch_paths,
        ),
        _return_expressions_by_function(tokens, functions),
        branch_paths,
        _native_alias_type_events(tokens, calls, token_starts),
        _native_value_type_events(tokens, calls, token_starts),
        _lexical_value_constraints(tokens, branch_path_tokens, branch_paths),
        dict(item_names_by_id),
        {
            (table.casefold(), field.casefold()): values
            for table, field, values in database_value_domains
        },
    )
    has_localization_work = any(
        _literal_labels(token.value, label_catalog, allow_patterns=True)
        for token in tokens
        if token.kind == "string"
    ) or any(
        call_contract(call.name) is not None
        or any("@B[" in argument for argument in call.arguments)
        for call in calls
    )
    if not has_localization_work:
        # Many animation and style helpers contain hundreds of ordinary calls
        # but no localization producer or consumer. Resolving every argument in
        # those files caused multi-second cold-index work while yielding no uses.
        return ScriptSemanticFacts(
            (),
            (),
            (),
            _function_value_summaries(analysis),
        )
    uses: list[SemanticLabelUse] = []
    external_flows: list[ExternalCallFlow] = []
    claimed_ranges: list[tuple[int, int]] = []
    for call in calls:
        resolved_arguments = _resolved_call_arguments(
            analysis,
            call,
            label_catalog,
        )
        contract = call_contract(call.name) or _semantic_call_contract(
            call,
            resolved_arguments,
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
            runtime_arguments = _runtime_argument_expressions(call, runtime_start)
            runtime_argument_values, runtime_argument_kinds = (
                _runtime_argument_semantics(
                    analysis,
                    call,
                    runtime_start,
                    required_branches=_branch_path_at_position(
                        analysis,
                        call.start,
                    ),
                )
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


def _lexical_value_constraints(
    tokens: tuple[Token, ...],
    tracked_indices: frozenset[int],
    branch_paths: dict[int, tuple[tuple[int, int], ...]],
) -> dict[int, dict[str, tuple[str, ...]]]:
    """Collect only finite values proven by enclosing code ranges.

    Equality sets in a true ``if``/``elseif`` arm and small literal numeric
    ``for`` ranges are exact code evidence.  Unknown or large ranges are left
    unresolved rather than partially enumerated.
    """
    branch_constraints = _conditional_branch_constraints(tokens)
    loop_constraints = _loop_constraints_at_tokens(tokens, tracked_indices)
    values: dict[int, dict[str, tuple[str, ...]]] = {}
    for token_index in tracked_indices:
        combined: dict[str, tuple[str, ...]] = {}
        for branch_key in branch_paths.get(token_index, ()):
            activation = branch_constraints.get(branch_key)
            if activation is None or token_index <= activation[0]:
                continue
            combined = _merge_conjunctive_constraints(combined, activation[1])
        combined = _merge_conjunctive_constraints(
            combined,
            loop_constraints.get(token_index, {}),
        )
        if combined:
            values[token_index] = combined
    return values


def _conditional_branch_constraints(
    tokens: tuple[Token, ...],
) -> dict[tuple[int, int], tuple[int, dict[str, tuple[str, ...]]]]:
    constraints: dict[
        tuple[int, int],
        tuple[int, dict[str, tuple[str, ...]]],
    ] = {}
    stack: list[list[object]] = []
    for index, token in enumerate(tokens):
        value = token.value.casefold() if token.kind == "identifier" else ""
        if value == "if":
            then_index = _condition_keyword(tokens, index + 1, "then")
            if then_index is not None:
                constraints[(index, 0)] = (
                    then_index,
                    _condition_value_constraints(tokens, index + 1, then_index),
                )
            stack.append(["if", index, 0, False])
        elif value == "elseif":
            if stack and stack[-1][0] == "if":
                stack[-1][2] = int(stack[-1][2]) + 1
                then_index = _condition_keyword(tokens, index + 1, "then")
                if then_index is not None:
                    constraints[(int(stack[-1][1]), int(stack[-1][2]))] = (
                        then_index,
                        _condition_value_constraints(tokens, index + 1, then_index),
                    )
        elif value == "else":
            if stack and stack[-1][0] == "if":
                stack[-1][2] = int(stack[-1][2]) + 1
        elif value == "end":
            if stack:
                stack.pop()
        elif value == "until":
            if stack and stack[-1][0] == "repeat":
                stack.pop()
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
    return constraints


def _loop_constraints_at_tokens(
    tokens: tuple[Token, ...],
    tracked_indices: frozenset[int],
) -> dict[int, dict[str, tuple[str, ...]]]:
    stack: list[list[object]] = []
    constraints: dict[int, dict[str, tuple[str, ...]]] = {}
    for index, token in enumerate(tokens):
        value = token.value.casefold() if token.kind == "identifier" else ""
        if value in {"elseif", "else"}:
            if stack and stack[-1][0] == "if" and value == "elseif":
                stack[-1][2] = int(stack[-1][2]) + 1
        elif value == "end":
            if stack:
                stack.pop()
        elif value == "until":
            if stack and stack[-1][0] == "repeat":
                stack.pop()
        elif value == "if":
            stack.append(["if", index, 0, False, {}])
        elif value == "for":
            stack.append(["for", index, 0, True, _numeric_for_constraint(tokens, index)])
        elif value == "while":
            stack.append(["while", index, 0, True, {}])
        elif value == "function":
            stack.append(["function", index, 0, False, {}])
        elif value == "repeat":
            stack.append(["repeat", index, 0, False, {}])
        elif value == "do":
            if stack and stack[-1][3] is True:
                stack[-1][3] = False
            else:
                stack.append(["do", index, 0, False, {}])
        if index not in tracked_indices:
            continue
        combined: dict[str, tuple[str, ...]] = {}
        for frame in stack:
            if frame[0] == "for" and frame[3] is False:
                combined = _merge_conjunctive_constraints(
                    combined,
                    frame[4] if isinstance(frame[4], dict) else {},
                )
        if combined:
            constraints[index] = combined
    return constraints


def _numeric_for_constraint(
    tokens: tuple[Token, ...],
    for_index: int,
) -> dict[str, tuple[str, ...]]:
    do_index = _condition_keyword(tokens, for_index + 1, "do")
    if (
        do_index is None
        or for_index + 3 >= do_index
        or tokens[for_index + 1].kind != "identifier"
        or tokens[for_index + 2].value != "="
    ):
        return {}
    parts = _split_token_range(tokens, for_index + 3, do_index, ",")
    if len(parts) not in {2, 3}:
        return {}
    bounds = tuple(_integer_token_value(tokens, start, end) for start, end in parts)
    if any(value is None for value in bounds):
        return {}
    first = int(bounds[0])
    last = int(bounds[1])
    step = int(bounds[2]) if len(bounds) == 3 else 1
    if step == 0 or (step > 0 and first > last) or (step < 0 and first < last):
        return {}
    stop = last + (1 if step > 0 else -1)
    numbers = tuple(range(first, stop, step))
    if not numbers or len(numbers) > 64:
        return {}
    return {tokens[for_index + 1].value.casefold(): tuple(str(value) for value in numbers)}


def _condition_keyword(
    tokens: tuple[Token, ...],
    start: int,
    keyword: str,
) -> int | None:
    depth = 0
    for index in range(start, len(tokens)):
        token = tokens[index]
        if token.value in {"(", "[", "{"}:
            depth += 1
        elif token.value in {")",
            "]",
            "}",
        }:
            depth = max(0, depth - 1)
        elif (
            depth == 0
            and token.kind == "identifier"
            and token.value.casefold() == keyword
        ):
            return index
    return None


def _condition_value_constraints(
    tokens: tuple[Token, ...],
    start: int,
    end: int,
) -> dict[str, tuple[str, ...]]:
    while start < end and tokens[start].value == "(":
        close = _matching_token(tokens, start, "(", ")")
        if close != end - 1:
            break
        start += 1
        end -= 1
    if start >= end or (
        tokens[start].kind == "identifier"
        and tokens[start].value.casefold() == "not"
    ):
        return {}
    disjunctions = _split_token_range(tokens, start, end, "or")
    if len(disjunctions) > 1:
        branches = [
            _condition_value_constraints(tokens, part_start, part_end)
            for part_start, part_end in disjunctions
        ]
        common = set(branches[0]) if branches else set()
        for branch in branches[1:]:
            common.intersection_update(branch)
        return {
            name: tuple(
                dict.fromkeys(
                    value
                    for branch in branches
                    for value in branch[name]
                )
            )[:64]
            for name in common
        }
    conjunctions = _split_token_range(tokens, start, end, "and")
    if len(conjunctions) > 1:
        combined: dict[str, tuple[str, ...]] = {}
        for part_start, part_end in conjunctions:
            combined = _merge_conjunctive_constraints(
                combined,
                _condition_value_constraints(tokens, part_start, part_end),
            )
        combined = _merge_conjunctive_constraints(
            combined,
            _bounded_integer_constraints(tokens, conjunctions),
        )
        return combined
    equality = _split_token_range(tokens, start, end, "==")
    if len(equality) != 2:
        return {}
    left_name = _lvalue_name(tokens, equality[0][0], equality[0][1])
    right_name = _lvalue_name(tokens, equality[1][0], equality[1][1])
    left_value = _literal_token_value(tokens, equality[0][0], equality[0][1])
    right_value = _literal_token_value(tokens, equality[1][0], equality[1][1])
    if left_name and right_value is not None:
        return {left_name.casefold(): (right_value,)}
    if right_name and left_value is not None:
        return {right_name.casefold(): (left_value,)}
    return {}


def _bounded_integer_constraints(
    tokens: tuple[Token, ...],
    parts: tuple[tuple[int, int], ...],
) -> dict[str, tuple[str, ...]]:
    bounds: dict[str, list[int | None]] = {}
    for start, end in parts:
        comparison = _integer_comparison_bound(tokens, start, end)
        if comparison is None:
            continue
        name, lower, upper = comparison
        current = bounds.setdefault(name, [None, None])
        if lower is not None:
            current[0] = lower if current[0] is None else max(int(current[0]), lower)
        if upper is not None:
            current[1] = upper if current[1] is None else min(int(current[1]), upper)
    values: dict[str, tuple[str, ...]] = {}
    for name, (lower, upper) in bounds.items():
        if lower is None or upper is None or lower > upper or upper - lower >= 64:
            continue
        values[name] = tuple(str(value) for value in range(lower, upper + 1))
    return values


def _integer_comparison_bound(
    tokens: tuple[Token, ...],
    start: int,
    end: int,
) -> tuple[str, int | None, int | None] | None:
    while start < end and tokens[start].value == "(":
        close = _matching_token(tokens, start, "(", ")")
        if close != end - 1:
            break
        start += 1
        end -= 1
    for operator in (">=", ">", "<=", "<"):
        operands = _split_token_range(tokens, start, end, operator)
        if len(operands) != 2:
            continue
        left_name = _lvalue_name(tokens, operands[0][0], operands[0][1])
        right_name = _lvalue_name(tokens, operands[1][0], operands[1][1])
        left_value = _integer_token_value(tokens, operands[0][0], operands[0][1])
        right_value = _integer_token_value(tokens, operands[1][0], operands[1][1])
        if left_name and right_value is not None:
            if operator == ">=":
                return left_name.casefold(), right_value, None
            if operator == ">":
                return left_name.casefold(), right_value + 1, None
            if operator == "<=":
                return left_name.casefold(), None, right_value
            return left_name.casefold(), None, right_value - 1
        if right_name and left_value is not None:
            if operator == ">=":
                return right_name.casefold(), None, left_value
            if operator == ">":
                return right_name.casefold(), None, left_value - 1
            if operator == "<=":
                return right_name.casefold(), left_value, None
            return right_name.casefold(), left_value + 1, None
    return None


def _merge_conjunctive_constraints(
    left: dict[str, tuple[str, ...]],
    right: Mapping[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    if not right:
        return dict(left)
    merged = dict(left)
    for name, values in right.items():
        if name not in merged:
            merged[name] = values
            continue
        allowed = set(values)
        intersection = tuple(value for value in merged[name] if value in allowed)
        if intersection:
            merged[name] = intersection
        else:
            merged.pop(name, None)
    return merged


def _literal_token_value(
    tokens: tuple[Token, ...],
    start: int,
    end: int,
) -> str | None:
    while start < end and tokens[start].value == "(":
        close = _matching_token(tokens, start, "(", ")")
        if close != end - 1:
            break
        start += 1
        end -= 1
    if end - start == 1 and tokens[start].kind in {"string", "number"}:
        return tokens[start].value
    if (
        end - start == 1
        and tokens[start].kind == "identifier"
        and tokens[start].value.casefold() in {"true", "false"}
    ):
        return tokens[start].value.casefold()
    if (
        end - start == 2
        and tokens[start].value in {"+", "-"}
        and tokens[start + 1].kind == "number"
    ):
        return tokens[start].value + tokens[start + 1].value
    return None


def _integer_token_value(
    tokens: tuple[Token, ...],
    start: int,
    end: int,
) -> int | None:
    value = _literal_token_value(tokens, start, end)
    return int(value) if value is not None and re.fullmatch(r"[-+]?\d+", value) else None


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
    if dynamic_expression:
        for value in _syntactic_dynamic_values(
            analysis.tokens,
            token_indices[0],
            token_indices[-1] + 1,
        ):
            for label, relative in _literal_labels(
                value,
                catalog,
                allow_patterns=True,
            ):
                candidates.append(
                    (
                        label,
                        start + relative,
                        "dynamic",
                        82,
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


def _syntactic_dynamic_values(
    tokens: tuple[Token, ...],
    start: int,
    end: int,
) -> tuple[str, ...]:
    parts = _split_token_range(tokens, start, end, "..")
    if len(parts) <= 1:
        return ()
    values: list[str] = []
    for part_start, part_end in parts:
        while (
            part_end - part_start >= 2
            and tokens[part_start].value == "("
            and _matching_token(tokens, part_start, "(", ")") == part_end - 1
        ):
            part_start += 1
            part_end -= 1
        if part_end - part_start == 1 and tokens[part_start].kind in {
            "number",
            "string",
        }:
            values.append(tokens[part_start].value)
        else:
            values.append("*")
    return ("".join(values),)


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
    if _label_family_base(label):
        return "exact"
    if not catalog:
        return "family"
    variants = {label, label.lstrip("_")}
    if variants & catalog:
        return "exact"
    if variants & _catalog_family_separators(catalog).keys():
        return "family"
    return "exact"


def _semantic_label_identity(
    label: str,
    kind: str,
    catalog: frozenset[str],
) -> str:
    role_alias = _catalog_backed_role_alias(label, catalog)
    if role_alias:
        label = role_alias
    if kind != "family" or not catalog or _label_family_base(label):
        return label
    variants = {label, label.lstrip("_")}
    family_separators = _catalog_family_separators(catalog)
    if variants & family_separators.keys():
        separator = next(
            (
                family_separators[variant]
                for variant in (label, label.lstrip("_"))
                if variant in family_separators
            ),
            "_+",
        )
        return label + separator + "*"
    return label


def _catalog_backed_role_alias(label: str, catalog: frozenset[str]) -> str:
    """Recover a unique catalog label when a script adds a stray BODY segment."""
    if not catalog or {label, label.lstrip("_")} & catalog:
        return ""
    match = re.match(
        r"^(?P<base>.+)_body(?P<suffix>(?:_\+|\+)[A-Za-z0-9*]+)$",
        label,
    )
    if match is None:
        return ""
    candidate = match.group("base") + match.group("suffix")
    return candidate if {candidate, candidate.lstrip("_")} & catalog else ""


@lru_cache(maxsize=8)
def _catalog_family_separators(catalog: frozenset[str]) -> Mapping[str, str]:
    """Cache each catalog family and its actual suffix separator once."""
    separators: dict[str, str] = {}
    for label in sorted(catalog):
        match = re.match(
            r"^(?P<base>.*?)(?P<separator>_\+|\+)[A-Za-z0-9*]+$",
            label,
        )
        if match is not None:
            separators.setdefault(match.group("base"), match.group("separator"))
    return separators


def _label_family_base(label: str) -> str:
    match = re.match(r"^(.*?)(?:_\+|\+)[A-Za-z0-9*]+$", label)
    return match.group(1) if match is not None else ""


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
    conditional_parts = _split_token_range(analysis.tokens, start, end, "or")
    if len(conditional_parts) > 1:
        values: list[str] = []
        for part_start, part_end in conditional_parts:
            conjunctions = _split_token_range(
                analysis.tokens,
                part_start,
                part_end,
                "and",
            )
            value_start, value_end = conjunctions[-1]
            values.extend(
                _evaluate_tokens(
                    analysis,
                    value_start,
                    value_end,
                    position,
                    function_index,
                    resolving,
                    parameter_bindings,
                    required_branches,
                )
            )
        return tuple(dict.fromkeys(values))[:64]
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
                    left + _concatenation_value(right)
                    for left in combined
                    for right in part_values
                )
            )[:64]
        return combined
    numeric_values = _numeric_binary_values(
        analysis,
        start,
        end,
        position,
        function_index,
        resolving,
        parameter_bindings,
        required_branches,
    )
    if numeric_values is not None:
        return numeric_values
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
    if (
        end - start == 2
        and analysis.tokens[start].value in {"+", "-"}
        and analysis.tokens[start + 1].kind == "number"
    ):
        return (analysis.tokens[start].value + analysis.tokens[start + 1].value,)
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


def _numeric_binary_values(
    analysis: _Analysis,
    start: int,
    end: int,
    position: int,
    function_index: int | None,
    resolving: set[tuple[str, int | None]],
    parameter_bindings: dict[tuple[int, str], tuple[str, ...]] | None,
    required_branches: tuple[tuple[int, int], ...],
) -> tuple[str, ...] | None:
    depth = 0
    operator_index: int | None = None
    for index in range(start, end):
        value = analysis.tokens[index].value
        if value in {"(", "[", "{"}:
            depth += 1
            continue
        if value in {")",
            "]",
            "}",
        }:
            depth = max(0, depth - 1)
            continue
        if depth or value not in {"+", "-"} or index == start:
            continue
        previous = analysis.tokens[index - 1].value
        if previous in {"(", "[", "{", ",", "+", "-", "*", "/", "%", "==", "~="}:
            continue
        operator_index = index
    if operator_index is None:
        return None
    left_values = _evaluate_tokens(
        analysis,
        start,
        operator_index,
        position,
        function_index,
        resolving,
        parameter_bindings,
        required_branches,
    )
    right_values = _evaluate_tokens(
        analysis,
        operator_index + 1,
        end,
        position,
        function_index,
        resolving,
        parameter_bindings,
        required_branches,
    )
    if (
        not left_values
        or not right_values
        or any(not re.fullmatch(r"[-+]?\d+", value) for value in (*left_values, *right_values))
    ):
        return None
    operator = analysis.tokens[operator_index].value
    return tuple(
        dict.fromkeys(
            str(int(left) + int(right) if operator == "+" else int(left) - int(right))
            for left in left_values
            for right in right_values
        )
    )[:64]


def _concatenation_value(value: str) -> str:
    """Keep unresolved calls useful as wildcard evidence inside dynamic strings."""
    return "*" if semantic_literal(value).kind == SEMANTIC_EXPRESSION else value


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
    terminal_name = re.split(r"[.:]", call.name)[-1].casefold()
    if terminal_name == "getid":
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
        return ()
    if terminal_name == "getname":
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
        expression = analysis.text[call.start : call.end].strip()
        return (expression,) if expression else ()
    target_indices = analysis.functions_by_alias.get(call.name.casefold(), ())
    if not target_indices and native_semantic_function_name(call.name) is None:
        expression = analysis.text[call.start : call.end].strip()
        return (expression,) if expression else ()
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
        resolved = resolve_native_semantic_function(
            call.name,
            semantic_arguments,
            item_names_by_id=analysis.item_names_by_id,
            database_value_domains=analysis.database_value_domains,
        )
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
    if token.kind not in {"identifier", "string"}:
        return ()
    alias = token.value.casefold()
    kinds: list[str] = []
    fixed = _FIXED_ALIAS_KINDS.get(alias) if token.kind == "string" else None
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
    if not kinds and token.kind == "string":
        whole_function_kinds = {
            kind
            for _position, _producer_token, kind in analysis.alias_type_events.get(
                (call.function_index, alias),
                (),
            )
        }
        if len(whole_function_kinds) == 1:
            kinds.extend(whole_function_kinds)
    if token.kind == "string" and call.function_index is not None:
        kinds.extend(
            kind
            for _position, _producer_token, kind in analysis.alias_type_events.get(
                (None, alias),
                (),
            )
        )
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
        typed_arguments = [
            *_NATIVE_ALIAS_OUTPUT_KINDS.get(name, ()),
            *_NATIVE_ALIAS_INPUT_KINDS.get(name, ()),
        ]
        if name == "istype" and len(call.argument_spans) > 1:
            type_start, type_end = call.argument_spans[1]
            type_first = bisect.bisect_left(token_starts, type_start)
            type_last = bisect.bisect_left(token_starts, type_end)
            type_tokens = tuple(
                index
                for index in range(type_first, type_last)
                if tokens[index].end <= type_end
            )
            if len(type_tokens) == 1 and tokens[type_tokens[0]].kind == "string":
                proven_kind = _ENGINE_TYPE_NAME_KINDS.get(
                    tokens[type_tokens[0]].value.casefold()
                )
                if proven_kind is not None:
                    typed_arguments.append((0, proven_kind))
        for argument_index, kind in typed_arguments:
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
            if (
                len(indices) != 1
                or tokens[indices[0]].kind not in {"identifier", "string"}
            ):
                continue
            token = tokens[indices[0]]
            event = (
                call.start,
                bisect.bisect_left(token_starts, call.start),
                kind,
            )
            keys = [(call.function_index, token.value.casefold())]
            if (
                token.kind == "string"
                and call.function_index is not None
                and name != "istype"
            ):
                keys.append((None, token.value.casefold()))
            for key in keys:
                grouped.setdefault(key, []).append(event)
    return {key: tuple(events) for key, events in grouped.items()}


def _native_value_type_events(
    tokens: tuple[Token, ...],
    calls: tuple[ScriptCall, ...],
    token_starts: tuple[int, ...],
) -> dict[tuple[int | None, str], tuple[tuple[int, int, str], ...]]:
    """Record native calls that prove the domain of a scalar identifier."""
    grouped: dict[
        tuple[int | None, str],
        list[tuple[int, int, str]],
    ] = {}
    for call in calls:
        name = re.split(r"[.:]", call.name)[-1].casefold()
        if name != "scenariogetobjectbyname" or len(call.argument_spans) < 2:
            continue
        class_start, class_end = call.argument_spans[0]
        class_first = bisect.bisect_left(token_starts, class_start)
        class_last = bisect.bisect_left(token_starts, class_end)
        class_indices = tuple(
            index
            for index in range(class_first, class_last)
            if tokens[index].end <= class_end
        )
        value_start, value_end = call.argument_spans[1]
        value_first = bisect.bisect_left(token_starts, value_start)
        value_last = bisect.bisect_left(token_starts, value_end)
        value_indices = tuple(
            index
            for index in range(value_first, value_last)
            if tokens[index].end <= value_end
        )
        if (
            len(class_indices) != 1
            or tokens[class_indices[0]].kind != "string"
            or len(value_indices) != 1
            or tokens[value_indices[0]].kind != "identifier"
        ):
            continue
        kind = _SCENARIO_LOOKUP_VALUE_KINDS.get(
            tokens[class_indices[0]].value.casefold()
        )
        if kind is None:
            continue
        value_token = tokens[value_indices[0]]
        grouped.setdefault(
            (call.function_index, value_token.value.casefold()),
            [],
        ).append(
            (
                call.start,
                bisect.bisect_left(token_starts, call.start),
                kind,
            )
        )
    return {key: tuple(events) for key, events in grouped.items()}


def _indirect_function_calls(
    tokens: tuple[Token, ...],
    assignments_by_name: dict[tuple[int | None, str], list[Assignment]],
    calls: tuple[ScriptCall, ...],
    calls_by_alias: dict[str, list[int]],
    functions_by_alias: dict[str, list[int]],
    token_starts: tuple[int, ...],
    branch_paths: dict[int, tuple[tuple[int, int], ...]],
) -> dict[str, tuple[int, ...]]:
    """Index calls through locals assigned a known script function."""
    grouped: dict[str, list[int]] = {}
    for variable, call_indices in calls_by_alias.items():
        if variable in functions_by_alias or not _IDENTIFIER_RE.fullmatch(variable):
            continue
        for call_index in call_indices:
            call = calls[call_index]
            call_token = bisect.bisect_left(token_starts, call.start)
            call_path = branch_paths.get(call_token, ())
            reaching_by_path: dict[tuple[tuple[int, int], ...], Assignment] = {}
            for assignment in assignments_by_name.get(
                (call.function_index, variable),
                (),
            ):
                if assignment.position >= call.start:
                    continue
                assignment_path = branch_paths.get(assignment.token_start, ())
                if not _branches_compatible(assignment_path, call_path):
                    continue
                reaching_by_path[assignment_path] = assignment
            for assignment in reaching_by_path.values():
                if assignment.token_end - assignment.token_start != 1:
                    continue
                target_token = tokens[assignment.token_start]
                if target_token.kind != "identifier":
                    continue
                target = target_token.value.casefold()
                if target not in functions_by_alias:
                    continue
                targets = grouped.setdefault(target, [])
                if call_index not in targets:
                    targets.append(call_index)
    return {target: tuple(indices) for target, indices in grouped.items()}


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
    database_backed = _DATABASE_VALUE_MARK in value
    if database_backed:
        value = value.replace(_DATABASE_VALUE_MARK, "")
    marker_kind = _semantic_marker_kind(value)
    if marker_kind:
        return SemanticValue(marker_kind, "")
    stripped = value.strip()
    if value in {"", "$N"}:
        kind = SEMANTIC_STRUCTURE
    elif DYNASTY_CREST_LITERAL_RE.fullmatch(stripped):
        kind = SEMANTIC_DYNASTY_CREST
    elif stripped.startswith(("@L_", "_")) or COMPACT_LABEL_RE.fullmatch(stripped):
        kind = SEMANTIC_LABEL
    elif re.fullmatch(r"[-+]?\d+(?:\.\d+)?|true|false", stripped, re.IGNORECASE):
        kind = SEMANTIC_NUMBER
    elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:]*\s*\(.*\)", stripped, re.DOTALL):
        kind = SEMANTIC_EXPRESSION
    else:
        kind = SEMANTIC_TEXT
    if database_backed and kind == SEMANTIC_LABEL:
        kind = SEMANTIC_LABEL_DOMAIN
    return SemanticValue(kind, value)


def _semantic_candidate(value: SemanticValue) -> str:
    if value.kind in _SEMANTIC_MARKER_KINDS and not value.text:
        return _SEMANTIC_KIND_PREFIX + value.kind
    if value.kind == SEMANTIC_DATABASE_VALUE and value.text:
        return f"{_DATABASE_VALUE_MARK}{value.text}{_DATABASE_VALUE_MARK}"
    return value.text


def _semantic_marker_kind(value: str) -> str:
    if not value.startswith(_SEMANTIC_KIND_PREFIX):
        return ""
    kind = value[len(_SEMANTIC_KIND_PREFIX) :]
    return kind if kind in _SEMANTIC_MARKER_KINDS else ""


def native_semantic_function_name(alias: str) -> str | None:
    name = re.split(r"[.:]", alias)[-1].casefold().split("_")[-1]
    if name in _NATIVE_OBJECT_RETURN_KINDS:
        return name
    if name in {
        "citylevel2label",
        "generateprivilegelistlabels",
        "getdatabasevalue",
        "getflaglabel",
        "getnobilitytitlelabel",
        "itemgetlabel",
        "officegettextlabel",
        "professiongetlabel",
    }:
        return name
    if name in {"ceil", "floor", "rand", "sub"}:
        return name
    return None


def resolve_native_semantic_function(
    alias: str,
    argument_values: tuple[tuple[SemanticValue, ...], ...],
    *,
    item_names_by_id: Mapping[int, str] | None = None,
    database_value_domains: Mapping[tuple[str, str], tuple[str, ...]] | None = None,
) -> tuple[tuple[SemanticValue, ...], ...] | None:
    """Apply engine function contracts shared by local and cross-file evaluation."""
    name = native_semantic_function_name(alias)
    object_kind = _NATIVE_OBJECT_RETURN_KINDS.get(name or "")
    if object_kind is not None:
        return ((SemanticValue(object_kind, ""),),)
    if name == "getflaglabel":
        # dyn_GetFlagLabel returns the crest symbol belonging to the supplied
        # dynasty/sim alias. Its exact glyph is runtime state, but its display
        # domain is fixed.
        return ((SemanticValue(SEMANTIC_DYNASTY_CREST, ""),),)
    if name == "getdatabasevalue":
        values = _database_value_candidates(
            argument_values,
            database_value_domains or {},
        )
        return (
            tuple(SemanticValue(SEMANTIC_DATABASE_VALUE, value) for value in values),
        ) if values else None
    if name == "itemgetlabel":
        item_values = argument_values[0] if argument_values else ()
        singular_values = argument_values[1] if len(argument_values) > 1 else ()
        item_names: list[str] = []
        unresolved = False
        for value in item_values:
            if (
                re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value.text)
                and value.text.casefold() not in {"true", "false"}
            ):
                item_names.append(value.text)
            elif re.fullmatch(r"[-+]?\d+", value.text):
                item_name = (item_names_by_id or {}).get(int(value.text))
                if item_name:
                    item_names.append(item_name)
                else:
                    unresolved = True
            else:
                unresolved = True
        item_names = list(dict.fromkeys(item_names))
        if unresolved or not item_names:
            item_names.append("*")
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
    if name == "officegettextlabel":
        # OfficeGetTextLabel returns the localized display label for the
        # supplied office object/ID. The exact office and gender may be runtime
        # values, but the engine label family is fixed.
        return ((semantic_literal("_CHARACTERS_3_OFFICES_NAME_*_+*"),),)
    if name == "professiongetlabel":
        # The engine selects a profession and gender-specific localization
        # member. Runtime values vary, but the returned label family is fixed.
        return ((semantic_literal("_CHARACTERS_2_PROFESSIONS_*_NAME_+*"),),)
    if name == "sub":
        source_values = argument_values[0] if argument_values else ()
        starts = _semantic_integer_candidates(argument_values, 1)
        ends = _semantic_integer_candidates(argument_values, 2)
        if not source_values or not starts or (len(argument_values) > 2 and not ends):
            return ((),)
        requested_ends: tuple[int | None, ...] = ends or (None,)
        values: list[SemanticValue] = []
        for source in source_values:
            if not source.text:
                continue
            for start in starts:
                for end in requested_ends:
                    value = semantic_literal(_lua_substring(source.text, start, end))
                    if value not in values:
                        values.append(value)
                    if len(values) >= 64:
                        return (tuple(values),)
        return (tuple(values),)
    if name == "rand":
        maxima = _semantic_integer_candidates(argument_values, 0)
        values: list[SemanticValue] = []
        for maximum in maxima:
            if maximum <= 0 or maximum > 64:
                continue
            values.extend(
                semantic_literal(str(value))
                for value in range(maximum)
            )
        return (tuple(dict.fromkeys(values))[:64],)
    if name in {"ceil", "floor"}:
        return ((SemanticValue(SEMANTIC_NUMBER, ""),),)
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
        for _index in range(10):
            positions.append(
                (semantic_literal("_PRIVILEGE_*_MESSAGETEXT_+0"),)
            )
            positions.append((semantic_literal("$N"),))
        positions.append(
            (semantic_literal("_PRIVILEGE_*_MESSAGETEXT_+0"),)
        )
    while len(positions) < 21:
        positions.append((semantic_literal(""),))
    return tuple(positions[:21])


def _database_value_candidates(
    argument_values: tuple[tuple[SemanticValue, ...], ...],
    domains: Mapping[tuple[str, str], tuple[str, ...]],
) -> tuple[str, ...]:
    """Resolve a finite GetDatabaseValue column without guessing its row."""
    if len(argument_values) < 3 or not domains:
        return ()
    table_patterns = tuple(
        pattern
        for value in argument_values[0]
        if (pattern := _database_lookup_pattern(value)) is not None
    )
    field_patterns = tuple(
        pattern
        for value in argument_values[2]
        if (pattern := _database_lookup_pattern(value)) is not None
    )
    if not table_patterns or not field_patterns:
        return ()
    values: list[str] = []
    for (table, field), candidates in domains.items():
        if not any(pattern.fullmatch(table) for pattern in table_patterns):
            continue
        if not any(pattern.fullmatch(field) for pattern in field_patterns):
            continue
        for candidate in candidates:
            if candidate in values:
                continue
            values.append(candidate)
            if len(values) > 64:
                return ()
    return tuple(values)


def _database_lookup_pattern(value: SemanticValue) -> re.Pattern[str] | None:
    if not value.text or value.kind == SEMANTIC_EXPRESSION:
        return None
    marker = re.escape(SUMMARY_PARAMETER_PREFIX) + r"\d+" + re.escape(SUMMARY_PARAMETER_PREFIX)
    escaped = re.escape(value.text.casefold())
    escaped = re.sub(marker, ".*", escaped)
    escaped = escaped.replace(r"\*", ".*")
    return re.compile(escaped)


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


def _lua_substring(value: str, start: int, end: int | None) -> str:
    """Apply Lua's inclusive, one-based string.sub index rules."""
    size = len(value)
    first = start if start > 0 else size + start + 1 if start < 0 else 1
    last = size if end is None else end if end > 0 else size + end + 1
    first = max(1, first)
    last = min(size, last)
    if first > last:
        return ""
    return value[first - 1 : last]


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
    proven_kind = _proven_variable_value_kind(
        analysis,
        name,
        position,
        function_index,
        required_branches,
    )
    if proven_kind:
        return (_semantic_candidate(SemanticValue(proven_kind, "")),)
    token_index = bisect.bisect_right(analysis.token_starts, position) - 1
    constrained = analysis.lexical_value_constraints.get(token_index, {}).get(
        name.casefold(),
        (),
    )
    if constrained:
        return constrained
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
    table_match = re.fullmatch(r"(.+)\[([^\]]+)\]", name)
    if table_match is not None:
        requested_field = table_match.group(2)
        for field_name in analysis.table_fields_by_base.get(
            (function_index, table_match.group(1).casefold()),
            (),
        ):
            assigned_match = re.fullmatch(r".+\[([^\]]+)\]", field_name)
            if assigned_match is None:
                continue
            assigned_field = assigned_match.group(1)
            if requested_field.isdigit() and assigned_field.isdigit():
                continue
            values.extend(
                _resolve_variable(
                    analysis,
                    field_name,
                    position,
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
    call_indices: list[int] = []
    for alias in function.aliases:
        normalized_alias = alias.casefold()
        for call_index in (
            *analysis.calls_by_alias.get(normalized_alias, ()),
            *analysis.indirect_calls_by_target.get(normalized_alias, ()),
        ):
            if call_index not in call_indices:
                call_indices.append(call_index)
    for call_index in call_indices:
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


def _proven_variable_value_kind(
    analysis: _Analysis,
    name: str,
    position: int,
    function_index: int | None,
    required_branches: tuple[tuple[int, int], ...],
) -> str:
    key = (function_index, name.casefold())
    latest_assignment = max(
        (
            assignment.position
            for assignment in analysis.assignments_by_name.get(key, ())
            if assignment.position < position
            and (
                not required_branches
                or _branches_compatible(
                    _branch_path_for_token(analysis, assignment.token_start),
                    required_branches,
                )
            )
        ),
        default=-1,
    )
    kinds = {
        kind
        for event_position, producer_token, kind in analysis.value_type_events.get(key, ())
        if latest_assignment < event_position < position
        and (
            not required_branches
            or _branches_compatible(
                _branch_path_for_token(analysis, producer_token),
                required_branches,
            )
        )
    }
    return next(iter(kinds)) if len(kinds) == 1 else ""


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
                dict.fromkeys(
                    left + _concatenation_value(right)
                    for left in bases
                    for right in suffix
                )
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
    compact = COMPACT_LABEL_RE.fullmatch(stripped)
    if compact is not None and not stripped.startswith("@L_"):
        normalized = compact.group("label").casefold()
        catalog_values = {normalized, normalized.lstrip("_")}
        if catalog_values & catalog or (not catalog and _label_family_base(normalized)):
            return ((normalized, value.find(stripped)),)
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
            or (not catalog and _label_family_base(normalized))
            or (allow_patterns and "*" in normalized)
        ):
            return ((normalized, value.find(stripped)),)
    return ()


def _normalize_label(label: str) -> str:
    value = label.strip()
    if value.startswith("@L_"):
        value = value[3:]
    elif value.startswith("@L"):
        value = value[2:]
    if value.endswith("+"):
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
    for argument_index, (start, end) in enumerate(
        call.argument_spans[runtime_start:],
        start=runtime_start,
    ):
        expression = call.arguments[argument_index]
        packed_table = variadic_argument_pack(expression)
        if packed_table and packed_table != expression:
            for ordinal in range(1, _MAX_VARIADIC_ARGUMENTS + 1):
                resolved = _resolve_variable(
                    analysis,
                    f"{packed_table}[{ordinal}]",
                    call.start,
                    call.function_index,
                    set(),
                    required_branches=required_branches,
                )
                candidates = tuple(dict.fromkeys(resolved))[:64]
                values.append(
                    tuple(
                        "" if _semantic_marker_kind(value) else value
                        for value in candidates
                    )
                )
                kinds.append(
                    tuple(
                        _semantic_marker_kind(value) or semantic_literal(value).kind
                        for value in candidates
                    )
                )
            continue
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
    if call.name.casefold() == "feedback_messageoffice":
        privilege_labels = _feedback_office_privilege_labels(analysis, call)
        values.append(privilege_labels)
        kinds.append((SEMANTIC_LABEL,) * len(privilege_labels))
    return tuple(values), tuple(kinds)


def _runtime_argument_expressions(
    call: ScriptCall,
    runtime_start: int,
) -> tuple[str, ...]:
    expressions = call.arguments[runtime_start:]
    if call.name.casefold() != "feedback_messageoffice":
        return expressions
    provider = call.arguments[1].strip() if len(call.arguments) > 1 else ""
    generated = f"{provider}()" if provider else "feedback_MessageOffice privileges"
    return (*expressions, generated)


def _feedback_office_privilege_labels(
    analysis: _Analysis,
    call: ScriptCall,
) -> tuple[str, ...]:
    """Model the extra privilege-list argument injected by feedback_MessageOffice."""
    if len(call.arguments) <= 1:
        return ("_MEASURE_*_NAME_+0",)
    provider = call.arguments[1].strip().casefold()
    target_indices = analysis.functions_by_alias.get(provider, ())
    privileges: list[str] = []
    for target_index in target_indices:
        recursion_key = (f"@office-privileges:{provider}", target_index)
        for start, end, position in analysis.returns_by_function.get(target_index, ()):
            for part_start, part_end in _split_token_range(
                analysis.tokens,
                start,
                end,
                ",",
            ):
                for value in _evaluate_tokens(
                    analysis,
                    part_start,
                    part_end,
                    position,
                    target_index,
                    {recursion_key},
                    required_branches=(),
                ):
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
                        privileges.append(value)
    labels = tuple(
        dict.fromkeys(f"_MEASURE_{privilege}_NAME_+0" for privilege in privileges)
    )
    return labels or ("_MEASURE_*_NAME_+0",)


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
