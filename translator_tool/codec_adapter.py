from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
from types import ModuleType


class CodecError(ValueError):
    """Raised when text cannot be converted to or from the game font map."""


def is_unsupported_emoji(char: str) -> bool:
    """The game can pass through normal Unicode text, but not emoji glyphs."""
    codepoint = ord(char)
    return 0x1F000 <= codepoint <= 0x1FAFF


class Guild2Codec:
    def __init__(self, codec: dict[str, str], codec_module: ModuleType) -> None:
        self.codec = codec
        self.codec_module = codec_module

    @classmethod
    def load(cls, codec_path: Path) -> "Guild2Codec":
        module = _load_encoder_module(codec_path)
        try:
            codec = module.load_codec(codec_path)
        except Exception as exc:
            raise CodecError(str(exc)) from exc
        return cls(codec, module)

    def decode(self, text: str) -> str:
        try:
            converted, _missing = self.codec_module.decode_text(text, self.codec, "error", "")
        except Exception as exc:
            raise CodecError(str(exc)) from exc
        return converted

    def encode(self, text: str) -> str:
        missing = self.unsupported_characters(text)
        if missing:
            chars = "".join(missing)
            points = ", ".join(f"{char}(U+{ord(char):04X})" for char in missing)
            raise CodecError(f"cannot encode character(s): {chars} / {points}")
        try:
            converted, _missing = self.codec_module.encode_text(text, self.codec, "error", "")
        except Exception as exc:
            raise CodecError(str(exc)) from exc
        return converted

    def unsupported_characters(self, text: str) -> list[str]:
        """Return distinct characters the game font cannot encode/display."""
        missing: list[str] = []
        for char in text:
            if char in self.codec:
                continue
            if self.codec_module.is_private_char(char):
                continue
            if not is_unsupported_emoji(char) and not self.codec_module.requires_codec_mapping(char):
                continue
            if char not in missing:
                missing.append(char)
        return missing


@lru_cache(maxsize=4)
def _load_encoder_module(codec_path: Path) -> ModuleType:
    script = codec_path.resolve().parents[1] / "guild2_codec.py"
    if not script.exists():
        raise CodecError(f"codec module not found: {script}")
    spec = importlib.util.spec_from_file_location("guild2_codec_runtime", script)
    if spec is None or spec.loader is None:
        raise CodecError(f"cannot load codec module: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def default_codec_path(project_root: Path, codec_root: Path | None = None) -> Path:
    """Return the bundled codec path, optionally separate from a language project."""
    root = codec_root if codec_root is not None else project_root
    return root / "encoder" / "data" / "guild2_codec.json"
