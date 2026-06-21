from __future__ import annotations

import html
import json
import re
from pathlib import Path


PRIVATE_MIN = 0xA100
PRIVATE_MAX = 0xACFF
ENTITY_RE = re.compile(r"&#x([0-9a-fA-F]+);|&#([0-9]+);")
SLASH_U_RE = re.compile(r"\\u([0-9a-fA-F]{4})|\\U([0-9a-fA-F]{8})")
UPLUS_RE = re.compile(r"U\+([0-9a-fA-F]{4,6})")


class CodecError(ValueError):
    """Raised when text cannot be converted to or from the game font map."""


def is_private(char: str) -> bool:
    return len(char) == 1 and PRIVATE_MIN <= ord(char) <= PRIVATE_MAX


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
    tokens = UPLUS_RE.findall(text)
    if tokens and re.fullmatch(r"(?:\s|,|;|\|)*" + r"(?:U\+[0-9a-fA-F]{4,6}(?:\s|,|;|\|)*)+", text):
        return "".join(chr(int(token, 16)) for token in tokens)
    return UPLUS_RE.sub(lambda match: chr(int(match.group(1), 16)), text)


class Guild2Codec:
    def __init__(self, plain_to_game: dict[str, str], game_to_plain: dict[str, str]) -> None:
        self.plain_to_game = plain_to_game
        self.game_to_plain = game_to_plain

    @classmethod
    def load(cls, codec_path: Path) -> "Guild2Codec":
        raw = json.loads(codec_path.read_text(encoding="utf-8"))
        plain_to_game = raw.get("plain_to_game")
        game_to_plain = raw.get("game_to_plain")
        if not isinstance(plain_to_game, dict) or not isinstance(game_to_plain, dict):
            raise CodecError(f"invalid codec table: {codec_path}")
        return cls(
            {str(key): str(value) for key, value in plain_to_game.items()},
            {str(key): str(value) for key, value in game_to_plain.items()},
        )

    def decode(self, text: str) -> str:
        text = normalize_game_input(text)
        out: list[str] = []
        missing: list[str] = []
        for char in text:
            if is_private(char):
                target = self.game_to_plain.get(char)
                if target:
                    out.append(target)
                else:
                    out.append(char)
                    if char not in missing:
                        missing.append(char)
            else:
                out.append(char)
        if missing:
            points = ", ".join(f"U+{ord(char):04X}" for char in missing)
            raise CodecError(f"cannot decode game character(s): {points}")
        return "".join(out)

    def encode(self, text: str) -> str:
        out: list[str] = []
        missing: list[str] = []
        for char in text:
            target = self.plain_to_game.get(char)
            if isinstance(target, str) and len(target) == 1:
                out.append(target)
                continue
            if ord(char) < 128:
                out.append(char)
                continue
            if is_private(char):
                out.append(char)
                continue
            out.append(char)
            if char not in missing:
                missing.append(char)
        if missing:
            chars = "".join(missing)
            points = ", ".join(f"{char}(U+{ord(char):04X})" for char in missing)
            raise CodecError(f"cannot encode character(s): {chars} / {points}")
        return "".join(out)


def default_codec_path(project_root: Path, codec_root: Path | None = None) -> Path:
    """Return the bundled codec path, optionally separate from a language project."""
    root = codec_root if codec_root is not None else project_root
    return root / "encoder" / "data" / "guild2_chinese_codec.json"
