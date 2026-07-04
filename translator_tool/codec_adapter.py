from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path
from types import ModuleType


class CodecError(ValueError):
    """Raised when text cannot be converted to or from the game font map."""


CODEC_LANGUAGE_NAMES = frozenset(
    {
        "chinese",
        "schinese",
        "tchinese",
        "zh",
        "zh-cn",
        "zh_cn",
        "zh-hans",
        "zh_hans",
        "zh-tw",
        "zh_tw",
        "zh-hant",
        "zh_hant",
    }
)


def language_uses_codec(language: str) -> bool:
    """Only Chinese translation folders should route through the Guild 2 codec."""
    normalized = language.lstrip("#").strip().casefold()
    return normalized in CODEC_LANGUAGE_NAMES


def load_codec_for_language(root: Path, language: str) -> Guild2Codec | None:
    """Load the runtime codec only when Chinese text has a complete codec bundle."""
    if not language_uses_codec(language):
        return None
    encoder_dir = _find_encoder_dir(root)
    if encoder_dir is None:
        return None
    required = (
        encoder_dir / "guild2_codec.py",
        encoder_dir / "data" / "guild2_write_codec.json",
        encoder_dir / "data" / "guild2_read_codec.json",
    )
    if any(not path.is_file() for path in required):
        return None
    return Guild2Codec.load(root)


def is_unsupported_emoji(char: str) -> bool:
    """The game can pass through normal Unicode text, but not emoji glyphs."""
    codepoint = ord(char)
    return 0x1F000 <= codepoint <= 0x1FAFF


def is_private_use(char: str) -> bool:
    """Allow already-encoded private-use glyphs to pass through unchanged."""
    codepoint = ord(char)
    return (
        0xE000 <= codepoint <= 0xF8FF
        or 0xF0000 <= codepoint <= 0xFFFFD
        or 0x100000 <= codepoint <= 0x10FFFD
    )


class Guild2Codec:
    def __init__(
        self,
        write_codec: dict[str, str],
        read_codec: dict[str, str],
        codec_module: ModuleType,
    ) -> None:
        self.write_codec = write_codec
        self.read_codec = read_codec
        self.codec_module = codec_module

    @classmethod
    def load(cls, root: Path) -> "Guild2Codec":
        module = _load_encoder_module(root)
        try:
            write_codec = module.load_write_codec(module.default_write_codec_path())
            read_codec = module.load_read_codec(module.default_read_codec_path())
        except Exception as exc:
            raise CodecError(str(exc)) from exc
        return cls(write_codec, read_codec, module)

    def decode(self, text: str) -> str:
        try:
            converted, _missing = self.codec_module.decode_text(text, self.read_codec)
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
            converted, _missing = self.codec_module.encode_text(text, self.write_codec, "error", "")
        except Exception as exc:
            raise CodecError(str(exc)) from exc
        return converted

    def unsupported_characters(self, text: str) -> list[str]:
        """Return distinct characters the game font cannot encode/display."""
        missing: list[str] = []
        for char in text:
            if char in self.write_codec:
                continue
            if is_private_use(char):
                continue
            if not is_unsupported_emoji(char) and not self.codec_module.requires_codec_mapping(char):
                continue
            if char not in missing:
                missing.append(char)
        return missing


@lru_cache(maxsize=4)
def _load_encoder_module(root: Path) -> ModuleType:
    script = _resolve_encoder_dir(root) / "guild2_codec.py"
    if not script.exists():
        raise CodecError(f"codec module not found: {script}")
    spec = importlib.util.spec_from_file_location("guild2_codec_runtime", script)
    if spec is None or spec.loader is None:
        raise CodecError(f"cannot load codec module: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _find_encoder_dir(root: Path) -> Path | None:
    location = root.resolve()
    if location.is_file():
        location = location.parent
    search_roots = [location]
    # In PyInstaller onedir builds, sys._MEIPASS points at `_internal`, while
    # optional external assets are most naturally placed next to the EXE.
    if location.name.casefold() == "_internal":
        search_roots.append(location.parent)
    for candidate_root in search_roots:
        if (candidate_root / "guild2_codec.py").is_file():
            return candidate_root
        encoder_dir = candidate_root / "encoder"
        if (encoder_dir / "guild2_codec.py").is_file():
            return encoder_dir
    return None


def _resolve_encoder_dir(root: Path) -> Path:
    encoder_dir = _find_encoder_dir(root)
    if encoder_dir is not None:
        return encoder_dir
    location = root.resolve()
    if location.is_file():
        location = location.parent
    raise CodecError(f"encoder directory not found under: {location}")
