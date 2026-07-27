from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
from typing import Protocol

from .code_index import dynamic_label_patterns, normalize_label
from .i18n import translate
from .script_semantics import variadic_argument_pack


GLYPH_MARK = "\ufffc"
_NESTED_PLACEHOLDER_RE = re.compile(r"%(\d+)([A-Za-z]*)")


def placeholder_reference_score(text: str, reference: object) -> int:
    placeholders = tuple(
        dict.fromkeys(
            (int(match.group(1)), match.group(2) or "")
            for match in _NESTED_PLACEHOLDER_RE.finditer(text)
        )
    )
    return _reference_score(reference, placeholders)


def placeholder_reference_complete(text: str, reference: object) -> bool:
    placeholders = tuple(
        dict.fromkeys(
            int(match.group(1))
            for match in _NESTED_PLACEHOLDER_RE.finditer(text)
        )
    )
    return all(_placeholder_expressions(reference, number) for number in placeholders)


def _reference_score(reference: object, placeholders: tuple[tuple[int, str], ...]) -> int:
    confidence = int(getattr(reference, "confidence", 0) or 0)
    role = str(getattr(reference, "role", "") or "")
    role_score = {
        "body": 18,
        "header": 18,
        "template": 14,
        "runtime_label": 8,
        "button": 5,
        "assignment": -8,
        "gui_resource": -20,
    }.get(role, 0)
    score = confidence + role_score
    complete = True
    for number, suffix in placeholders:
        expressions = _placeholder_expressions(reference, number)
        if not expressions:
            complete = False
            score -= 18
            continue
        score += 5 + max(_expression_compatibility(expression, suffix) for expression in expressions)
    if complete:
        score += 16
    return score


def _expression_compatibility(expression: str, suffix: str) -> int:
    lowered = expression.casefold()
    normalized_suffix = suffix.upper()
    if normalized_suffix in {"SN", "SV", "SZ", "SA", "SK", "ST", "SD", "SB", "SL"}:
        if _plain_name_expression_kind(expression) == "character":
            return 10
        if "getid(" in lowered:
            return 8
        if "itemlabel" in lowered or "@l_" in lowered or lowered.lstrip().startswith("_"):
            return -6
        return 1
    if normalized_suffix == "DN":
        if "dynasty" in lowered or "dynid" in lowered:
            return 10
        return 3 if "getid(" in lowered else 0
    if normalized_suffix in {"GG", "GN", "GT"}:
        if "building" in lowered or "workbuilding" in lowered:
            return 9
        return 3 if "getid(" in lowered else 0
    if normalized_suffix == "NAME":
        semantic = _plain_name_expression_kind(expression)
        if semantic:
            return 9
        return 4 if _looks_like_object_expression(expression) else 0
    if suffix in {"", "l", "s"}:
        if "itemgetlabel" in lowered or "itemlabel" in lowered:
            return 12
        if "label" in lowered or "@l_" in lowered or lowered.lstrip().startswith("_"):
            return 8
        if "settlement" in lowered or "city" in lowered:
            return 5
        if "getid(" in lowered:
            return -1
        return 1
    if suffix in {"n", "i", "f", "t", "c", "z", "j"}:
        if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", expression.strip()):
            return 6
        if "getid(" in lowered or "@l_" in lowered:
            return -4
        return 2
    return 0


class PlaceholderLocalization(Protocol):
    target_language: str

    def character_name(
        self,
        seed_key: str,
        number: int,
        target: bool,
        *,
        forename_only: bool = False,
    ) -> str: ...

    def character_name_parts(
        self,
        seed_key: str,
        number: int,
        target: bool,
    ) -> tuple[str, str, str]: ...

    def sample_label(self, prefix: str, suffix: str, seed_key: str, number: int, target: bool) -> str: ...

    def sample_label_record(
        self,
        prefix: str,
        field_suffixes: tuple[str, ...],
        seed_key: str,
        number: int,
        target: bool,
    ) -> PlaceholderLabelRecord: ...

    def sample_character_profession(
        self,
        class_identity: str,
        seed_key: str,
        number: int,
        target: bool,
    ) -> str: ...

    def sample_character_office(
        self,
        seed_key: str,
        number: int,
        target: bool,
    ) -> str: ...

    def localized(self, label: str, target: bool) -> str: ...


@dataclass(frozen=True)
class PlaceholderContext:
    label: str
    file_rel: str
    target: bool
    locale: str
    references: tuple[object, ...] = ()
    argument_suffixes: tuple[tuple[int, tuple[str, ...]], ...] = ()

    @property
    def seed_key(self) -> str:
        return self.label or self.file_rel

    def suffixes_for(self, number: int) -> tuple[str, ...]:
        return next(
            (suffixes for argument_number, suffixes in self.argument_suffixes if argument_number == number),
            (),
        )


@dataclass(frozen=True)
class PlaceholderValue:
    text: str
    glyph_id: int | None = None


@dataclass(frozen=True)
class PlaceholderLabelRecord:
    identity: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class BuildingPreviewEntity:
    type_name: str
    proper_name: str

    def full_name(self) -> str:
        if self.type_name and self.proper_name:
            return f"{self.type_name}『{self.proper_name}』"
        return self.type_name or self.proper_name


@dataclass(frozen=True)
class CharacterPreviewEntity:
    forename: str
    surname: str
    title: str
    office: str
    class_name: str
    profession: str
    level: str
    city: str
    full_description_template: str

    @property
    def full_name(self) -> str:
        return " ".join(part for part in (self.forename, self.surname) if part)

    def full_description(self) -> str:
        values = {
            "ST": self.title,
            "SV": self.forename,
            "SD": self.surname,
            "SA": self.office,
            "NAME": self.city,
        }

        return re.sub(
            r"%1(ST|SV|SD|SA)|%2(NAME)",
            lambda match: values.get(match.group(1) or match.group(2), match.group(0)),
            self.full_description_template,
        )

    def project(self, suffix: str) -> str:
        return {
            "SN": self.full_name,
            "Sn": self.full_name,
            "SV": self.forename,
            "Sv": self.forename,
            "SZ": self.full_description(),
            "Sz": self.full_description(),
            "SK": self.class_name,
            "ST": self.title,
            "SA": self.office,
            "SD": self.surname,
            "SB": self.profession,
            "SL": self.level,
        }.get(suffix, "")


class PlaceholderEntityResolver:
    """Build one coherent preview entity before projecting placeholder fields."""

    def __init__(self, localization: PlaceholderLocalization) -> None:
        self.localization = localization

    def building(self, number: int, context: PlaceholderContext) -> BuildingPreviewEntity:
        record = self.localization.sample_label_record(
            "_BUILDING_",
            ("_NAME_+0", "_POOL_+0"),
            context.seed_key,
            number,
            context.target,
        )
        type_name, proper_name = record.values
        return BuildingPreviewEntity(
            _clean_sample_text(type_name)
            or translate("preview.value.building_type", locale=context.locale, number=number),
            _clean_sample_text(proper_name)
            or translate("preview.value.building_name", locale=context.locale, number=number),
        )

    def character(self, number: int, context: PlaceholderContext) -> CharacterPreviewEntity:
        forename, surname, gender = self.localization.character_name_parts(
            context.seed_key,
            number,
            context.target,
        )
        class_record = self.localization.sample_label_record(
            "_CHARACTERS_1_CLASSES_",
            ("_NAME_+0", "_LEVEL_+0"),
            context.seed_key,
            number,
            context.target,
        )
        class_name, level = class_record.values
        title_label = "_CHARACTERS_3_TITLES_NAME_+8" if gender == "female" else "_CHARACTERS_3_TITLES_NAME_+9"
        title = self.localization.localized(title_label, context.target)
        if title == title_label:
            title = translate("preview.value.title", locale=context.locale, number=number)
        needs_office = "SA" in context.suffixes_for(number)
        office = (
            self.localization.sample_character_office(
                context.seed_key,
                number,
                context.target,
            )
            if needs_office
            else ""
        )
        profession = self.localization.sample_character_profession(
            class_record.identity,
            context.seed_key,
            number,
            context.target,
        )
        template_label = "SubstSimFullDescOffice_+0" if office else "SubstSimFullDescNoOffice_+0"
        template = self.localization.localized(template_label, context.target)
        if template == template_label:
            template = "%1ST %1SV %1SD, %1SA in %2NAME" if office else "%1ST %1SV %1SD"
        return CharacterPreviewEntity(
            forename=forename,
            surname=surname,
            title=_clean_sample_text(title),
            office=_clean_sample_text(office),
            class_name=_clean_sample_text(class_name)
            or translate("preview.value.class", locale=context.locale, number=number),
            profession=_clean_sample_text(profession)
            or translate("preview.value.profession", locale=context.locale, number=number),
            level=_clean_sample_text(level)
            or translate("preview.value.level", locale=context.locale, number=number),
            city=_city_value(self.localization, number, context),
            full_description_template=template,
        )


class PlaceholderValueBuilder:
    def __init__(self, localization: PlaceholderLocalization) -> None:
        self.localization = localization
        self.entities = PlaceholderEntityResolver(localization)

    def argument_value(
        self,
        number: int,
        suffix: str,
        context: PlaceholderContext,
        _depth: int = 0,
    ) -> PlaceholderValue:
        if suffix in {"SN", "Sn", "SV", "Sv", "SZ", "Sz", "SK", "ST", "SA", "SD", "SB", "SL"}:
            return PlaceholderValue(self.entities.character(number, context).project(suffix))
        if suffix == "DS":
            return PlaceholderValue(GLYPH_MARK, 2029 + _stable_index(f"{context.seed_key}:{number}:crest", 17))
        explicit = self._explicit_argument_value(number, suffix, context)
        if explicit is not None:
            return explicit
        if suffix in {"", "l", "s"}:
            localized = _localized_argument_value(self.localization, number, context)
            if localized:
                if _depth < 3:
                    localized = self._resolve_nested_placeholders(localized, number, suffix, context, _depth + 1)
                return PlaceholderValue(_clean_sample_text(localized))
            semantic = _semantic_kind(number, context)
            if semantic == "character":
                return PlaceholderValue(
                    self.localization.character_name(context.seed_key, number, context.target)
                )
            if semantic == "item":
                item = self.localization.sample_label("_ITEM_", "_NAME_+0", context.seed_key, number, context.target)
                if item:
                    return PlaceholderValue(_clean_sample_text(item))
            if semantic == "building":
                return PlaceholderValue(self.entities.building(number, context).proper_name)
            if semantic == "city":
                return PlaceholderValue(_city_value(self.localization, number, context))
        values = {
            "n": "preview.value.number",
            "i": "preview.value.integer",
            "f": "preview.value.float",
            "t": "preview.value.money",
            "c": "preview.value.time",
            "z": "preview.value.duration",
            "j": "preview.value.date",
            "s": "preview.value.string",
            "l": "preview.value.label",
            "": "preview.value.argument",
        }
        key = values.get(suffix, "preview.value.argument")
        return PlaceholderValue(translate(key, locale=context.locale, number=number))

    def _resolve_nested_placeholders(
        self,
        text: str,
        current_number: int,
        current_suffix: str,
        context: PlaceholderContext,
        depth: int,
    ) -> str:
        def replace(match: re.Match[str]) -> str:
            number = int(match.group(1))
            suffix = match.group(2) or ""
            if number == current_number and suffix == current_suffix:
                return match.group(0)
            return self.argument_value(number, suffix, context, depth).text

        return _NESTED_PLACEHOLDER_RE.sub(replace, text)

    def _explicit_argument_value(
        self,
        number: int,
        suffix: str,
        context: PlaceholderContext,
    ) -> PlaceholderValue | None:
        if suffix == "NAME":
            semantic = _name_semantic_kind(number, context)
            if semantic == "character":
                return PlaceholderValue(
                    self.localization.character_name(context.seed_key, number, context.target)
                )
            if semantic == "building":
                return PlaceholderValue(self.entities.building(number, context).proper_name)
            if semantic == "city":
                return PlaceholderValue(_city_value(self.localization, number, context))
            if semantic == "dynasty":
                full = self.localization.character_name(context.seed_key, number, context.target)
                dynasty = full.rsplit(" ", 1)[-1] if full else ""
                return PlaceholderValue(
                    dynasty or translate("preview.value.dynasty", locale=context.locale, number=number)
                )
            return PlaceholderValue(
                translate("preview.value.object_name", locale=context.locale, number=number)
            )
        if suffix == "GG":
            return PlaceholderValue(self.entities.building(number, context).full_name())
        if suffix == "GN":
            return PlaceholderValue(self.entities.building(number, context).proper_name)
        if suffix == "GT":
            return PlaceholderValue(self.entities.building(number, context).type_name)
        if suffix == "DN":
            dynasty = self.entities.character(number, context).surname
            return PlaceholderValue(dynasty or translate("preview.value.dynasty", locale=context.locale, number=number))
        return None

    def named_value(self, token: str, context: PlaceholderContext) -> PlaceholderValue:
        name = token[1:-1]
        if name == "n":
            return PlaceholderValue("\n")
        if name in {"gold_icon", "hp_icon", "xp_icon", "my_crest"}:
            glyphs = {"gold_icon": 2002, "hp_icon": 2003, "xp_icon": 2056, "my_crest": 2029}
            return PlaceholderValue(GLYPH_MARK, glyphs[name])
        if name == "char_name":
            return PlaceholderValue(self.localization.character_name(context.seed_key, 1, context.target))
        if name == "spouse":
            return PlaceholderValue(self.localization.character_name(context.seed_key, 2, context.target))
        if name == "dyn_surname":
            full = self.localization.character_name(context.seed_key, 1, context.target)
            return PlaceholderValue(full.rsplit(" ", 1)[-1])
        key = {
            "gold": "preview.value.money_plain",
            "treasury": "preview.value.money_plain",
            "wealth": "preview.value.money_plain",
            "fame": "preview.value.number",
            "imperial_fame": "preview.value.number",
            "hp_cur": "preview.value.hp_current",
            "hp_max": "preview.value.hp_max",
            "xp": "preview.value.xp",
            "level": "preview.value.level",
            "settlement": "preview.value.city",
            "settlement_level": "preview.value.level",
            "settlement_tier_name": "preview.value.settlement_tier",
            "nobility": "preview.value.nobility",
            "children": "preview.value.children",
            "marriage_status": "preview.value.marriage",
            "turnover_tax": "preview.value.percent",
            "church_tithe": "preview.value.percent",
            "severity_of_law_name": "preview.value.law",
        }.get(name, "preview.value.named")
        return PlaceholderValue(translate(key, locale=context.locale, name=name, number=1))


def _stable_index(seed: str, size: int) -> int:
    if size <= 0:
        return 0
    import hashlib

    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little") % size


def _semantic_kind(number: int, context: PlaceholderContext) -> str:
    kinds: set[str] = set()
    for reference in context.references:
        for expression in _placeholder_expressions(reference, number):
            lowered = expression.casefold()
            if "itemgetlabel" in lowered or "itemlabel" in lowered:
                candidate = "item"
            elif "citylabel" in lowered or "settlement" in lowered or "city" in lowered:
                candidate = "city"
            elif "workbuilding" in lowered or "building" in lowered:
                candidate = "building"
            elif _plain_name_expression_kind(expression) == "character":
                candidate = "character"
            else:
                candidate = ""
            if candidate:
                kinds.add(candidate)
    return next(iter(kinds)) if len(kinds) == 1 else ""


def _name_semantic_kind(number: int, context: PlaceholderContext) -> str:
    kinds: set[str] = set()
    for reference in context.references:
        for expression in _placeholder_expressions(reference, number):
            candidate = _plain_name_expression_kind(expression)
            if candidate:
                kinds.add(candidate)
    return next(iter(kinds)) if len(kinds) == 1 else ""


def _plain_name_expression_kind(expression: str) -> str:
    """Return an object type only when the caller expression supplies direct evidence.

    ``%NAME`` itself is type-neutral: the game asks the object passed by the caller
    for its plain name.  A bare ``GetID(...)`` therefore does not prove that the
    object is a sim, building, or settlement.
    """
    lowered = expression.casefold()
    if "getsettlement" in lowered or "citylabel" in lowered:
        return "city"
    if "workbuilding" in lowered or "getbuilding" in lowered:
        return "building"
    if "getdynasty" in lowered or "dynastyget" in lowered or "dynid" in lowered:
        return "dynasty"
    if "simget" in lowered or "getsim" in lowered:
        return "character"
    alias_match = re.search(r"getid\s*\(\s*(['\"])([^'\"]+)\1\s*\)", expression, re.IGNORECASE)
    if alias_match is None:
        return ""
    alias = alias_match.group(2).casefold()
    if "settlement" in alias or re.search(r"(^|_)city($|_)", alias):
        return "city"
    if "building" in alias:
        return "building"
    if "dynasty" in alias or alias.startswith("dyn"):
        return "dynasty"
    if re.search(r"(^|_)(sim|person|character)($|_)", alias):
        return "character"
    return ""


def _looks_like_object_expression(expression: str) -> bool:
    lowered = expression.casefold()
    return any(
        marker in lowered
        for marker in ("getid(", "getsettlement", "getbuilding", "getdynasty", "simget", "getsim")
    )


def _placeholder_expression(reference: object, number: int) -> str:
    runtime_arguments = getattr(reference, "runtime_arguments", ())
    if isinstance(runtime_arguments, tuple) and runtime_arguments:
        remaining = number
        for index, expression in enumerate(runtime_arguments):
            value = str(expression)
            pack = variadic_argument_pack(value) if index == len(runtime_arguments) - 1 else ""
            if pack:
                return f"{pack}[{remaining}]" if remaining > 0 else ""
            if remaining == 1:
                return value
            remaining -= 1
        return ""
    argument_index = getattr(reference, "argument_index", None)
    arguments = getattr(reference, "arguments", ())
    if not isinstance(argument_index, int) or not isinstance(arguments, tuple):
        return ""
    base = argument_index + 1
    current_argument = str(arguments[argument_index]) if 0 <= argument_index < len(arguments) else ""
    if _is_button_argument(current_argument):
        while base < len(arguments) and _is_window_text_argument(str(arguments[base])):
            base += 1
    elif _is_localization_text_argument(current_argument):
        current_label = _localization_text_label(current_argument)
        while (
            current_label
            and base < len(arguments)
            and _is_related_window_text_label(current_label, _localization_text_label(str(arguments[base])))
        ):
            base += 1
    index = base + number - 1
    if 0 <= index < len(arguments):
        return str(arguments[index])
    return ""


def _placeholder_expressions(reference: object, number: int) -> tuple[str, ...]:
    values: list[str] = []
    expression = _placeholder_expression(reference, number)
    if expression:
        values.append(expression)
    runtime_values = getattr(reference, "runtime_argument_values", ())
    if isinstance(runtime_values, tuple) and 0 < number <= len(runtime_values):
        candidates = runtime_values[number - 1]
        if isinstance(candidates, tuple):
            for candidate in candidates:
                value = str(candidate)
                if value and value not in values:
                    values.append(value)
    return tuple(values)


def _is_paired_body_argument(reference: object, expression: str) -> bool:
    current = str(getattr(reference, "label", "") or "").strip().lstrip("_").casefold()
    next_label = _localization_text_label(expression)
    if not current or not next_label:
        return False
    paired = _paired_body_label(current)
    return bool(paired and paired == next_label.casefold())


def _paired_body_label(label: str) -> str:
    if "_head_" in label:
        return label.replace("_head_", "_body_", 1)
    if label.endswith("_head"):
        return f"{label[:-5]}_body"
    return ""


def _is_related_window_text_label(current_label: str, next_label: str) -> bool:
    if not current_label or not next_label:
        return False
    current = current_label.casefold()
    next_value = next_label.casefold()
    paired = _paired_body_label(current)
    if paired and paired == next_value:
        return True
    match = re.match(r"^(.*_)(head|header)(_\+.*)?$", current)
    if match is None:
        return False
    prefix, _, suffix = match.groups()
    suffix = suffix or ""
    suffix_join = f"{prefix[:-1]}{suffix}" if suffix.startswith("_+") and prefix.endswith("_") else f"{prefix}{suffix}"
    if next_value == suffix_join:
        return True
    return bool(re.match(rf"^{re.escape(prefix)}(body|text|question|answer){re.escape(suffix)}$", next_value))


def _literal_localization_label(expression: str) -> str:
    stripped = expression.strip()
    if ".." in stripped:
        return ""
    value = stripped.strip('"').strip("'")
    if not value.startswith("@L_"):
        return ""
    return value[3:].lstrip("_")


def _is_button_argument(expression: str) -> bool:
    return "@B[" in expression


def _is_localization_text_argument(expression: str) -> bool:
    return bool(_localization_text_label(expression))


def _is_window_text_argument(expression: str) -> bool:
    return _looks_like_window_text_label(_localization_text_label(expression))


def _looks_like_window_text_label(label: str) -> bool:
    value = label.casefold()
    return bool(re.search(r"(^|_)(head|header|body|text|question|answer)(_|$)", value))


def _localization_text_label(expression: str) -> str:
    literal = _literal_localization_label(expression)
    if literal:
        return literal
    dynamic = dynamic_label_patterns(expression)
    if dynamic:
        return dynamic[0].lstrip("_")
    return ""


def _localized_argument_value(
    localization: PlaceholderLocalization,
    number: int,
    context: PlaceholderContext,
) -> str:
    for reference in context.references:
        expression = _placeholder_expression(reference, number)
        value = _localized_expression_value(localization, expression, number, context)
        if not value:
            value = _localized_variable_value(localization, reference, expression, number, context)
        if value:
            return value
    return ""


def _localized_expression_value(
    localization: PlaceholderLocalization,
    expression: str,
    number: int,
    context: PlaceholderContext,
) -> str:
    contextual = _contextual_dynamic_label(expression, context.label)
    if contextual:
        value = localization.localized(contextual, context.target)
        if value and value != contextual:
            return value
    labels = _literal_label_candidates(expression)
    if labels:
        contextual_label = _matching_context_label(labels, context.label)
        if len(labels) == 1 or contextual_label:
            label = contextual_label or labels[0]
            value = localization.localized(label, context.target)
            if value and value != label:
                return value
    for prefix, suffix in _dynamic_sample_candidates(expression):
        value = localization.sample_label(prefix, suffix, context.seed_key, number, context.target)
        if value:
            return value
    return ""


def _localized_variable_value(
    localization: PlaceholderLocalization,
    reference: object,
    expression: str,
    number: int,
    context: PlaceholderContext,
) -> str:
    variable = expression.strip()
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", variable):
        return ""
    runtime_values = getattr(reference, "runtime_argument_values", ())
    resolved_values: list[str] = []
    if isinstance(runtime_values, tuple) and 0 < number <= len(runtime_values):
        candidates = runtime_values[number - 1]
        if isinstance(candidates, tuple):
            for candidate in candidates:
                value = _localized_expression_value(
                    localization,
                    str(candidate),
                    number,
                    context,
                )
                if value:
                    resolved_values.append(value)
    resolved_values = list(dict.fromkeys(resolved_values))
    if len(resolved_values) == 1:
        return resolved_values[0]
    if len(resolved_values) > 1:
        return ""
    path = getattr(reference, "path", None)
    line = getattr(reference, "line", None)
    if path is None or not isinstance(line, int):
        return ""
    labels, dynamic_samples = _variable_label_sources(str(path), line, variable)
    values: list[str] = []
    for label in labels:
        value = localization.localized(label, context.target)
        if value and value != label:
            values.append(value)
    for prefix, suffix in dynamic_samples:
        value = localization.sample_label(prefix, suffix, context.seed_key, number, context.target)
        if value:
            values.append(value)
    values = list(dict.fromkeys(values))
    if len(values) != 1:
        return ""
    return values[0]


def _contextual_dynamic_label(expression: str, context_label: str) -> str:
    if not context_label:
        return ""
    normalized = normalize_label(context_label).lstrip("_")
    for pattern in dynamic_label_patterns(expression):
        if _wildcard_label_matches(pattern.lstrip("_"), normalized):
            return context_label if context_label.startswith("_") else "_" + context_label
    return ""


def _matching_context_label(labels: tuple[str, ...], context_label: str) -> str:
    normalized = normalize_label(context_label).lstrip("_")
    return next(
        (label for label in labels if normalize_label(label).lstrip("_") == normalized),
        "",
    )


def _wildcard_label_matches(pattern: str, label: str) -> bool:
    regex = "^" + re.escape(pattern).replace(r"\*", ".*") + "$"
    return re.match(regex, label, re.IGNORECASE) is not None


@lru_cache(maxsize=2048)
def _variable_label_sources(path: str, line: int, variable: str) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    try:
        text = open(path, "r", encoding="utf-8", errors="ignore").read()
    except OSError:
        return (), ()
    prefix = "\n".join(text.splitlines()[: max(0, line - 1)])
    assignment_re = re.compile(
        rf"(?:^|\n)\s*(?:local\s+)?{re.escape(variable)}\s*=\s*(?P<expr>[^\n\r]*)",
        re.IGNORECASE,
    )
    labels: list[str] = []
    dynamic_samples: list[tuple[str, str]] = []
    for match in assignment_re.finditer(prefix):
        expr = match.group("expr")
        for label in _literal_label_candidates(expr):
            if label not in labels:
                labels.append(label)
        for sample in _dynamic_sample_candidates(expr):
            if sample not in dynamic_samples:
                dynamic_samples.append(sample)
    return tuple(labels), tuple(dynamic_samples)


def _dynamic_sample_candidates(expression: str) -> tuple[tuple[str, str], ...]:
    samples: list[tuple[str, str]] = []
    for label in dynamic_label_patterns(expression, normalized=False):
        if label.startswith("@L_"):
            label = "_" + label[3:].lstrip("_")
        if label.endswith("_+"):
            label += "*"
        star_index = label.find("*")
        if star_index < 0:
            continue
        sample = (label[:star_index], label[star_index + 1 :])
        if sample not in samples:
            samples.append(sample)
    return tuple(samples)


def _literal_label_candidates(expression: str) -> tuple[str, ...]:
    labels: list[str] = []
    for match in _LABEL_LITERAL_RE.finditer(expression):
        label = match.group(1) or match.group(2)
        if not label:
            continue
        if label.startswith("@L_"):
            label = "_" + label[3:].lstrip("_")
        elif not label.startswith("_"):
            label = "_" + label
        if label not in labels:
            labels.append(label)
    return tuple(labels)


_LABEL_LITERAL_RE = re.compile(
    r"(@L_[A-Za-z0-9_]+_\+[A-Za-z0-9*]+)|(?<![A-Za-z0-9])(_[A-Za-z0-9_]+_\+[A-Za-z0-9*]+)"
)


def _city_value(localization: PlaceholderLocalization, number: int, context: PlaceholderContext) -> str:
    value = localization.sample_label("_CITY_NAME_", "_+0", context.seed_key, number, context.target)
    return _clean_sample_text(value) if value else translate("preview.value.city", locale=context.locale, number=number)


def _clean_sample_text(value: str) -> str:
    import re

    cleaned = re.sub(r"#E\[[^\]]+\]", "", value)
    cleaned = cleaned.replace("$N", " ").replace("$T", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()
