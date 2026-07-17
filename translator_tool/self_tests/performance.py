from __future__ import annotations


LARGE_BATCH_MIN_ENTRIES = 10_000
LARGE_BATCH_SAVE_LIMIT_SECONDS = 8.0
CLIPBOARD_DECODE_LIMIT_SECONDS = 2.0


def assert_within_budget(name: str, elapsed: float, limit: float, *, detail: str = "") -> None:
    if elapsed <= limit:
        return
    suffix = f" ({detail})" if detail else ""
    raise AssertionError(f"{name} is too slow: {elapsed:.3f}s > {limit:.3f}s{suffix}")
