from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

from .codec_adapter import Guild2Codec


ARG_SUFFIX = r"(?:NAME|GG|GN|GT|SN|Sn|SV|Sv|SZ|Sz|SK|ST|SA|SD|SB|SL|DN|DS|[niftczjsl])?"
# English source text commonly pluralizes a dynasty name as `%1DNs`.
# Treat that as `%1DN` followed by literal text, never as a new placeholder.
ARG_TOKEN = rf"%\d+(?:(?:NAME|DN)(?=s(?![A-Za-z0-9]))|{ARG_SUFFIX}(?![A-Za-z0-9]))"
PRINTF_TOKEN = r"%(?:\d+\$)?[-+#0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[diufFeEgGxXos](?![A-Za-z0-9])"
PERCENT_TOKEN = rf"%%|%[<>]|{ARG_TOKEN}|{PRINTF_TOKEN}"

BYTE_TOKEN = r"(?:0|[1-9]\d?|1\d\d|2[0-4]\d|25[0-5])"
COLOR_TOKEN = rf"\$C\s*\[\s*{BYTE_TOKEN}(?:\s*,\s*{BYTE_TOKEN}){{2,3}}\s*\]"
COLOR_TOKEN_RE = re.compile(rf"^{COLOR_TOKEN}$")
FONT_TOKEN = r"\$F\[[^\]\r\n]*\]"
SYMBOL_TOKEN = r"\$S\[[^\]\r\n]*\]"
BACKGROUND_TOKEN = r"\$B\[[^\]\r\n]*\]"
HEADER_TOKEN = r"\$\[[^\r\n]*?\$\]"
LINE_OR_LAYOUT_TOKEN = r"\$[NLRZT<>]"
EMOTION_TOKEN = r"#E\[[A-Za-z0-9_]+\]"
SPEECH_TIMING_TOKEN = r"#SP[+-]"
LOCALIZATION_TOKEN = r"@L_[A-Za-z0-9_]+_\+[A-Za-z0-9]+"
INLINE_FALLBACK_TOKEN = r'@T"(?:[^"\\\r\n]|\\.)*"'

GUIDE_TAG_TOKEN = r"</?(?:header|text|separator|list|item|table|row|cell)\b[^>\r\n]*>"
GUIDE_INLINE_TOKEN = r"\{tip:[A-Za-z0-9_]+\}|\{/tip\}"
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

TOKEN_RE = re.compile(
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
            LOCALIZATION_TOKEN,
            INLINE_FALLBACK_TOKEN,
            GUIDE_TAG_TOKEN,
            GUIDE_INLINE_TOKEN,
            GUIDE_ATTR_TOKEN,
            GUIDE_TOC_TOKEN,
        ]
    )
)
HIGHLIGHT_RE = re.compile(
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
            LOCALIZATION_TOKEN,
            INLINE_FALLBACK_TOKEN,
            GUIDE_TAG_TOKEN,
            GUIDE_INLINE_TOKEN,
            GUIDE_ATTR_TOKEN,
            GUIDE_TOC_TOKEN,
            QUOTE_STYLE_TOKEN,
        ]
    )
)

FULLWIDTH_SYNTAX_RE = re.compile(r"[\uFF05\uFF04\uFF03\uFF3B\uFF3D\uFF5C\uFF10-\uFF19\uFF21-\uFF3A\uFF41-\uFF5A]")
CHINESE_QUOTE_RE = re.compile(r"[\u201C\u201D\u2018\u2019]")
ARG_TOKEN_RE = re.compile(ARG_TOKEN)
UNKNOWN_PERCENT_RE = re.compile(r"%(?:\d*[A-Za-z][A-Za-z0-9]*|[^\s])?")


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    message: str
    code: str = ""

    @property
    def blocks_save(self) -> bool:
        return self.severity == "error"


def format_tokens(text: str) -> Counter[str]:
    return Counter(match.group(0) for match in TOKEN_RE.finditer(text))


def split_soft_color_tokens(tokens: Counter[str]) -> tuple[Counter[str], Counter[str]]:
    hard: Counter[str] = Counter()
    soft: Counter[str] = Counter()
    for token, count in tokens.items():
        if COLOR_TOKEN_RE.fullmatch(token):
            # `$C[115,5,20]` and `$C[115, 5, 20]` are identical game data.
            soft[re.sub(r"\s+", "", token)] = count
        else:
            hard[token] = count
    return hard, soft


def format_counter_items(tokens: Counter[str]) -> str:
    return ", ".join(token for token, count in sorted(tokens.items()) for _ in range(count))


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


def _compare_argument_tokens(source: Counter[str], target: Counter[str]) -> list[ValidationIssue]:
    source_by_index: dict[int, set[str]] = {}
    target_by_index: dict[int, set[str]] = {}
    for token in source:
        number, suffix = _arg_parts(token) or (0, "")
        source_by_index.setdefault(number, set()).add(suffix)
    for token in target:
        number, suffix = _arg_parts(token) or (0, "")
        target_by_index.setdefault(number, set()).add(suffix)

    issues: list[ValidationIssue] = []
    for number, target_suffixes in sorted(target_by_index.items()):
        source_suffixes = source_by_index.get(number)
        if not source_suffixes:
            tokens = Counter(token for token in target if (_arg_parts(token) or (0, ""))[0] == number)
            issues.append(ValidationIssue("warning", f"参数编号不存在: {format_counter_items(tokens)}", code="argument-index"))
            continue
        source_categories = {_arg_category(suffix) for suffix in source_suffixes}
        for suffix in sorted(target_suffixes):
            if _arg_category(suffix) not in source_categories:
                issues.append(ValidationIssue("warning", f"参数类型不匹配: %{number}{suffix}", code="argument-type"))
            elif suffix not in source_suffixes:
                issues.append(ValidationIssue("warning", f"参数类型替换: %{number}{suffix}", code="argument-variant"))
    for number, source_suffixes in sorted(source_by_index.items()):
        if number not in target_by_index:
            issues.append(ValidationIssue("warning", f"未使用原文参数: %{number}{'/'.join(sorted(source_suffixes))}", code="argument-omitted"))
    return issues


def unknown_syntax_tokens(text: str) -> list[str]:
    unknown: list[str] = []
    position = 0
    while position < len(text):
        marker = text[position]
        if marker == "%":
            known = TOKEN_RE.match(text, position)
            if known is not None:
                position = known.end()
                continue
            candidate = UNKNOWN_PERCENT_RE.match(text, position)
            if candidate is not None:
                unknown.append(candidate.group(0))
                position = candidate.end()
                continue
        elif marker in "$#@":
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


def compare_tokens(source: str, target: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    source_tokens = format_tokens(source)
    target_tokens = format_tokens(target)
    # $N is a cosmetic line-break directive. Translators may legitimately reflow
    # Chinese text, so it must not produce a save-blocking format difference.
    source_tokens.pop("$N", None)
    target_tokens.pop("$N", None)
    source_args = _take_arg_tokens(source_tokens)
    target_args = _take_arg_tokens(target_tokens)
    source_fallbacks = _take_inline_fallbacks(source_tokens)
    target_fallbacks = _take_inline_fallbacks(target_tokens)
    source_hard, source_color = split_soft_color_tokens(source_tokens)
    target_hard, target_color = split_soft_color_tokens(target_tokens)

    missing = source_hard - target_hard
    extra = target_hard - source_hard
    missing_color = source_color - target_color
    extra_color = target_color - source_color

    issues.extend(_compare_argument_tokens(source_args, target_args))

    if missing:
        items = format_counter_items(missing)
        issues.append(ValidationIssue("warning", f"缺少格式标记: {items}"))
    if extra:
        items = format_counter_items(extra)
        issues.append(ValidationIssue("warning", f"新增格式标记: {items}"))
    if missing_color:
        items = format_counter_items(missing_color)
        issues.append(ValidationIssue("warning", f"颜色标记不一致(不阻止保存): 缺少 {items}"))
    if extra_color:
        items = format_counter_items(extra_color)
        issues.append(ValidationIssue("warning", f"颜色标记不一致(不阻止保存): 新增 {items}"))
    if source_fallbacks != target_fallbacks:
        issues.append(ValidationIssue("warning", "@T inline fallback count differs", code="format-fallback"))
    return issues


def validate_translation(
    source: str,
    target: str,
    *,
    dbt_field: bool,
    font_codec: Guild2Codec | None = None,
) -> list[ValidationIssue]:
    issues = compare_tokens(source, target)
    if dbt_field and '"' in target:
        issues.append(ValidationIssue("error", 'DBT 字段不能包含双引号 "，请使用 >Text<。'))
    if FULLWIDTH_SYNTAX_RE.search(target):
        bad = "".join(dict.fromkeys(FULLWIDTH_SYNTAX_RE.findall(target)))
        issues.append(ValidationIssue("warning", f"疑似全角格式符号: {bad}"))
    if CHINESE_QUOTE_RE.search(target):
        bad = "".join(dict.fromkeys(CHINESE_QUOTE_RE.findall(target)))
        issues.append(ValidationIssue("warning", f"中文引号可能不符合 Translation-Kit: {bad}"))
    source_unknown = unknown_syntax_tokens(source)
    target_unknown = unknown_syntax_tokens(target)
    if source_unknown:
        issues.append(ValidationIssue("warning", f"原文包含未知格式: {', '.join(source_unknown)}", code="unknown-format"))
    if target_unknown:
        issues.append(ValidationIssue("warning", f"译文包含未知格式: {', '.join(target_unknown)}", code="unknown-format"))
    if font_codec is not None:
        missing = font_codec.unsupported_characters(target)
        if missing:
            chars = "".join(missing)
            points = ", ".join(f"U+{ord(char):04X}" for char in missing)
            issues.append(ValidationIssue("warning", f"字库缺字: {chars} ({points})", code="font-glyph"))
    return issues


def issue_summary(issues: list[ValidationIssue]) -> str:
    return "; ".join(issue.message for issue in issues)
