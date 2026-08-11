from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .format_io import dbt_row_values, load_dbt


def game_item_names_by_id(
    game_root: Path | None,
    mod_name: str = "",
) -> tuple[tuple[int, str], ...]:
    """Return the effective finite item ID-to-name table for script analysis."""
    if game_root is None:
        return ()
    paths = [game_root / "DB" / "Items.dbt"]
    if mod_name:
        paths.append(game_root / "mods" / mod_name / "DB" / "Items.dbt")
    identities: list[tuple[str, int, int]] = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        identities.append((str(path.resolve()), stat.st_mtime_ns, stat.st_size))
    return _cached_item_names_by_id(tuple(identities))


@lru_cache(maxsize=8)
def _cached_item_names_by_id(
    identities: tuple[tuple[str, int, int], ...],
) -> tuple[tuple[int, str], ...]:
    names: dict[int, str] = {}
    for path_text, _modified_ns, _size in identities:
        try:
            document = load_dbt(Path(path_text))
        except (OSError, UnicodeError, ValueError):
            continue
        for row in document.rows:
            values = dbt_row_values(row)
            if (
                len(values) < 2
                or not values[0].lstrip("-").isdigit()
                or not values[1]
                or values[1] == "~"
            ):
                continue
            names[int(values[0])] = values[1]
    return tuple(sorted(names.items()))
