from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import re

from .format_io import dbt_row_values, load_dbt


_ITEM_LABEL_RE = re.compile(
    r"^(?:@L)?_?ITEM_(?P<name>.+?)_(?:NAME|TOOLTIP)_\+[A-Za-z0-9*]+$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ItemIngredientPreview:
    name: str
    count: int

    @property
    def icon_asset(self) -> str:
        return f"Hud/Items/Item_{self.name}.tga"


@dataclass(frozen=True)
class ItemPreviewData:
    name: str
    ingredients: tuple[ItemIngredientPreview, ...]

    @property
    def icon_asset(self) -> str:
        return f"Hud/Items/Item_{self.name}.tga"


def item_preview_data(game_root: Path | None, *labels: str) -> ItemPreviewData | None:
    if game_root is None:
        return None
    item_name = next(
        (
            match.group("name")
            for label in labels
            if (match := _ITEM_LABEL_RE.match(label.strip()))
        ),
        "",
    )
    if not item_name:
        return None
    items_path = game_root / "DB" / "Items.dbt"
    try:
        stat = items_path.stat()
    except OSError:
        return None
    return _cached_item_preview_data(
        str(items_path.resolve()),
        stat.st_mtime_ns,
        stat.st_size,
        item_name.casefold(),
    )


@lru_cache(maxsize=128)
def _cached_item_preview_data(
    path_text: str,
    _modified_ns: int,
    _size: int,
    item_name: str,
) -> ItemPreviewData | None:
    try:
        document = load_dbt(Path(path_text))
    except (OSError, UnicodeError, ValueError):
        return None
    rows_by_id: dict[int, tuple[str, ...]] = {}
    rows_by_name: dict[str, tuple[str, ...]] = {}
    for row in document.rows:
        values = dbt_row_values(row)
        if len(values) < 19 or not values[0].lstrip("-").isdigit():
            continue
        rows_by_id[int(values[0])] = values
        rows_by_name[values[1].casefold()] = values
    values = rows_by_name.get(item_name)
    if values is None:
        return None
    ingredients: list[ItemIngredientPreview] = []
    for count_index, item_index in ((13, 14), (15, 16), (17, 18)):
        raw_count = values[count_index]
        raw_id = values[item_index]
        if not raw_count.lstrip("-").isdigit() or not raw_id.lstrip("-").isdigit():
            continue
        count = int(raw_count)
        ingredient = rows_by_id.get(int(raw_id))
        if count <= 0 or ingredient is None:
            continue
        ingredients.append(ItemIngredientPreview(ingredient[1], count))
    return ItemPreviewData(values[1], tuple(ingredients))
