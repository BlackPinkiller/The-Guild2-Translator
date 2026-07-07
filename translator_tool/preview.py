from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import html
from pathlib import Path
import re
import struct
import unicodedata

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QImage, QPainter, qRgba

from .format_io import load_dbt, translatable_fields
from .i18n import translate
from .validation import (
    COLOR_TOKEN_RE,
    FORMAT_GUIDE,
    FORMAT_GUILD2,
    FORMAT_TOOLTIP,
    GUIDE_TOKEN_RE,
    GUILD2_TOKEN_RE,
    PRINTF_TOKEN,
    QUOTE_STYLE_TOKEN,
    TOOLTIP_TOKEN_RE,
    format_dialect,
)


PREVIEW_MARK = "\u200b"
GLYPH_MARK = "\ufffc"
FONT_RECORD_RE = re.compile(
    r"(?:^|/)fonts/(?P<font>.+)_(?P<start>\d+)-(?P<end>\d+)\.tga(?P<index>\d+)$",
    re.IGNORECASE,
)
ARG_PREVIEW_RE = re.compile(r"%(\d+)([A-Za-z]*)")
PRINTF_PREVIEW_RE = re.compile(PRINTF_TOKEN)
SYMBOL_PREVIEW_RE = re.compile(r"\$S\[\s*(\d+)\s*\]")
COLOR_VALUE_RE = re.compile(r"\d+")
GUIDE_VALUE_RE = re.compile(r"\[([rgb])=(\d{1,3})\]")


@dataclass(frozen=True)
class PreviewAtom:
    text: str
    raw_start: int
    raw_end: int
    replacement: bool = False
    glyph_id: int | None = None
    color: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class PreviewSpan:
    display_start: int
    display_end: int
    atom: PreviewAtom


@dataclass(frozen=True)
class PreviewDocument:
    raw_text: str
    atoms: tuple[PreviewAtom, ...]
    display_text: str
    spans: tuple[PreviewSpan, ...]
    display_to_raw: tuple[int, ...]

    @classmethod
    def from_atoms(cls, raw_text: str, atoms: list[PreviewAtom]) -> "PreviewDocument":
        display_parts: list[str] = []
        spans: list[PreviewSpan] = []
        boundaries = [0]
        display_position = 0
        for atom in atoms:
            text = atom.text or PREVIEW_MARK
            normalized = atom if atom.text else PreviewAtom(
                text,
                atom.raw_start,
                atom.raw_end,
                atom.replacement,
                atom.glyph_id,
                atom.color,
            )
            display_parts.append(text)
            display_end = display_position + len(text)
            spans.append(PreviewSpan(display_position, display_end, normalized))
            raw_length = max(atom.raw_end - atom.raw_start, 0)
            for offset in range(1, len(text) + 1):
                boundaries.append(atom.raw_start + round(raw_length * offset / len(text)))
            display_position = display_end
        if not atoms:
            boundaries = [0]
        return cls(raw_text, tuple(atoms), "".join(display_parts), tuple(spans), tuple(boundaries))

    def raw_position(self, display_position: int) -> int:
        if not self.display_to_raw:
            return 0
        index = max(0, min(display_position, len(self.display_to_raw) - 1))
        return self.display_to_raw[index]

    def display_position(self, raw_position: int) -> int:
        position = max(0, min(raw_position, len(self.raw_text)))
        for span in self.spans:
            atom = span.atom
            if atom.raw_start <= position <= atom.raw_end:
                raw_length = atom.raw_end - atom.raw_start
                if raw_length <= 0:
                    return span.display_start
                ratio = (position - atom.raw_start) / raw_length
                return span.display_start + round((span.display_end - span.display_start) * ratio)
        return len(self.display_text)

    def display_range(self, raw_start: int, raw_end: int) -> tuple[int, int]:
        return self.display_position(raw_start), self.display_position(raw_end)


@dataclass(frozen=True)
class GlyphRecord:
    texture_path: Path
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class UiAssetRecord:
    name: str
    texture_path: Path
    x: int
    y: int
    width: int
    height: int


class Dxt3Texture:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = path.read_bytes()
        if len(self.data) < 128 or self.data[:4] != b"DDS " or self.data[84:88] != b"DXT3":
            raise ValueError(f"unsupported Guild 2 font texture: {path}")
        self.height, self.width = struct.unpack_from("<II", self.data, 12)
        self.blocks_wide = (self.width + 3) // 4

    @staticmethod
    def _rgb565(value: int) -> tuple[int, int, int]:
        red = ((value >> 11) & 0x1F) * 255 // 31
        green = ((value >> 5) & 0x3F) * 255 // 63
        blue = (value & 0x1F) * 255 // 31
        return red, green, blue

    def crop(self, x: int, y: int, width: int, height: int) -> QImage:
        image = QImage(width, height, QImage.Format.Format_RGBA8888)
        image.fill(0)
        first_block_x = x // 4
        last_block_x = (x + width - 1) // 4
        first_block_y = y // 4
        last_block_y = (y + height - 1) // 4
        for block_y in range(first_block_y, last_block_y + 1):
            for block_x in range(first_block_x, last_block_x + 1):
                offset = 128 + (block_y * self.blocks_wide + block_x) * 16
                block = self.data[offset : offset + 16]
                if len(block) != 16:
                    continue
                alpha_bits = int.from_bytes(block[:8], "little")
                color0, color1, color_bits = struct.unpack_from("<HHI", block, 8)
                first = self._rgb565(color0)
                second = self._rgb565(color1)
                colors = (
                    first,
                    second,
                    tuple((2 * first[index] + second[index]) // 3 for index in range(3)),
                    tuple((first[index] + 2 * second[index]) // 3 for index in range(3)),
                )
                for pixel in range(16):
                    source_x = block_x * 4 + pixel % 4
                    source_y = block_y * 4 + pixel // 4
                    target_x = source_x - x
                    target_y = source_y - y
                    if not (0 <= target_x < width and 0 <= target_y < height):
                        continue
                    red, green, blue = colors[(color_bits >> (pixel * 2)) & 0x3]
                    alpha = ((alpha_bits >> (pixel * 4)) & 0xF) * 17
                    image.setPixel(target_x, target_y, qRgba(red, green, blue, alpha))
        return image


class GameGlyphAtlas:
    def __init__(self, hud_root: Path, variant: str = "") -> None:
        self.root = hud_root / variant if variant else hud_root
        self.records: dict[tuple[str, int], GlyphRecord] = {}
        self.keys_by_codepoint: dict[int, list[tuple[str, int]]] = {}
        self.textures: dict[Path, Dxt3Texture] = {}
        self.images: dict[tuple[str, int], QImage] = {}
        self._load_records()

    def _load_records(self) -> None:
        path = self.root / "Sets.dat"
        if not path.is_file():
            return
        data = path.read_bytes()
        offset = 0
        if len(data) < 4:
            return
        set_count = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        textures: list[Path] = []
        for _ in range(set_count):
            name_length = struct.unpack_from("<I", data, offset)[0]
            offset += 4 + name_length
            texture_length = struct.unpack_from("<I", data, offset)[0]
            offset += 4
            texture_name = data[offset : offset + texture_length].rstrip(b"\0").decode("latin1")
            offset += texture_length
            offset += 8
            textures.append(self.root / texture_name)
        record_count = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        for _ in range(record_count):
            name_length = struct.unpack_from("<I", data, offset)[0]
            offset += 4
            name = data[offset : offset + name_length].rstrip(b"\0").decode("latin1")
            offset += name_length
            set_index, x, y, width, height = struct.unpack_from("<5I", data, offset)
            offset += 36
            match = FONT_RECORD_RE.search(name.replace("\\", "/"))
            if match is None or not (0 <= set_index < len(textures)):
                continue
            codepoint = int(match.group("start")) + int(match.group("index"))
            if codepoint > int(match.group("end")):
                continue
            key = (match.group("font").casefold(), codepoint)
            if key not in self.records:
                self.records[key] = GlyphRecord(textures[set_index], x, y, width, height)
                self.keys_by_codepoint.setdefault(codepoint, []).append(key)

    def glyph(self, codepoint: int, font: str = "BookAntiqua_large") -> QImage | None:
        key = (font.casefold(), codepoint)
        if key in self.images:
            return self.images[key]
        record = self.records.get(key)
        if record is None:
            candidates = self.keys_by_codepoint.get(codepoint, ())
            if not candidates:
                return None
            key = candidates[0]
            record = self.records[key]
        if not record.texture_path.is_file():
            return None
        try:
            texture = self.textures.get(record.texture_path)
            if texture is None:
                texture = Dxt3Texture(record.texture_path)
                self.textures[record.texture_path] = texture
            image = texture.crop(record.x, record.y, record.width, record.height)
        except (OSError, ValueError, struct.error):
            return None
        self.images[key] = image
        return image


class GameUiAtlas:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.records: dict[str, UiAssetRecord] = {}
        self.textures: dict[Path, Dxt3Texture | QImage] = {}
        self.images: dict[str, QImage] = {}
        self._load_records()

    def _load_records(self) -> None:
        path = self.root / "Sets.dat"
        if not path.is_file():
            return
        try:
            data = path.read_bytes()
            offset = 0
            set_count = struct.unpack_from("<I", data, offset)[0]
            offset += 4
            textures: list[Path] = []
            for _ in range(set_count):
                name_length = struct.unpack_from("<I", data, offset)[0]
                offset += 4 + name_length
                texture_length = struct.unpack_from("<I", data, offset)[0]
                offset += 4
                texture_name = data[offset : offset + texture_length].rstrip(b"\0").decode("latin1")
                offset += texture_length + 8
                textures.append(self.root / texture_name)
            record_count = struct.unpack_from("<I", data, offset)[0]
            offset += 4
            for _ in range(record_count):
                name_length = struct.unpack_from("<I", data, offset)[0]
                offset += 4
                name = data[offset : offset + name_length].rstrip(b"\0").decode("latin1")
                offset += name_length
                set_index, x, y, width, height = struct.unpack_from("<5I", data, offset)
                offset += 36
                if not (0 <= set_index < len(textures)):
                    continue
                normalized = name.replace("\\", "/").casefold()
                self.records.setdefault(
                    normalized,
                    UiAssetRecord(name, textures[set_index], x, y, width, height),
                )
        except (OSError, UnicodeError, ValueError, struct.error):
            self.records.clear()

    def image(self, name: str) -> QImage | None:
        requested = name.replace("\\", "/").casefold()
        key = requested if requested in self.records else next(
            (
                candidate
                for candidate in self.records
                if candidate.endswith("/" + requested) or candidate.rsplit("/", 1)[-1] == requested
            ),
            "",
        )
        if not key:
            return None
        if key in self.images:
            return self.images[key]
        record = self.records[key]
        if not record.texture_path.is_file():
            return None
        try:
            texture = self.textures.get(record.texture_path)
            if texture is None:
                if record.texture_path.suffix.casefold() == ".dds":
                    texture = Dxt3Texture(record.texture_path)
                else:
                    texture = QImage(str(record.texture_path))
                    if texture.isNull():
                        return None
                self.textures[record.texture_path] = texture
            image = (
                texture.crop(record.x, record.y, record.width, record.height)
                if isinstance(texture, Dxt3Texture)
                else texture.copy(record.x, record.y, record.width, record.height)
            )
        except (OSError, ValueError, struct.error):
            return None
        self.images[key] = image
        return image


class GameLocalization:
    def __init__(self, game_root: Path | None, target_language: str) -> None:
        self.game_root = game_root
        self.target_language = target_language
        self.source: dict[str, str] = {}
        self.target: dict[str, str] = {}
        self.first_name_keys: tuple[str, ...] = ()
        self.surname_keys: tuple[str, ...] = ()
        self._load()

    @staticmethod
    def _read_labels(path: Path) -> dict[str, str]:
        if not path.is_file():
            return {}
        try:
            document = load_dbt(path)
        except (OSError, UnicodeError, ValueError):
            return {}
        fields = translatable_fields(path.name, document.string_columns)
        field = fields[0] if fields else ""
        values: dict[str, str] = {}
        for row in document.rows:
            label = row.get("label") or row.get("key")
            value = row.get(field) if field else ""
            if label:
                values[label] = value
        return values

    @staticmethod
    def _language_folder(language: str) -> str:
        normalized = language.strip().lstrip("#").casefold()
        if normalized in {"zh", "zh-cn", "chinese"}:
            return "#chinese"
        return "#" + normalized if normalized else ""

    def _load(self) -> None:
        if self.game_root is None:
            return
        languages = self.game_root / "DB" / "Languages"
        self.source = self._read_labels(languages / "Text.dbt")
        folder = self._language_folder(self.target_language)
        self.target = self._read_labels(languages / folder / "Text.dbt") if folder else {}
        keys = tuple(sorted(key for key in self.source if key.startswith("_NAMES_")))
        self.first_name_keys = tuple(key for key in keys if "_MALE_+" in key or "_FEMALE_+" in key)
        self.surname_keys = tuple(key for key in keys if "_SURNAMES_+" in key)

    @staticmethod
    def _index(seed: str, size: int) -> int:
        if size <= 0:
            return 0
        digest = hashlib.sha256(seed.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "little") % size

    def _pick(self, values: tuple[str, ...], seed: str) -> str:
        return values[self._index(seed, len(values))] if values else ""

    def localized(self, label: str, target: bool) -> str:
        if target:
            return self.target.get(label) or self.source.get(label) or label
        return self.source.get(label) or label

    def character_name(self, unit_key: str, number: int, target: bool, *, forename_only: bool = False) -> str:
        seed = f"{unit_key}:{number}"
        first_key = self._pick(self.first_name_keys, seed + ":first")
        surname_key = self._pick(self.surname_keys, seed + ":surname")
        if not first_key:
            locale = self.target_language if target else "en"
            return translate("preview.value.character", locale=locale, number=number)
        first = self.localized(first_key, target)
        if forename_only or not surname_key:
            return first
        surname = self.localized(surname_key, target)
        return f"{first} {surname}".strip()


class PreviewService:
    def __init__(
        self,
        game_root: Path | None = None,
        target_language: str = "#chinese",
        translation_font_dir: str = "",
        ui_assets_dir: str = "",
    ) -> None:
        self.game_root = game_root
        self.target_language = target_language
        self.translation_font_dir = translation_font_dir
        self.ui_assets_dir = ui_assets_dir
        self._localization: GameLocalization | None = None
        self._atlases: dict[bool, GameGlyphAtlas | None] = {}
        self._ui_atlas: GameUiAtlas | None = None
        self._render_cache: dict[tuple[str, str, str, bool, str], PreviewDocument] = {}
        self._scaled_glyph_cache: dict[tuple[int, float, int], QImage] = {}

    def configure(
        self,
        game_root: Path | None,
        target_language: str,
        translation_font_dir: str = "",
        ui_assets_dir: str = "",
    ) -> None:
        if (
            game_root == self.game_root
            and target_language == self.target_language
            and translation_font_dir == self.translation_font_dir
            and ui_assets_dir == self.ui_assets_dir
        ):
            return
        self.game_root = game_root
        self.target_language = target_language
        self.translation_font_dir = translation_font_dir
        self.ui_assets_dir = ui_assets_dir
        self._localization = None
        self._atlases.clear()
        self._ui_atlas = None
        self._render_cache.clear()
        self._scaled_glyph_cache.clear()

    @property
    def localization(self) -> GameLocalization:
        if self._localization is None:
            self._localization = GameLocalization(self.game_root, self.target_language)
        return self._localization

    def locale(self, target: bool) -> str:
        if not target:
            return "en"
        normalized = self.target_language.lstrip("#").casefold()
        if normalized in {"zh", "zh-cn", "chinese"}:
            return "zh-CN"
        return normalized

    def _argument_value(self, unit_key: str, number: int, suffix: str, target: bool) -> tuple[str, int | None]:
        locale = self.locale(target)
        if suffix in {"SN", "Sn", "SZ", "Sz"}:
            return self.localization.character_name(unit_key, number, target), None
        if suffix in {"SV", "Sv"}:
            return self.localization.character_name(unit_key, number, target, forename_only=True), None
        if suffix == "DS":
            seed = f"{unit_key}:{number}:crest"
            return GLYPH_MARK, 2029 + GameLocalization._index(seed, 17)
        values = {
            "NAME": "preview.value.city",
            "GG": "preview.value.building_full",
            "GN": "preview.value.building_name",
            "GT": "preview.value.building_type",
            "SK": "preview.value.class",
            "ST": "preview.value.title",
            "SA": "preview.value.office",
            "SD": "preview.value.nobility",
            "SB": "preview.value.profession",
            "SL": "preview.value.level",
            "DN": "preview.value.dynasty",
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
        return translate(key, locale=locale, number=number), None

    def _named_value(self, token: str, unit_key: str, target: bool) -> tuple[str, int | None]:
        name = token[1:-1]
        if name == "n":
            return "\n", None
        if name in {"gold_icon", "hp_icon", "xp_icon", "my_crest"}:
            glyphs = {"gold_icon": 2002, "hp_icon": 2003, "xp_icon": 2056, "my_crest": 2029}
            return GLYPH_MARK, glyphs[name]
        if name == "char_name":
            return self.localization.character_name(unit_key, 1, target), None
        if name == "spouse":
            return self.localization.character_name(unit_key, 2, target), None
        if name == "dyn_surname":
            full = self.localization.character_name(unit_key, 1, target)
            return full.rsplit(" ", 1)[-1], None
        locale = self.locale(target)
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
        return translate(key, locale=locale, name=name, number=1), None

    def _localization_value(self, token: str, target: bool) -> str:
        label = token[2:]
        return self.localization.localized(label, target)

    def render(
        self,
        text: str,
        *,
        unit_key: str,
        file_rel: str,
        kind: str,
        target: bool,
    ) -> PreviewDocument:
        key = (unit_key, file_rel, kind, target, text)
        cached = self._render_cache.get(key)
        if cached is not None:
            return cached
        dialect = format_dialect(file_rel, kind)
        compiler = _PreviewCompiler(self, unit_key, target, dialect)
        document = compiler.compile(text)
        if len(self._render_cache) >= 2048:
            self._render_cache.clear()
        self._render_cache[key] = document
        return document

    def _atlas(self, target: bool) -> GameGlyphAtlas | None:
        if target in self._atlases:
            return self._atlases[target]
        configured = self.translation_font_dir if target else ""
        configured_root = self._configured_directory(configured)
        if configured_root is not None and (configured_root / "Sets.dat").is_file():
            atlas = GameGlyphAtlas(configured_root)
            self._atlases[target] = atlas
            return atlas
        if self.game_root is None:
            self._atlases[target] = None
            return None
        hud_root = self.game_root / "Textures" / "Hud"
        variant = ""
        if target:
            normalized = self.target_language.lstrip("#").casefold()
            variant = "chinese" if normalized in {"zh", "zh-cn", "chinese"} else normalized
        atlas = GameGlyphAtlas(hud_root, variant) if (hud_root / variant / "Sets.dat").is_file() else GameGlyphAtlas(hud_root)
        self._atlases[target] = atlas
        return atlas

    @staticmethod
    def _configured_directory(value: str) -> Path | None:
        if not value.strip():
            return None
        path = Path(value).expanduser()
        return path.parent if path.is_file() else path

    def ui_image(self, name: str) -> QImage | None:
        if self._ui_atlas is None:
            root = self._configured_directory(self.ui_assets_dir)
            if root is None and self.game_root is not None:
                root = self.game_root / "Textures" / "Hud"
            if root is None or not (root / "Sets.dat").is_file():
                return None
            self._ui_atlas = GameUiAtlas(root)
        return self._ui_atlas.image(name)

    def game_window_image(
        self,
        header: PreviewDocument | None,
        body: PreviewDocument | None,
        *,
        target: bool,
    ) -> QImage:
        background = next(
            (
                image
                for name in ("mbback0.tga", "MessagePerga.tga", "Pamphlet.tga")
                if (image := self.ui_image(name)) is not None and not image.isNull()
            ),
            None,
        )
        if background is None or background.isNull():
            canvas = QImage(344, 344, QImage.Format.Format_ARGB32_Premultiplied)
            canvas.fill(QColor("#d8bd83"))
        else:
            canvas = background.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        top = 35
        if header is not None:
            top = self._draw_game_document(
                painter,
                header,
                target=target,
                top=top,
                left=34,
                right=canvas.width() - 34,
                scale=1.0,
                centered=True,
            ) + 12
        if body is not None:
            self._draw_game_document(
                painter,
                body,
                target=target,
                top=top,
                left=32,
                right=canvas.width() - 32,
                scale=0.85,
                centered=False,
            )
        painter.end()
        return canvas

    def _draw_game_document(
        self,
        painter: QPainter,
        document: PreviewDocument,
        *,
        target: bool,
        top: int,
        left: int,
        right: int,
        scale: float,
        centered: bool,
    ) -> int:
        atlas = self._atlas(target)
        if atlas is None:
            return top
        lines: list[list[QImage]] = [[]]
        widths = [0]
        line_height = max(12, round(25 * scale))
        max_lines = max(1, (painter.device().height() - top - 28) // line_height)
        stopped = False

        def append_image(image: QImage) -> bool:
            if widths[-1] + image.width() > right - left and lines[-1]:
                if len(lines) >= max_lines:
                    return False
                lines.append([])
                widths.append(0)
            lines[-1].append(image)
            widths[-1] += image.width()
            return True

        for atom in document.atoms:
            if atom.glyph_id is not None:
                glyph = atlas.glyph(atom.glyph_id)
                if glyph is not None:
                    image = self._scaled_game_glyph(glyph, scale, None)
                    if not append_image(image):
                        break
                continue
            color = QColor(*(atom.color or (55, 38, 24, 255)))
            for char in atom.text.replace(PREVIEW_MARK, ""):
                if char == "\n":
                    if len(lines) >= max_lines:
                        stopped = True
                        break
                    lines.append([])
                    widths.append(0)
                    continue
                if char == "\t":
                    char = " "
                for codepoint in self._game_codepoints(char, target):
                    glyph = atlas.glyph(codepoint)
                    if glyph is None:
                        continue
                    image = self._scaled_game_glyph(glyph, scale, color)
                    if not append_image(image):
                        stopped = True
                        break
                if stopped:
                    break
            if stopped:
                break
        y = top
        for images, width in zip(lines, widths):
            x = left + max(0, (right - left - width) // 2) if centered else left
            for image in images:
                painter.drawImage(x, y + max(0, (line_height - image.height()) // 2), image)
                x += image.width()
            y += line_height
        return y

    def _game_codepoints(self, char: str, target: bool) -> tuple[int, ...]:
        return (ord(char),)

    def _scaled_game_glyph(self, image: QImage, scale: float, color: QColor | None) -> QImage:
        color_key = color.rgba() if color is not None else 0
        cache_key = (image.cacheKey(), scale, color_key)
        cached = self._scaled_glyph_cache.get(cache_key)
        if cached is not None:
            return cached
        width = max(1, round(image.width() * scale))
        height = max(1, round(image.height() * scale))
        source = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        scaled = source.scaled(
            width,
            height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        result = scaled
        if color is not None:
            result = QImage(width, height, QImage.Format.Format_ARGB32)
            result.fill(Qt.GlobalColor.transparent)
            for y in range(height):
                for x in range(width):
                    alpha = scaled.pixelColor(x, y).alpha() * color.alpha() // 255
                    result.setPixel(x, y, qRgba(color.red(), color.green(), color.blue(), alpha))
        if len(self._scaled_glyph_cache) >= 4096:
            self._scaled_glyph_cache.clear()
        self._scaled_glyph_cache[cache_key] = result
        return result

    def glyph_image(self, glyph_id: int, target: bool) -> QImage | None:
        atlas = self._atlas(target)
        return atlas.glyph(glyph_id) if atlas is not None else None

    def text_glyph_image(
        self,
        char: str,
        target: bool,
        color: tuple[int, int, int, int] | None = None,
    ) -> QImage | None:
        atlas = self._atlas(target)
        if atlas is None:
            return None
        codepoints = self._game_codepoints(char, target)
        if len(codepoints) != 1:
            return None
        image = atlas.glyph(codepoints[0])
        if image is None:
            return None
        return self._scaled_game_glyph(image, 1.0, QColor(*(color or (55, 38, 24, 255))))

    def tooltip_html(self, document: PreviewDocument, *, target: bool) -> str:
        line_units = [
            sum(
                2 if unicodedata.east_asian_width(char) in {"W", "F", "A"} else 1
                for char in line
            )
            for line in document.display_text.replace(PREVIEW_MARK, "").splitlines()
        ]
        longest_line = max(line_units, default=0)
        width = max(180, min(520, longest_line * 7 + 24))
        parts = [
            f'<table width="{width}" cellspacing="0" cellpadding="0"><tr>'
            '<td style="white-space:pre-wrap; font-family:&quot;Microsoft YaHei UI&quot;,&quot;Microsoft YaHei&quot;,&quot;SimSun&quot;,sans-serif">'
        ]
        for span in document.spans:
            atom = span.atom
            style: list[str] = []
            if atom.replacement and atom.glyph_id is None and atom.text not in {"\n", "\t", PREVIEW_MARK}:
                style.append("text-decoration:underline")
            if atom.color is not None:
                red, green, blue, alpha = atom.color
                style.append(f"color:rgba({red},{green},{blue},{alpha / 255:.3f})")
                luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
                outline = "#3c3836" if luminance >= 145 else "#fbf1c7"
                style.append(
                    f"text-shadow:-1px 0 {outline},0 1px {outline},1px 0 {outline},0 -1px {outline}"
                )
            if atom.glyph_id is not None:
                image = self.glyph_image(atom.glyph_id, target)
                if image is not None:
                    buffer = QBuffer()
                    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                    image.save(buffer, "PNG")
                    encoded = base64.b64encode(bytes(buffer.data())).decode("ascii")
                    parts.append(
                        f'<img height="18" src="data:image/png;base64,{encoded}">'
                    )
                    continue
            value = html.escape(atom.text.replace(PREVIEW_MARK, ""))
            value = value.replace("\n", "<br>").replace("\t", "&emsp;")
            if style:
                parts.append(f'<span style="{";".join(style)}">{value}</span>')
            else:
                parts.append(value)
        parts.append("</td></tr></table>")
        return "".join(parts)


class _PreviewCompiler:
    def __init__(self, service: PreviewService, unit_key: str, target: bool, dialect: str) -> None:
        self.service = service
        self.unit_key = unit_key
        self.target = target
        self.dialect = dialect
        self.atoms: list[PreviewAtom] = []
        self.color: tuple[int, int, int, int] | None = None
        self.guide_rgb = {"r": 60, "g": 60, "b": 60}
        self.quote_re = re.compile(QUOTE_STYLE_TOKEN)

    def compile(self, text: str) -> PreviewDocument:
        if self.dialect == FORMAT_GUIDE:
            self._compile_matches(text, GUIDE_TOKEN_RE, self._guide_token)
        elif self.dialect == FORMAT_TOOLTIP:
            self._compile_matches(text, TOOLTIP_TOKEN_RE, self._tooltip_token)
        else:
            self._compile_matches(text, GUILD2_TOKEN_RE, self._guild2_token)
        return PreviewDocument.from_atoms(text, self.atoms)

    def _emit(
        self,
        text: str,
        raw_start: int,
        raw_end: int,
        *,
        replacement: bool = False,
        glyph_id: int | None = None,
    ) -> None:
        self.atoms.append(PreviewAtom(text, raw_start, raw_end, replacement, glyph_id, self.color))

    def _compile_matches(self, text: str, pattern: re.Pattern[str], handler) -> None:
        position = 0
        for match in pattern.finditer(text):
            if match.start() > position:
                self._emit(text[position : match.start()], position, match.start())
            handler(match.group(0), match.start(), match.end())
            position = match.end()
        if position < len(text):
            self._emit(text[position:], position, len(text))

    def _guild2_token(self, token: str, start: int, end: int) -> None:
        if self.quote_re.fullmatch(token):
            self._emit("「", start, start + 1, replacement=True)
            inner_start = start + 1
            inner_end = end - 1
            if inner_start < inner_end:
                self._emit(token[1:-1], inner_start, inner_end)
            self._emit("」", end - 1, end, replacement=True)
            return
        if token.startswith("$[") and token.endswith("$]"):
            self._emit(PREVIEW_MARK, start, start + 2, replacement=True)
            inner = token[2:-2]
            nested = _PreviewCompiler(self.service, self.unit_key, self.target, FORMAT_GUILD2)
            nested.color = self.color
            nested_document = nested.compile(inner)
            for atom in nested_document.atoms:
                self.atoms.append(
                    PreviewAtom(
                        atom.text,
                        atom.raw_start + start + 2,
                        atom.raw_end + start + 2,
                        atom.replacement,
                        atom.glyph_id,
                        atom.color,
                    )
                )
            self._emit(PREVIEW_MARK, end - 2, end, replacement=True)
            return
        if token == "$N":
            self._emit("\n", start, end, replacement=True)
            return
        if token == "$T":
            self._emit("\t", start, end, replacement=True)
            return
        if token in {"$>", "%>"}:
            self._emit(">", start, end, replacement=True)
            return
        if token in {"$<", "%<"}:
            self._emit("<", start, end, replacement=True)
            return
        if token in {"$Z", "$L", "$R"}:
            self._emit(PREVIEW_MARK, start, end, replacement=True)
            return
        if COLOR_TOKEN_RE.fullmatch(token):
            values = [int(value) for value in COLOR_VALUE_RE.findall(token)]
            if len(values) == 3:
                values.append(255)
            self.color = (values[0], values[1], values[2], values[3])
            self._emit(PREVIEW_MARK, start, end, replacement=True)
            return
        symbol = SYMBOL_PREVIEW_RE.fullmatch(token)
        if symbol is not None:
            self._emit(GLYPH_MARK, start, end, replacement=True, glyph_id=int(symbol.group(1)))
            return
        if token.startswith(("$F[", "$B[", "#E[", "#SP", "@N")):
            self._emit(PREVIEW_MARK, start, end, replacement=True)
            return
        if token.startswith("@L_"):
            self._emit(self.service._localization_value(token, self.target), start, end, replacement=True)
            return
        if token.startswith('@T"') and token.endswith('"'):
            self._emit(token[3:-1], start, end, replacement=True)
            return
        if token == "%%":
            self._emit("%", start, end, replacement=True)
            return
        argument = ARG_PREVIEW_RE.fullmatch(token)
        if argument is not None:
            number = int(argument.group(1))
            if argument.group(2) == "t":
                locale = self.service.locale(self.target)
                split = max(start, end - 1)
                self._emit(
                    translate("preview.value.money_plain", locale=locale, number=number),
                    start,
                    split,
                    replacement=True,
                )
                self._emit(GLYPH_MARK, split, end, replacement=True, glyph_id=2002)
                return
            value, glyph_id = self.service._argument_value(
                self.unit_key,
                number,
                argument.group(2),
                self.target,
            )
            self._emit(value, start, end, replacement=True, glyph_id=glyph_id)
            return
        if PRINTF_PREVIEW_RE.fullmatch(token):
            suffix = token[-1].casefold()
            locale = self.service.locale(self.target)
            key = "preview.value.float" if suffix in {"f", "e", "g"} else (
                "preview.value.string" if suffix == "s" else "preview.value.integer"
            )
            self._emit(translate(key, locale=locale, number=1), start, end, replacement=True)
            return
        self._emit(token, start, end)

    def _tooltip_token(self, token: str, start: int, end: int) -> None:
        if SYMBOL_PREVIEW_RE.fullmatch(token):
            self._guild2_token(token, start, end)
            return
        if token in {"%dyn_color%", "%officer_color:alderman%"}:
            self.color = (115, 5, 20, 255)
            self._emit(PREVIEW_MARK, start, end, replacement=True)
            return
        if token == "%color_reset%":
            self.color = None
            self._emit(PREVIEW_MARK, start, end, replacement=True)
            return
        value, glyph_id = self.service._named_value(token, self.unit_key, self.target)
        self._emit(value, start, end, replacement=True, glyph_id=glyph_id)

    def _guide_token(self, token: str, start: int, end: int) -> None:
        lowered = token.casefold()
        if token.startswith("<"):
            if lowered.startswith("<separator"):
                value = "\n────────\n"
            elif lowered in {"</header>", "</text>", "</list>", "</table>"}:
                value = "\n\n"
            elif lowered == "</row>":
                value = "\n"
            elif lowered == "</cell>":
                value = "\t"
            elif lowered == "<item>":
                value = "• "
            elif lowered == "</item>":
                value = "\n"
            else:
                value = PREVIEW_MARK
            self._emit(value, start, end, replacement=True)
            return
        guide_value = GUIDE_VALUE_RE.fullmatch(token)
        if guide_value is not None:
            channel, value = guide_value.groups()
            self.guide_rgb[channel] = min(int(value), 255)
            self.color = (
                self.guide_rgb["r"],
                self.guide_rgb["g"],
                self.guide_rgb["b"],
                255,
            )
            self._emit(PREVIEW_MARK, start, end, replacement=True)
            return
        if token.startswith("{key:"):
            key = token[5:-1].replace("_", " ")
            self._emit(key, start, end, replacement=True)
            return
        if token.startswith(("{autolist:", "{bullet_autolist:")):
            name = token[1:-1].split(":", 1)[1].replace("_", " ")
            locale = self.service.locale(self.target)
            value = translate("preview.value.dynamic_list", locale=locale, name=name)
            self._emit(value, start, end, replacement=True)
            return
        self._emit(PREVIEW_MARK, start, end, replacement=True)
