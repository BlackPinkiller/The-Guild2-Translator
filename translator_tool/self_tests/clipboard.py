from __future__ import annotations

import json
import time

from ..entry_clipboard import MAX_ENTRY_CLIPBOARD_COUNT, decode_translations
from .performance import CLIPBOARD_DECODE_LIMIT_SECONDS, assert_within_budget


def assert_entry_clipboard_decoder() -> None:
    if decode_translations(b"") is not None:
        raise AssertionError("empty entry clipboard payload was accepted")
    if decode_translations(b"{}") is not None:
        raise AssertionError("non-list entry clipboard payload was accepted")
    if decode_translations(b'[{"translation":1}]') is not None:
        raise AssertionError("non-text entry clipboard translation was accepted")

    payload = [{"translation": "x"}] * MAX_ENTRY_CLIPBOARD_COUNT
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    started = time.perf_counter()
    decoded = decode_translations(raw)
    elapsed = time.perf_counter() - started
    if decoded is None or len(decoded) != MAX_ENTRY_CLIPBOARD_COUNT:
        raise AssertionError("maximum supported entry clipboard payload was not decoded")
    assert_within_budget(
        "entry clipboard decode",
        elapsed,
        CLIPBOARD_DECODE_LIMIT_SECONDS,
        detail=f"{MAX_ENTRY_CLIPBOARD_COUNT} entries",
    )
