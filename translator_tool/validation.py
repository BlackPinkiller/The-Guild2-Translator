from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
import unicodedata

from .codec_adapter import Guild2Codec
from .i18n import translate


ARG_SUFFIXES = (
    "NAME",
    "GG",
    "GN",
    "GT",
    "SN",
    "Sn",
    "SV",
    "Sv",
    "SZ",
    "Sz",
    "SK",
    "ST",
    "SA",
    "SD",
    "SB",
    "SL",
    "DN",
    "DS",
    "n",
    "i",
    "f",
    "t",
    "c",
    "z",
    "j",
    "s",
    "l",
)
FORMAT_GUILD2 = "guild2"
FORMAT_GUIDE = "guide"
FORMAT_TOOLTIP = "tooltip"

KNOWN_GENDER_SUFFIXES = ("Male", "Female")
ARG_SUFFIX = "(?:" + "|".join(re.escape(suffix) for suffix in ARG_SUFFIXES) + ")"
ARG_PLAIN_TOKEN = r"%\d+(?![A-Za-z0-9_:])"
# The engine stops parsing as soon as a known suffix is complete, even when
# translators continue immediately with letters or digits such as `%2NAMEwe`
# or `%2GG6小时`.
ARG_TOKEN = rf"(?:{ARG_PLAIN_TOKEN}|%\d+{ARG_SUFFIX})"
PRINTF_TOKEN = r"%(?:\d+\$)?[-+#0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[diufFeEgGxXos](?![A-Za-z0-9])"
NAMED_PERCENT_TOKEN = r"%[A-Za-z][A-Za-z0-9_:-]*%"
LITERAL_PERCENT_TOKEN = r"%(?=$|[\s$.,:;!?()\[\]{}\"'”’<>]|[^\x00-\x7F])|(?<=\d)%(?![A-Za-z0-9_:])"
PERCENT_TOKEN = rf"%%|%[<>]|{ARG_TOKEN}|{PRINTF_TOKEN}|{LITERAL_PERCENT_TOKEN}"

BYTE_TOKEN = r"(?:0|[1-9]\d?|1\d\d|2[0-4]\d|25[0-5])"
COLOR_TOKEN = rf"\$C\s*\[\s*{BYTE_TOKEN}(?:\s*,\s*{BYTE_TOKEN}){{2,3}}\s*\]"
COLOR_TOKEN_RE = re.compile(rf"^{COLOR_TOKEN}$")
FONT_TOKEN = r"\$F\[[^\]\r\n]*\]"
SYMBOL_TOKEN = r"\$S\[[^\]\r\n]*\]"
BACKGROUND_TOKEN = r"\$B\[[^\]\r\n]*\]"
# Ornamental/literal bracket decoration.  It has no runtime placeholder
# semantics, and real text may use short forms such as `$[ $(` to show bracket
# glyphs, so recognize it broadly and exclude it from source/target diffs.
HEADER_TOKEN = r"\$\[(?:[^\r\n]*?\$\]|[^\r\n]*?\$|(?=\s|$))"
TAB_LAYOUT_TOKEN = r"\$T(?=\$|#|@|%|<|>|[ \t\r\n.,:;!?()\[\]{}]|$)"
LINE_OR_LAYOUT_TOKEN = rf"\$[NLRZ<>]|{TAB_LAYOUT_TOKEN}"
EMOTION_TOKEN = r"#E\[[A-Za-z0-9_]+\]"
SPEECH_TIMING_TOKEN = r"#SP[+-]"
NAME_SUFFIX_TOKEN = r"@N[A-Za-z]+"
LOCALIZATION_TOKEN = r"@L_[A-Za-z0-9_]+_\+[A-Za-z0-9]+"
INLINE_FALLBACK_TOKEN = r'@T"(?:[^"\\\r\n]|\\.)*"'

GUIDE_TAG_TOKEN = r"</?(?:header|text|separator|list|item|table|row|cell)\b[^>\r\n]*>"
GUIDE_INLINE_TOKEN = r"\{/?[A-Za-z_]+(?::[A-Za-z0-9_:-]+)*\}"
GUIDE_ATTR_TOKEN = (
    r"\[(?:"
    r'type="(?:bullet|numbered)"|'
    r'font="[^"\]\r\n]+"|'
    r"columns=\d+|"
    r"[rgb]=\d{1,3}|"
    r'link="[A-Za-z0-9_:-]+"'
    r")\]"
)
GUIDE_TOC_TOKEN = r"\[(?:Category|Page|SubCategory|AutoPages)\]"

QUOTE_STYLE_TOKEN = r"(?<!<)>[^<>\r\n]{1,160}<(?!/?[A-Za-z][^>]*>)"

GUILD2_TOKEN_RE = re.compile(
    "|".join(
        [
            PERCENT_TOKEN,
            COLOR_TOKEN,
            FONT_TOKEN,
            SYMBOL_TOKEN,
            BACKGROUND_TOKEN,
            HEADER_TOKEN,
            LINE_OR_LAYOUT_TOKEN,
            EMOTION_TOKEN,
            SPEECH_TIMING_TOKEN,
            NAME_SUFFIX_TOKEN,
            LOCALIZATION_TOKEN,
            INLINE_FALLBACK_TOKEN,
        ]
    )
)
GUIDE_TOKEN_RE = re.compile(
    "|".join(
        [
            GUIDE_TAG_TOKEN,
            GUIDE_INLINE_TOKEN,
            GUIDE_ATTR_TOKEN,
            GUIDE_TOC_TOKEN,
        ]
    )
)
TOOLTIP_TOKEN_RE = re.compile("|".join((ARG_TOKEN, NAMED_PERCENT_TOKEN, SYMBOL_TOKEN)))

GUILD2_HIGHLIGHT_RE = re.compile(
    "|".join(
        [
            PERCENT_TOKEN,
            COLOR_TOKEN,
            FONT_TOKEN,
            SYMBOL_TOKEN,
            BACKGROUND_TOKEN,
            HEADER_TOKEN,
            LINE_OR_LAYOUT_TOKEN,
            EMOTION_TOKEN,
            SPEECH_TIMING_TOKEN,
            NAME_SUFFIX_TOKEN,
            LOCALIZATION_TOKEN,
            INLINE_FALLBACK_TOKEN,
            QUOTE_STYLE_TOKEN,
        ]
    )
)
GUIDE_HIGHLIGHT_RE = re.compile(
    "|".join(
        [
            GUIDE_TAG_TOKEN,
            GUIDE_INLINE_TOKEN,
            GUIDE_ATTR_TOKEN,
            GUIDE_TOC_TOKEN,
        ]
    )
)
TOOLTIP_HIGHLIGHT_RE = TOOLTIP_TOKEN_RE

# Existing callers that explicitly handle ordinary Guild 2 DBT syntax can keep
# importing these aliases.
TOKEN_RE = GUILD2_TOKEN_RE
HIGHLIGHT_RE = GUILD2_HIGHLIGHT_RE
PROTECTED_TOKEN_RE = re.compile(
    "|".join((GUILD2_TOKEN_RE.pattern, TOOLTIP_TOKEN_RE.pattern, GUIDE_TOKEN_RE.pattern))
)

CHINESE_QUOTE_RE = re.compile(r"[\u201C\u201D\u2018\u2019]")
ARG_TOKEN_RE = re.compile(ARG_TOKEN)
NAME_SUFFIX_TOKEN_RE = re.compile(rf"^{NAME_SUFFIX_TOKEN}$")
UNKNOWN_PERCENT_RE = re.compile(r"%(?:\d*[A-Za-z][A-Za-z0-9]*|[^\s])?")


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    message: str
    code: str = ""

    @property
    def blocks_save(self) -> bool:
        return self.severity == "error"


def format_dialect(file_rel: str, kind: str = "dbt") -> str:
    normalized = file_rel.replace("\\", "/").casefold()
    if kind == "text" or normalized.startswith("guides/"):
        return FORMAT_GUIDE
    if normalized.rsplit("/", 1)[-1] == "tooltips.dbt":
        return FORMAT_TOOLTIP
    return FORMAT_GUILD2


def token_re_for(dialect: str) -> re.Pattern[str]:
    if dialect == FORMAT_GUIDE:
        return GUIDE_TOKEN_RE
    if dialect == FORMAT_TOOLTIP:
        return TOOLTIP_TOKEN_RE
    return GUILD2_TOKEN_RE


def highlight_re_for(dialect: str) -> re.Pattern[str]:
    if dialect == FORMAT_GUIDE:
        return GUIDE_HIGHLIGHT_RE
    if dialect == FORMAT_TOOLTIP:
        return TOOLTIP_HIGHLIGHT_RE
    return GUILD2_HIGHLIGHT_RE


def format_tokens(text: str, *, dialect: str = FORMAT_GUILD2) -> Counter[str]:
    return Counter(match.group(0) for match in token_re_for(dialect).finditer(text))


def normalize_color_token_spacing(text: str) -> str:
    if "$C[" not in text:
        return text
    matches = list(TOKEN_RE.finditer(text))
    if not matches:
        return text
    token_by_end = {match.end(): match for match in matches}
    insertion_points: set[int] = set()
    for match in matches:
        token = match.group(0)
        if not COLOR_TOKEN_RE.fullmatch(token):
            continue
        run_start = match.start()
        while True:
            previous = token_by_end.get(run_start)
            if previous is None or not _is_color_spacing_prefix_token(previous.group(0)):
                break
            run_start = previous.start()
        if _should_insert_space_before_run(text, run_start):
            insertion_points.add(run_start)
    if not insertion_points:
        return text
    parts: list[str] = []
    cursor = 0
    for point in sorted(insertion_points):
        parts.append(text[cursor:point])
        parts.append(" ")
        cursor = point
    parts.append(text[cursor:])
    return "".join(parts)


def _is_color_spacing_prefix_token(token: str) -> bool:
    if COLOR_TOKEN_RE.fullmatch(token):
        return True
    if token in {"$N", "$Z", "$L", "$R", "$T", "$>", "$<"}:
        return True
    if token.startswith("#E[") or token.startswith("#SP"):
        return True
    if token.startswith("$F[") or token.startswith("$B["):
        return True
    if NAME_SUFFIX_TOKEN_RE.fullmatch(token):
        return True
    return False


def _should_insert_space_before_run(text: str, run_start: int) -> bool:
    if run_start <= 0:
        return False
    char = text[run_start - 1]
    if not char or char.isspace():
        return False
    if char in "([{<\u3008\u300a\u3010\u300c\u300e\uff08":
        return False
    category = unicodedata.category(char)
    if category[0] in {"L", "N"}:
        return True
    return category in {"Pe", "Pf", "Po", "Sm", "Sc"}


def split_soft_color_tokens(tokens: Counter[str]) -> tuple[Counter[str], Counter[str]]:
    hard: Counter[str] = Counter()
    soft: Counter[str] = Counter()
    for token, count in tokens.items():
        if COLOR_TOKEN_RE.fullmatch(token):
            # `$C[115,5,20]` and `$C[115, 5, 20]` are identical game data.
            soft[re.sub(r"\s+", "", token)] = count
        elif token == "%":
            hard["%%"] += count
        elif NAME_SUFFIX_TOKEN_RE.fullmatch(token):
            hard[_canonical_name_suffix(token)] += count
        else:
            hard[token] += count
    return hard, soft


def format_counter_items(tokens: Counter[str]) -> str:
    return ", ".join(token for token, count in sorted(tokens.items()) for _ in range(count))


def _common_prefix_length(left: str, right: str) -> int:
    size = min(len(left), len(right))
    for index in range(size):
        if left[index] != right[index]:
            return index
    return size


def _bounded_edit_distance(left: str, right: str, max_distance: int) -> int | None:
    if abs(len(left) - len(right)) > max_distance:
        return None
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        row_minimum = left_index
        for right_index, right_char in enumerate(right, start=1):
            substitution = previous[right_index - 1] + (left_char != right_char)
            insertion = current[right_index - 1] + 1
            deletion = previous[right_index] + 1
            value = min(substitution, insertion, deletion)
            current.append(value)
            row_minimum = min(row_minimum, value)
        if row_minimum > max_distance:
            return None
        previous = current
    return previous[-1] if previous[-1] <= max_distance else None


def _canonical_name_suffix(token: str) -> str:
    suffix = token[2:]
    lowered = suffix.lower()
    for known in KNOWN_GENDER_SUFFIXES:
        known_lowered = known.lower()
        if lowered == known_lowered:
            return "@N" + known
        if len(lowered) >= 3 and (
            known_lowered.startswith(lowered)
            or lowered.startswith(known_lowered)
            or _bounded_edit_distance(lowered, known_lowered, 1) is not None
        ):
            return "@N" + known
    return "@N" + lowered.capitalize()


def _arg_parts(token: str) -> tuple[int, str] | None:
    match = re.fullmatch(r"%(\d+)(.*)", token)
    return (int(match.group(1)), match.group(2)) if match else None


def _arg_category(suffix: str) -> str:
    if suffix in {"SN", "SV"}:
        return "character-name"
    if suffix in {"Sn", "Sv"}:
        return "character-name-genitive"
    return suffix or "plain"


def _take_arg_tokens(tokens: Counter[str]) -> Counter[str]:
    args: Counter[str] = Counter()
    for token in [token for token in tokens if ARG_TOKEN_RE.fullmatch(token)]:
        args[token] = tokens.pop(token)
    return args


def _take_inline_fallbacks(tokens: Counter[str]) -> int:
    values = [token for token in tokens if token.startswith('@T"')]
    return sum(tokens.pop(token) for token in values)


def _drop_decoration_tokens(tokens: Counter[str]) -> None:
    for token in [token for token in tokens if token.startswith("$[")]:
        del tokens[token]


def _compare_argument_tokens(
    source: Counter[str], target: Counter[str], optional_source: Counter[str] | None = None
) -> list[ValidationIssue]:
    source_by_index: dict[int, set[str]] = {}
    optional_by_index: dict[int, set[str]] = {}
    target_by_index: dict[int, set[str]] = {}
    for token in source:
        number, suffix = _arg_parts(token) or (0, "")
        source_by_index.setdefault(number, set()).add(suffix)
    for token in optional_source or ():
        number, suffix = _arg_parts(token) or (0, "")
        optional_by_index.setdefault(number, set()).add(suffix)
    for token in target:
        number, suffix = _arg_parts(token) or (0, "")
        target_by_index.setdefault(number, set()).add(suffix)

    matching_source: dict[int, set[str]] = {number: set(suffixes) for number, suffixes in source_by_index.items()}
    for number, suffixes in optional_by_index.items():
        matching_source.setdefault(number, set()).update(suffixes)

    issues: list[ValidationIssue] = []
    for number, target_suffixes in sorted(target_by_index.items()):
        source_suffixes = matching_source.get(number)
        if not source_suffixes:
            tokens = Counter(token for token in target if (_arg_parts(token) or (0, ""))[0] == number)
            issues.append(
                ValidationIssue(
                    "warning",
                    translate("validation.argument_index", items=format_counter_items(tokens)),
                    code="argument-index",
                )
            )
            continue
        source_categories = {_arg_category(suffix) for suffix in source_suffixes}
        for suffix in sorted(target_suffixes):
            if _arg_category(suffix) not in source_categories:
                issues.append(
                    ValidationIssue(
                        "warning",
                        translate("validation.argument_type", token=f"%{number}{suffix}"),
                        code="argument-type",
                    )
                )
            elif suffix not in source_suffixes:
                issues.append(
                    ValidationIssue(
                        "warning",
                        translate("validation.argument_variant", token=f"%{number}{suffix}"),
                        code="argument-variant",
                    )
                )
    for number, source_suffixes in sorted(source_by_index.items()):
        if number not in target_by_index:
            issues.append(
                ValidationIssue(
                    "warning",
                    translate("validation.argument_omitted", token=f"%{number}{'/'.join(sorted(source_suffixes))}"),
                    code="argument-omitted",
                )
            )
    return issues


def _literal_percent_end(text: str, position: int) -> int | None:
    if position < 0 or position >= len(text) or text[position] != "%":
        return None
    next_char = text[position + 1] if position + 1 < len(text) else ""
    previous_char = text[position - 1] if position > 0 else ""
    if not next_char or next_char.isspace():
        return position + 1
    if previous_char.isdigit() and not (next_char.isascii() and (next_char.isalnum() or next_char in {"_", ":"})):
        return position + 1
    if next_char in '$.,:;!?)]}"\'”’':
        return position + 1
    return None


def unknown_syntax_tokens(text: str, *, dialect: str = FORMAT_GUILD2) -> list[str]:
    if dialect != FORMAT_GUILD2:
        return []
    unknown: list[str] = []
    position = 0
    while position < len(text):
        marker = text[position]
        if marker == "%":
            known = TOKEN_RE.match(text, position)
            if known is not None:
                position = known.end()
                continue
            literal = _literal_percent_end(text, position)
            if literal is not None:
                position = literal
                continue
            candidate = UNKNOWN_PERCENT_RE.match(text, position)
            if candidate is not None:
                unknown.append(candidate.group(0))
                position = candidate.end()
                continue
        elif marker in "#@":
            known = TOKEN_RE.match(text, position)
            if known is not None:
                position = known.end()
                continue
            if position + 1 < len(text) and text[position + 1] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz<>[":
                end = position + 1
                while end < len(text) and not text[end].isspace():
                    end += 1
                unknown.append(text[position:end])
                position = end
                continue
        position += 1
    return unknown


def guide_plain_double_quotes(text: str) -> int:
    if '"' not in text:
        return 0
    protected = [False] * len(text)
    for match in GUIDE_TOKEN_RE.finditer(text):
        for index in range(match.start(), match.end()):
            protected[index] = True
    return sum(1 for index, char in enumerate(text) if char == '"' and not protected[index])


def _arg_suffix_repair_candidates(raw_suffix: str) -> list[str]:
    normalized = raw_suffix.lower()
    if len(normalized) < 2:
        return []
    ranked: list[tuple[tuple[int, int, int], str]] = []
    for suffix in ARG_SUFFIXES:
        lowered = suffix.lower()
        distance = _bounded_edit_distance(normalized, lowered, 2)
        prefix = _common_prefix_length(normalized, lowered)
        if distance is None and prefix < 2:
            continue
        score = (
            distance if distance is not None else max(len(normalized), len(lowered)),
            abs(len(normalized) - len(lowered)),
            -prefix,
        )
        ranked.append((score, suffix))
    return [suffix for _, suffix in sorted(ranked)]


def _repair_candidates_from_unknown(token: str) -> list[str]:
    candidates: list[str] = []
    if re.fullmatch(r"%[A-Za-z][A-Za-z0-9_:-]*", token):
        candidates.append(token + "%")
    match = re.fullmatch(r"%(\d+)([A-Za-z0-9_:]+)", token)
    if match is not None:
        number, raw_suffix = match.groups()
        candidates.extend(f"%{number}{suffix}" for suffix in _arg_suffix_repair_candidates(raw_suffix))
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def _match_source_unknown_repairs(
    source_unknown: Counter[str],
    source_hard: Counter[str],
    source_args: Counter[str],
    target_hard: Counter[str],
    target_args: Counter[str],
) -> tuple[Counter[str], Counter[str]]:
    matched: Counter[str] = Counter()
    suspect: Counter[str] = Counter()
    available = (target_hard - source_hard) + (target_args - source_args)
    for token, count in sorted(source_unknown.items()):
        for _ in range(count):
            repaired = False
            for candidate in _repair_candidates_from_unknown(token):
                if available[candidate] <= matched[candidate]:
                    continue
                matched[candidate] += 1
                suspect[f"{token}→{candidate}"] += 1
                repaired = True
                break
            if not repaired:
                suspect[token] += 1
    return matched, suspect


def compare_tokens(
    source: str,
    target: str,
    *,
    source_unknown: Counter[str] | None = None,
    dialect: str = FORMAT_GUILD2,
) -> tuple[list[ValidationIssue], Counter[str]]:
    issues: list[ValidationIssue] = []
    source_tokens = format_tokens(source, dialect=dialect)
    target_tokens = format_tokens(target, dialect=dialect)
    if dialect != FORMAT_GUILD2:
        missing = source_tokens - target_tokens
        extra = target_tokens - source_tokens
        if missing:
            issues.append(
                ValidationIssue(
                    "warning",
                    translate("validation.format_missing", items=format_counter_items(missing)),
                    code="format-missing",
                )
            )
        if extra:
            issues.append(
                ValidationIssue(
                    "warning",
                    translate("validation.format_extra", items=format_counter_items(extra)),
                    code="format-extra",
                )
            )
        return issues, Counter()
    # $N is a cosmetic line-break directive. Translators may legitimately reflow
    # Chinese text, so it must not produce a format mismatch warning.
    source_tokens.pop("$N", None)
    target_tokens.pop("$N", None)
    source_args = _take_arg_tokens(source_tokens)
    target_args = _take_arg_tokens(target_tokens)
    source_fallbacks = _take_inline_fallbacks(source_tokens)
    target_fallbacks = _take_inline_fallbacks(target_tokens)
    _drop_decoration_tokens(source_tokens)
    _drop_decoration_tokens(target_tokens)
    source_hard, source_color = split_soft_color_tokens(source_tokens)
    target_hard, target_color = split_soft_color_tokens(target_tokens)
    repaired, source_suspect = _match_source_unknown_repairs(
        source_unknown or Counter(),
        source_hard,
        source_args,
        target_hard,
        target_args,
    )
    repaired_args = Counter({token: count for token, count in repaired.items() if ARG_TOKEN_RE.fullmatch(token)})
    repaired_hard = Counter({token: count for token, count in repaired.items() if not ARG_TOKEN_RE.fullmatch(token)})

    missing = source_hard - target_hard
    extra = (target_hard - source_hard) - repaired_hard
    missing_color = source_color - target_color
    extra_color = target_color - source_color

    issues.extend(_compare_argument_tokens(source_args, target_args, optional_source=repaired_args))

    if missing:
        items = format_counter_items(missing)
        issues.append(ValidationIssue("warning", translate("validation.format_missing", items=items), code="format-missing"))
    if extra:
        items = format_counter_items(extra)
        issues.append(ValidationIssue("warning", translate("validation.format_extra", items=items), code="format-extra"))
    if missing_color:
        items = format_counter_items(missing_color)
        issues.append(
            ValidationIssue("warning", translate("validation.format_color_missing", items=items), code="format-color-missing")
        )
    if extra_color:
        items = format_counter_items(extra_color)
        issues.append(
            ValidationIssue("warning", translate("validation.format_color_extra", items=items), code="format-color-extra")
        )
    if source_fallbacks != target_fallbacks:
        issues.append(ValidationIssue("warning", "@T inline fallback count differs", code="format-fallback"))
    return issues, source_suspect


def validate_translation(
    source: str,
    target: str,
    *,
    dbt_field: bool,
    font_codec: Guild2Codec | None = None,
    dialect: str = FORMAT_GUILD2,
) -> list[ValidationIssue]:
    source_unknown_raw = Counter(unknown_syntax_tokens(source, dialect=dialect))
    target_unknown_raw = Counter(unknown_syntax_tokens(target, dialect=dialect))
    source_unknown = source_unknown_raw - target_unknown_raw
    target_unknown = target_unknown_raw - source_unknown_raw
    issues, source_suspect = compare_tokens(
        source,
        target,
        source_unknown=source_unknown,
        dialect=dialect,
    )
    if dbt_field and '"' in target:
        issues.append(ValidationIssue("error", translate("validation.dbt_quote"), code="dbt-quote"))
    if dialect == FORMAT_GUIDE:
        quote_count = guide_plain_double_quotes(target)
        if quote_count:
            issues.append(
                ValidationIssue(
                    "warning",
                    translate("validation.guide_quote", count=quote_count),
                    code="guide-quote",
                )
            )
    if CHINESE_QUOTE_RE.search(target):
        bad = "".join(dict.fromkeys(CHINESE_QUOTE_RE.findall(target)))
        issues.append(ValidationIssue("warning", translate("validation.quote_style", text=bad), code="quote-style"))
    if source_suspect:
        issues.append(
            ValidationIssue(
                "warning",
                translate("validation.source_suspect", items=format_counter_items(source_suspect)),
                code="source-format-suspect",
            )
        )
    if target_unknown:
        issues.append(
            ValidationIssue(
                "warning",
                translate("validation.unknown_format", items=format_counter_items(target_unknown)),
                code="unknown-format",
            )
        )
    if font_codec is not None:
        missing = font_codec.unsupported_characters(target)
        if missing:
            chars = "".join(missing)
            points = ", ".join(f"U+{ord(char):04X}" for char in missing)
            issues.append(
                ValidationIssue(
                    "warning",
                    translate("validation.font_glyph", chars=chars, points=points),
                    code="font-glyph",
                )
            )
    return issues


def issue_summary(issues: list[ValidationIssue]) -> str:
    return "; ".join(issue.message for issue in issues)
