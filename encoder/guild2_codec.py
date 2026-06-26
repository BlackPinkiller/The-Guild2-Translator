from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Iterable


ENTITY_RE = re.compile(r"&#x([0-9a-fA-F]+);|&#([0-9]+);")
UPLUS_TOKEN_RE = re.compile(r"U\+([0-9a-fA-F]{4,6})")
SLASH_U_RE = re.compile(r"\\u([0-9a-fA-F]{4})|\\U([0-9a-fA-F]{8})")
PRIVATE_MIN = 0xA100
PRIVATE_MAX = 0xACFF


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def default_codec_path() -> Path:
    return script_dir() / "data" / "guild2_codec.json"


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_codec(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"codec table not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"invalid codec table: {path}")

    codec: dict[str, str] = {}
    for source, target in raw.items():
        if not isinstance(source, str) or not isinstance(target, str):
            raise ValueError("codec table must be a plain string dictionary")
        if len(source) != 1 or not target:
            raise ValueError(f"codec entries must map one source char to text: {source!r} -> {target!r}")
        codec[source] = target
    return codec


def is_private_char(char: str) -> bool:
    return len(char) == 1 and PRIVATE_MIN <= ord(char) <= PRIVATE_MAX


def requires_codec_mapping(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3000 <= codepoint <= 0x303F
        or 0x3400 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0xFF00 <= codepoint <= 0xFFEF
        or 0x20000 <= codepoint <= 0x3134F
    )


def normalize_game_input(text: str) -> str:
    text = html.unescape(text)

    def entity_replace(match: re.Match[str]) -> str:
        raw = match.group(1) or match.group(2)
        base = 16 if match.group(1) else 10
        return chr(int(raw, base))

    text = ENTITY_RE.sub(entity_replace, text)

    def slash_u_replace(match: re.Match[str]) -> str:
        raw = match.group(1) or match.group(2)
        return chr(int(raw, 16))

    text = SLASH_U_RE.sub(slash_u_replace, text)

    tokens = UPLUS_TOKEN_RE.findall(text)
    if tokens and re.fullmatch(r"(?:\s|,|;|\|)*" + r"(?:U\+[0-9a-fA-F]{4,6}(?:\s|,|;|\|)*)+", text):
        return "".join(chr(int(token, 16)) for token in tokens)

    return UPLUS_TOKEN_RE.sub(lambda match: chr(int(match.group(1), 16)), text)


def apply_missing(char: str, mode: str, replacement: str, direction: str) -> str:
    if mode == "keep":
        return char
    if mode == "drop":
        return ""
    if mode == "replace":
        return replacement
    if mode == "error":
        raise ValueError(f"cannot {direction} character {char}")
    raise ValueError(f"unknown missing mode: {mode}")


def encode_replacement(replacement: str, codec: dict[str, str]) -> str:
    return "".join(codec.get(char, char) if not is_private_char(char) else char for char in replacement)


def encode_text(text: str, codec: dict[str, str], missing: str, replacement: str) -> tuple[str, list[str]]:
    encoded_replacement = encode_replacement(replacement, codec)
    out: list[str] = []
    missing_chars: list[str] = []
    for char in text:
        target = codec.get(char) if not is_private_char(char) else None
        if target is not None:
            out.append(target)
            continue
        if not requires_codec_mapping(char):
            out.append(char)
            continue
        if char not in missing_chars:
            missing_chars.append(char)
        out.append(apply_missing(char, missing, encoded_replacement, "encode"))
    return "".join(out), missing_chars


def decode_text(text: str, codec: dict[str, str], missing: str, replacement: str) -> tuple[str, list[str]]:
    text = normalize_game_input(text)
    out: list[str] = []
    missing_chars: list[str] = []
    for char in text:
        target = codec.get(char) if is_private_char(char) else None
        if target is not None:
            out.append(target)
            continue
        if not is_private_char(char):
            out.append(char)
            continue
        if char not in missing_chars:
            missing_chars.append(char)
        out.append(apply_missing(char, missing, replacement, "decode"))
    return "".join(out), missing_chars


def format_output(text: str, output_format: str, missing_chars: list[str]) -> str:
    if output_format == "raw":
        return text
    if output_format == "entity":
        return "".join(f"&#x{ord(char):X};" if ord(char) > 127 else char for char in text)
    if output_format == "decimal-entity":
        return "".join(f"&#{ord(char)};" if ord(char) > 127 else char for char in text)
    if output_format == "uplus":
        return " ".join(f"U+{ord(char):04X}" for char in text)
    if output_format == "json":
        return json.dumps(
            {
                "text": text,
                "codepoints": [f"U+{ord(char):04X}" for char in text],
                "missing": [f"U+{ord(char):04X}" for char in missing_chars],
            },
            ensure_ascii=False,
            indent=2,
        )
    raise ValueError(f"unknown output format: {output_format}")


def read_input(args: argparse.Namespace) -> str:
    if args.file:
        return Path(args.file).read_text(encoding=args.input_encoding)
    if args.text:
        return " ".join(args.text)
    return sys.stdin.read()


def write_output(args: argparse.Namespace, text: str) -> None:
    if args.output:
        Path(args.output).write_text(text, encoding=args.output_encoding)
        return
    print(text, end="" if text.endswith("\n") else "\n")


def lookup_text(text: str, codec: dict[str, str]) -> str:
    rows: list[dict[str, str]] = []
    for char in normalize_game_input(text):
        row = {"char": char, "uplus": f"U+{ord(char):04X}"}
        if char in codec and not is_private_char(char):
            game = codec[char]
            row["game"] = game
            row["game_uplus"] = f"U+{ord(game):04X}" if len(game) == 1 else ""
        if char in codec and is_private_char(char):
            plain = codec[char]
            row["plain"] = plain
            row["plain_uplus"] = " ".join(f"U+{ord(item):04X}" for item in plain)
        rows.append(row)
    return json.dumps(rows, ensure_ascii=False, indent=2)


def stats_text(codec: dict[str, str]) -> str:
    return json.dumps(
        {
            "entries": len(codec),
            "encode_private": sum(1 for source, target in codec.items() if not is_private_char(source) and len(target) == 1 and is_private_char(target)),
            "decode_private": sum(1 for source in codec if is_private_char(source)),
            "substitution": sum(1 for source, target in codec.items() if not is_private_char(source) and not (len(target) == 1 and is_private_char(target))),
        },
        ensure_ascii=False,
        indent=2,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Global codec for The Guild 2 Renaissance font encoding.")
    parser.add_argument("--codec", type=Path, default=default_codec_path(), help="Path to guild2_codec.json.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_convert_args(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("text", nargs="*", help="Text to convert. Reads stdin when omitted.")
        sub.add_argument("--file", help="Read input from a file.")
        sub.add_argument("--output", "-o", help="Write output to a file.")
        sub.add_argument("--input-encoding", default="utf-8-sig")
        sub.add_argument("--output-encoding", default="utf-8")
        sub.add_argument("--format", choices=["raw", "entity", "decimal-entity", "uplus", "json"], default="raw")
        sub.add_argument("--missing", choices=["replace", "keep", "drop", "error"], default="error")
        sub.add_argument("--replacement", default="口", help="Replacement character for missing mode 'replace'.")

    add_convert_args(subparsers.add_parser("encode", help="Convert normal text to game font encoding."))
    add_convert_args(subparsers.add_parser("decode", help="Convert game font encoding back to normal text."))

    lookup = subparsers.add_parser("lookup", help="Show mapping details for characters.")
    lookup.add_argument("text", nargs="*", help="Characters, U+ tokens, or entities to inspect.")
    lookup.add_argument("--file", help="Read input from a file.")
    lookup.add_argument("--input-encoding", default="utf-8-sig")

    subparsers.add_parser("stats", help="Print codec table stats.")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    codec = load_codec(args.codec)

    if args.command == "stats":
        print(stats_text(codec))
        return 0

    if args.command == "lookup":
        print(lookup_text(read_input(args), codec))
        return 0

    text = read_input(args)
    if args.command == "encode":
        converted, missing_chars = encode_text(text, codec, args.missing, args.replacement)
    elif args.command == "decode":
        converted, missing_chars = decode_text(text, codec, args.missing, args.replacement)
    else:
        parser.error(f"unknown command: {args.command}")
        return 2
    write_output(args, format_output(converted, args.format, missing_chars))
    return 0


def cli() -> int:
    try:
        return main()
    except KeyboardInterrupt:
        print("error: cancelled", file=sys.stderr)
        return 130
    except (FileNotFoundError, PermissionError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
