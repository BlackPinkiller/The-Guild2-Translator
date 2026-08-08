from __future__ import annotations

from pathlib import Path

from ..format_io import detect_encoding, load_dbt_bytes


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
