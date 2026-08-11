from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .format_io import dbt_row_values, load_dbt


MAX_DATABASE_DOMAIN_VALUES = 64
DatabaseValueDomains = tuple[tuple[str, str, tuple[str, ...]], ...]


def game_database_value_domains(
    game_root: Path | None,
    mod_name: str = "",
) -> DatabaseValueDomains:
    """Return bounded effective DB column domains for script value analysis."""
    if game_root is None:
        return ()
    roots = [game_root / "DB"]
    if mod_name:
        roots.append(game_root / "mods" / mod_name / "DB")
    identities: list[tuple[str, int, int]] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.dbt"), key=lambda item: item.name.casefold()):
            try:
                stat = path.stat()
            except OSError:
                continue
            identities.append((str(path.resolve()), stat.st_mtime_ns, stat.st_size))
    return _cached_database_value_domains(tuple(identities))


@lru_cache(maxsize=8)
def _cached_database_value_domains(
    identities: tuple[tuple[str, int, int], ...],
) -> DatabaseValueDomains:
    rows: dict[tuple[str, str, str], str] = {}
    for path_text, _modified_ns, _size in identities:
        path = Path(path_text)
        try:
            document = load_dbt(path)
        except (OSError, UnicodeError, ValueError):
            continue
        table = path.stem.casefold()
        for row_number, row in enumerate(document.rows):
            values = dbt_row_values(row)
            row_identity = values[0] if values else str(row_number)
            for (field, _kind), value in zip(document.columns, values):
                if not value or value == "~":
                    continue
                rows[(table, row_identity, field.casefold())] = value

    grouped: dict[tuple[str, str], list[str]] = {}
    overflowed: set[tuple[str, str]] = set()
    for (table, _row_identity, field), value in rows.items():
        key = (table, field)
        if key in overflowed:
            continue
        values = grouped.setdefault(key, [])
        if value in values:
            continue
        values.append(value)
        if len(values) > MAX_DATABASE_DOMAIN_VALUES:
            grouped.pop(key, None)
            overflowed.add(key)
    return tuple(
        (table, field, tuple(values))
        for (table, field), values in sorted(grouped.items())
        if values
    )
