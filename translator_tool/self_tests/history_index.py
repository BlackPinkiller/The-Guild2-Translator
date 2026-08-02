from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

from ..git_history import TranslationLogEntry
from .. import history_index as history_index_module
from ..history_index import HistoryIndexCapacityError, HistoryIndexStore


def assert_history_index_is_persistent_and_bounded() -> None:
    root = Path(tempfile.mkdtemp(prefix="translator_history_index_"))
    original_limit = history_index_module.MAX_INDEXED_COMMITS
    try:
        plain_path = HistoryIndexStore.for_repository(
            root,
            "#chinese",
            codec_fingerprint="plain",
        ).path
        codec_path = HistoryIndexStore.for_repository(
            root,
            "#chinese",
            codec_fingerprint="codec-test",
        ).path
        if plain_path == codec_path:
            raise AssertionError("history index cache key ignored codec-dependent decoded text")

        store = HistoryIndexStore(root / "history.sqlite3")
        first = TranslationLogEntry("新增", "Text.dbt", "10", "Greeting", "Text", "Hello", "您好")
        revised = TranslationLogEntry("更新", "Text.dbt", "10", "Greeting", "Text", "Hello", "你好", "您好")
        store.store_commit("first", (first,))
        store.store_commit("empty", ())
        store.store_commit("revised", (revised,))

        reopened = HistoryIndexStore(root / "history.sqlite3")
        if reopened.indexed_hashes(("first", "empty", "missing")) != {"first", "empty"}:
            raise AssertionError("history index did not persist indexed commits, including an empty commit")
        entries = reopened.entries_for_commits(("revised", "first"))
        if entries["first"] != [first] or entries["revised"] != [revised]:
            raise AssertionError("history index did not preserve translation log entry fields")

        batched = HistoryIndexStore(root / "batched.sqlite3")
        with batched.writer() as writer:
            for number in range(100):
                writer.store_commit(f"batch-{number}", ())
        if len(batched.indexed_hashes(f"batch-{number}" for number in range(100))) != 100:
            raise AssertionError("history index write session did not persist every commit")

        closable = HistoryIndexStore(root / "closable.sqlite3")
        closable.store_commit("one", ())
        closable.indexed_hashes(("one",))
        closable.path.unlink()
        if closable.path.exists():
            raise AssertionError("history index left its SQLite file locked after an operation")

        corrupt = HistoryIndexStore(root / "corrupt.sqlite3")
        corrupt.path.write_bytes(b"not a sqlite database")
        if corrupt.indexed_hashes(("missing",)):
            raise AssertionError("rebuilt corrupt history index unexpectedly contained entries")
        corrupt.store_commit("recovered", ())
        if corrupt.indexed_hashes(("recovered",)) != {"recovered"}:
            raise AssertionError("disposable corrupt history index was not rebuilt")

        reopened.retain_commits(("revised",))
        if reopened.indexed_hashes(("first", "revised")) != {"revised"}:
            raise AssertionError("history index retained commits that are no longer in the indexed history window")

        history_index_module.MAX_INDEXED_COMMITS = 1
        bounded = HistoryIndexStore(root / "bounded.sqlite3")
        bounded.store_commit("one", ())
        bounded.store_commit("one", ())
        try:
            bounded.store_commit("two", ())
        except HistoryIndexCapacityError:
            pass
        else:
            raise AssertionError("history index commit bound was not enforced")
    finally:
        history_index_module.MAX_INDEXED_COMMITS = original_limit
        shutil.rmtree(root, ignore_errors=True)
