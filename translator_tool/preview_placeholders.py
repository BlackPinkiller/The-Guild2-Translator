from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
from typing import Protocol

from .code_index import dynamic_label_patterns
from .i18n import translate


GLYPH_MARK = "\ufffc"
_NESTED_PLACEHOLDER_RE = re.compile(r"%(\d+)([A-Za-z]*)")


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

    def sample_label(self, prefix: str, suffix: str, seed_key: str, number: int, target: bool) -> str: ...

    def localized(self, label: str, target: bool) -> str: ...


@dataclass(frozen=True)
class PlaceholderContext:
    label: str
    file_rel: str
    target: bool
    locale: str
    references: tuple[object, ...] = ()

    @property
    def seed_key(self) -> str:
        return self.label or self.file_rel


@dataclass(frozen=True)
class PlaceholderValue:
    text: str
    glyph_id: int | None = None


class PlaceholderValueBuilder:
    def __init__(self, localization: PlaceholderLocalization) -> None:
        self.localization = localization

    def argument_value(
        self,
        number: int,
        suffix: str,
        context: PlaceholderContext,
        _depth: int = 0,
    ) -> PlaceholderValue:
        if suffix in {"SN", "Sn", "SZ", "Sz"}:
            return PlaceholderValue(
                self.localization.character_name(context.seed_key, number, context.target)
            )
        if suffix in {"SV", "Sv"}:
            return PlaceholderValue(
                self.localization.character_name(
                    context.seed_key,
                    number,
                    context.target,
                    forename_only=True,
                )
            )
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
                return PlaceholderValue(_building_name_value(self.localization, number, context))
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
                return PlaceholderValue(_building_name_value(self.localization, number, context))
            return PlaceholderValue(_city_value(self.localization, number, context))
        if suffix == "GG":
            building_name = _building_name_value(self.localization, number, context)
            building_type = _building_type_value(self.localization, number, context)
            if building_name and building_type and building_type.casefold() not in building_name.casefold():
                return PlaceholderValue(f"{building_name} {building_type}")
            value = building_name or building_type or translate("preview.value.building_full", locale=context.locale, number=number)
            return PlaceholderValue(value)
        if suffix == "GN":
            return PlaceholderValue(_building_name_value(self.localization, number, context))
        if suffix == "GT":
            return PlaceholderValue(_building_type_value(self.localization, number, context))
        if suffix == "SL":
            value = self.localization.sample_label("_CHARACTERS_1_CLASSES_", "_LEVEL_+0", context.seed_key, number, context.target)
            return PlaceholderValue(_clean_sample_text(value) if value else translate("preview.value.level", locale=context.locale, number=number))
        if suffix == "DN":
            full = self.localization.character_name(context.seed_key, number, context.target)
            dynasty = full.rsplit(" ", 1)[-1] if full else ""
            return PlaceholderValue(dynasty or translate("preview.value.dynasty", locale=context.locale, number=number))
        samples = {
            "SK": ("_CHARACTERS_1_CLASSES_", "_NAME_+0", "preview.value.class"),
            "ST": ("_CHARACTERS_3_TITLES_NAME_+", "", "preview.value.title"),
            "SA": ("_CHARACTERS_3_OFFICES_NAME_", "_+0", "preview.value.office"),
            "SD": ("_CHARACTERS_3_TITLES_NAME_+", "", "preview.value.nobility"),
            "SB": ("_CHARACTERS_2_PROFESSIONS_", "_NAME_+0", "preview.value.profession"),
        }
        sample = samples.get(suffix)
        if sample is None:
            return None
        prefix, label_suffix, fallback_key = sample
        value = self.localization.sample_label(prefix, label_suffix, context.seed_key, number, context.target)
        if value:
            return PlaceholderValue(_clean_sample_text(value))
        return PlaceholderValue(translate(fallback_key, locale=context.locale, number=number))

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
    priority = {"item": 4, "city": 3, "building": 2, "character": 1}
    best = ""
    for reference in context.references:
        expression = _placeholder_expression(reference, number)
        lowered = expression.casefold()
        if not lowered:
            continue
        if "itemgetlabel" in lowered or "itemlabel" in lowered:
            candidate = "item"
        elif "citylabel" in lowered or "settlement" in lowered or "city" in lowered:
            candidate = "city"
        elif "workbuilding" in lowered or "building" in lowered:
            candidate = "building"
        elif "getid(" in lowered or '"owner"' in lowered or "'owner'" in lowered:
            candidate = "character"
        else:
            candidate = ""
        if priority.get(candidate, 0) > priority.get(best, 0):
            best = candidate
    return best


def _name_semantic_kind(number: int, context: PlaceholderContext) -> str:
    for reference in context.references:
        expression = _placeholder_expression(reference, number)
        next_expression = _placeholder_expression(reference, number + 1)
        lowered = expression.casefold()
        next_lowered = next_expression.casefold()
        if "workbuilding" in lowered or "building" in lowered:
            return "building"
        if 'getid("")' in lowered:
            return "building"
        if "getsettlement" in lowered or "citylabel" in lowered or "settlement" in lowered or "city" in lowered:
            return "city"
        if "getsettlement" in next_lowered and "getid(" in lowered:
            return "city"
        if '"owner"' in lowered or "'owner'" in lowered:
            return "character"
    return ""


def _placeholder_expression(reference: object, number: int) -> str:
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
    labels = _literal_label_candidates(expression)
    if labels:
        label = labels[_stable_index(f"{context.seed_key}:{number}:{expression}:label", len(labels))]
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
    if not values:
        return ""
    return values[_stable_index(f"{context.seed_key}:{number}:{variable}:variable-label", len(values))]


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


def _building_name_value(localization: PlaceholderLocalization, number: int, context: PlaceholderContext) -> str:
    value = localization.sample_label("_BUILDING_", "_POOL_+0", context.seed_key, number, context.target)
    return _clean_sample_text(value) if value else translate("preview.value.building_name", locale=context.locale, number=number)


def _building_type_value(localization: PlaceholderLocalization, number: int, context: PlaceholderContext) -> str:
    value = localization.sample_label("_BUILDING_", "_NAME_+0", context.seed_key, number, context.target)
    return _clean_sample_text(value) if value else translate("preview.value.building_type", locale=context.locale, number=number)


def _clean_sample_text(value: str) -> str:
    import re

    cleaned = re.sub(r"#E\[[^\]]+\]", "", value)
    cleaned = cleaned.replace("$N", " ").replace("$T", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()
