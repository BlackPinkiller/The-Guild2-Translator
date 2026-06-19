from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re


# Translation-Kit placeholders:
#   %1, %1n, %1i, %1t, %1s, %1l, %1SN, %1SV, %1GG, %1DN, %1NAME, %1SA, %1ST, %1SK
# Real files also contain legacy suffixes such as %1SZ, %1DS, %1GT, %2c, %3j and old %s/%d.
ARG_SUFFIX = r"(?:NAME|SN|SV|GG|DN|SA|ST|SK|SZ|DS|GT|[nitslcj])?"
ARG_TOKEN = rf"%\d+{ARG_SUFFIX}"
PRINTF_TOKEN = r"%(?:\d+\$)?[-+#0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[diufFeEgGxXos]"
NAMED_PERCENT_TOKEN = r"%[A-Za-z_][A-Za-z0-9_]*(?::[A-Za-z0-9_]+)?%"
PERCENT_TOKEN = rf"%%|{ARG_TOKEN}|{NAMED_PERCENT_TOKEN}|{PRINTF_TOKEN}"

COLOR_TOKEN = r"\$C(?:\[(?:\d{1,3},){2,3}\d{1,3}\])?"
COLOR_TOKEN_RE = re.compile(rf"^{COLOR_TOKEN}$")
SYMBOL_TOKEN = r"\$S\[\d{1,4}\]"
LINE_OR_LAYOUT_TOKEN = r"\$[NLRZ]"
EMOTION_TOKEN = r"#E\[[A-Za-z0-9_]+\]"

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
            SYMBOL_TOKEN,
            LINE_OR_LAYOUT_TOKEN,
            EMOTION_TOKEN,
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
            SYMBOL_TOKEN,
            LINE_OR_LAYOUT_TOKEN,
            EMOTION_TOKEN,
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
BARE_PERCENT_RE = re.compile(
    r"(?<!%)%(?!%|\d|[-+#0]*(?:\d+|\*)?(?:\.(?:\d+|\*))?[diufFeEgGxXos]\b|[A-Za-z_][A-Za-z0-9_]*(?::[A-Za-z0-9_]+)?%)"
)


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    message: str

    @property
    def blocks_save(self) -> bool:
        return self.severity == "error"


def format_tokens(text: str) -> Counter[str]:
    return Counter(match.group(0) for match in TOKEN_RE.finditer(text))


def split_soft_color_tokens(tokens: Counter[str]) -> tuple[Counter[str], Counter[str]]:
    hard: Counter[str] = Counter()
    soft: Counter[str] = Counter()
    for token, count in tokens.items():
        target = soft if COLOR_TOKEN_RE.fullmatch(token) else hard
        target[token] = count
    return hard, soft


def format_counter_items(tokens: Counter[str]) -> str:
    return ", ".join(token for token, count in sorted(tokens.items()) for _ in range(count))


def compare_tokens(source: str, target: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    source_tokens = format_tokens(source)
    target_tokens = format_tokens(target)
    source_hard, source_color = split_soft_color_tokens(source_tokens)
    target_hard, target_color = split_soft_color_tokens(target_tokens)

    missing = source_hard - target_hard
    extra = target_hard - source_hard
    missing_color = source_color - target_color
    extra_color = target_color - source_color

    if missing:
        items = format_counter_items(missing)
        issues.append(ValidationIssue("error", f"缺少格式标记: {items}"))
    if extra:
        items = format_counter_items(extra)
        issues.append(ValidationIssue("warning", f"新增格式标记: {items}"))
    if missing_color:
        items = format_counter_items(missing_color)
        issues.append(ValidationIssue("warning", f"颜色标记不一致(不阻止保存): 缺少 {items}"))
    if extra_color:
        items = format_counter_items(extra_color)
        issues.append(ValidationIssue("warning", f"颜色标记不一致(不阻止保存): 新增 {items}"))
    return issues


def validate_translation(source: str, target: str, *, dbt_field: bool) -> list[ValidationIssue]:
    issues = compare_tokens(source, target)
    if dbt_field and '"' in target:
        issues.append(ValidationIssue("error", 'DBT 字段不能包含双引号 "，请使用 >Text<。'))
    if FULLWIDTH_SYNTAX_RE.search(target):
        bad = "".join(dict.fromkeys(FULLWIDTH_SYNTAX_RE.findall(target)))
        issues.append(ValidationIssue("error", f"疑似全角格式符号: {bad}"))
    if CHINESE_QUOTE_RE.search(target):
        bad = "".join(dict.fromkeys(CHINESE_QUOTE_RE.findall(target)))
        issues.append(ValidationIssue("warning", f"中文引号可能不符合 Translation-Kit: {bad}"))
    if BARE_PERCENT_RE.search(target):
        issues.append(ValidationIssue("warning", "发现单个 %；若要显示百分号，Translation-Kit 建议使用 %%"))
    return issues


def issue_summary(issues: list[ValidationIssue]) -> str:
    return "; ".join(issue.message for issue in issues)
