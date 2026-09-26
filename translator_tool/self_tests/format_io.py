from __future__ import annotations

from pathlib import Path

from ..format_io import detect_encoding, load_dbt_bytes


def assert_incremental_dbt_parse_matches_full_parse() -> None:
    path = Path("Text.dbt")
    header = '"id" INT |"label" STRING |"english" STRING |\r\nData:\r\n'
    body = '1 "ONE" "First" |\r\n2 "TWO" "Second\r\nline" |\r\n3 "THREE" "Third" |'
    text = header + body
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        original = load_dbt_bytes(path, text.encode(encoding))
        cases = (
            text.replace('"First"', '"Edited"'),
            text.replace('1 "ONE" "First" |\r\n', ''),
            text.replace('2 "TWO"', '4 "FOUR" "Inserted" |\r\n2 "TWO"'),
            text.replace('Second\r\nline', 'Second\r\nlonger\r\nline'),
            text.replace('"english" STRING', '"chinese" STRING'),
            text.replace('"First"', '"Unclosed'),
            text.replace('\r\n', '\n') + '\n',
            '',
        )
        for changed in cases:
            raw = changed.encode(encoding)
            incremental = load_dbt_bytes(path, raw, previous=original)
            full = load_dbt_bytes(path, raw)
            if incremental != full or incremental.render_bytes() != full.render_bytes():
                raise AssertionError(f"incremental DBT parse changed semantics for {encoding}")
        changed = text.replace('"First"', '"Edited"').encode(encoding)
        incremental = load_dbt_bytes(path, changed, previous=original)
        if incremental.rows[1] is not original.rows[1] or incremental.rows[0] is original.rows[0]:
            raise AssertionError("incremental DBT parse did not reuse only unchanged rows")
        original.rows[1].set_raw("english", "Unsaved")
        original.rows[2].delete()
        pristine = load_dbt_bytes(path, changed, previous=original)
        if pristine != load_dbt_bytes(path, changed):
            raise AssertionError("incremental parse reused unsaved row mutations")


def assert_guild2_encoding_detection() -> None:
    utf16_text = (
        'Table Description:\r\n'
        '"id" INT  0   |"label" STRING  0   |"english" STRING  0   |\r\n'
        '\r\nData:\r\n'
        '1     "TEST"   "Text"   |\r\n'
    )
    raw_utf16_le = utf16_text.encode("utf-16-le")
    if detect_encoding(raw_utf16_le) != "utf-16-le":
        raise AssertionError("BOM-less Guild 2 UTF-16 LE was not detected")
    document = load_dbt_bytes(Path("Kontor.dbt"), raw_utf16_le)
    if len(document.rows) != 1 or document.rows[0].get("english") != "Text":
        raise AssertionError("BOM-less Guild 2 UTF-16 LE DBT was not parsed")
    if document.render_bytes() != raw_utf16_le:
        raise AssertionError("BOM-less UTF-16 LE gained a BOM or changed bytes")

    raw_utf8 = utf16_text.encode("utf-8")
    if detect_encoding(raw_utf8) != "utf-8":
        raise AssertionError("Guild 2 UTF-8 DBT was misdetected as UTF-16")
