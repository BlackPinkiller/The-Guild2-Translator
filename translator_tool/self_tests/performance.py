from __future__ import annotations

from time import perf_counter


LARGE_BATCH_MIN_ENTRIES = 10_000
LARGE_BATCH_SAVE_LIMIT_SECONDS = 8.0
CLIPBOARD_DECODE_LIMIT_SECONDS = 2.0
SAVED_FILE_REFRESH_LIMIT_SECONDS = 1.5
AI_CONTEXT_BUILD_LIMIT_SECONDS = 0.5
CACHED_SEARCH_FILTER_LIMIT_SECONDS = 0.25


def assert_within_budget(name: str, elapsed: float, limit: float, *, detail: str = "") -> None:
    if elapsed <= limit:
        return
    suffix = f" ({detail})" if detail else ""
    raise AssertionError(f"{name} is too slow: {elapsed:.3f}s > {limit:.3f}s{suffix}")


def assert_large_cached_search_filter_stays_interactive() -> None:
    from ..app import FILE_FILTER_ALL, STATUS_FILTER_ALL, UnitFilterProxyModel, UnitTableModel

    row_count = 20_000
    model = UnitTableModel()
    # This regression isolates the cached unfielded-search path. The proxy
    # should scan the parallel blobs once instead of asking Qt to call back
    # into Python for every source row.
    model.units = [None] * row_count  # type: ignore[list-item]
    model._search_rows = [
        f"file text label_{row} source translation"
        for row in range(row_count)
    ]
    model._search_rows_case_sensitive = list(model._search_rows)
    proxy = UnitFilterProxyModel()
    proxy.only_missing = False
    proxy.setSourceModel(model)

    started = perf_counter()
    proxy.set_filters(
        file_filter=FILE_FILTER_ALL,
        status_filter=STATUS_FILTER_ALL,
        only_missing=False,
        only_format_warnings=False,
        query="label_19999",
    )
    elapsed = perf_counter() - started
    assert_within_budget(
        "cached 20k-row search filter",
        elapsed,
        CACHED_SEARCH_FILTER_LIMIT_SECONDS,
        detail=f"visible={proxy.rowCount()}",
    )
    if proxy.rowCount() != 1 or proxy.mapToSource(proxy.index(0, 0)).row() != 19_999:
        raise AssertionError("cached large-project search returned the wrong source row")
