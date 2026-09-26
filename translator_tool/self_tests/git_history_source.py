from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

from ..git_history import LanguageGit
from ..history_index import HistoryIndexStore
from ..settings import AppSettings


def assert_history_uses_live_source_missing_from_old_commits() -> None:
    root = Path(tempfile.mkdtemp(prefix="translator_git_history_source_"))
    try:
        repo = root / "languages"
        target_root = repo / "#chinese"
        target_root.mkdir(parents=True)
        target = target_root / "Kontor.dbt"
        target.write_bytes(_dbt_bytes("chinese", "旧译文"))

        git = LanguageGit(root, "#chinese", enable_codec=False)
        git.ensure_repository(AppSettings())

        source = repo / "Kontor.dbt"
        source.write_bytes(_dbt_bytes("english", "Trade licence"))
        target.write_bytes(_dbt_bytes("chinese", "新译文"))
        commit = git.commit_saved((target,), ())
        if commit is None:
            raise AssertionError("missing-source history fixture did not create a target commit")

        entries = git.entries_for_commit(commit.full_hash)
        _assert_kontor_entry(entries)

        git._entry_cache.clear()
        dependencies: dict[str, dict[str, str]] = {}
        batched = dict(git.iter_entries_for_commits((commit.full_hash,), source_dependencies=dependencies))
        _assert_kontor_entry(batched.get(commit.full_hash, []))

        store = HistoryIndexStore.for_repository(repo, "#chinese", codec_fingerprint=git.history_codec_fingerprint)
        store.store_commit(commit.full_hash, batched[commit.full_hash], dependencies[commit.full_hash])
        store.store_commit("committed-source", entries)
        # Files never used as fallback must not invalidate either commit.
        (repo / "Unrelated.dbt").write_bytes(_dbt_bytes("english", "Unrelated"))
        store.invalidate_changed_sources(git.source_fingerprint)
        hashes = (commit.full_hash, "committed-source")
        if store.indexed_hashes(hashes) != set(hashes):
            raise AssertionError("unrelated source edit discarded the persistent history index")

        first_fingerprint = git.history_cache_fingerprint
        source.write_bytes(_dbt_bytes("english", "Updated trade licence"))
        if git.history_cache_fingerprint == first_fingerprint:
            raise AssertionError("history cache did not invalidate when a live fallback source changed")
        store.invalidate_changed_sources(git.source_fingerprint)
        if store.indexed_hashes(hashes) != {"committed-source"}:
            raise AssertionError("fallback source edit failed to invalidate only its dependent commits")
        refreshed = git.entries_for_commit(commit.full_hash)
        if refreshed[0].source_text != "Updated trade licence":
            raise AssertionError("in-memory history cache retained stale live source text")
        source.unlink()
        missing = dict(git.iter_entries_for_commits((commit.full_hash,), source_dependencies=dependencies))
        if missing[commit.full_hash]:
            raise AssertionError("removed live source remained in the in-memory history cache")
        store.store_commit(commit.full_hash, (), dependencies[commit.full_hash])
        source.write_bytes(_dbt_bytes("english", "Restored source"))
        store.invalidate_changed_sources(git.source_fingerprint)
        if commit.full_hash in store.indexed_hashes(hashes):
            raise AssertionError("previously missing source did not invalidate empty indexed history")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _assert_kontor_entry(entries: list) -> None:
    if len(entries) != 1:
        raise AssertionError(f"history dropped Kontor changes without a committed source: {entries!r}")
    entry = entries[0]
    if entry.file_rel != "Kontor.dbt" or entry.source_text != "Trade licence":
        raise AssertionError("history did not pair the target change with the live Kontor source")
    if entry.previous_text != "旧译文" or entry.translated_text != "新译文":
        raise AssertionError("history changed the recorded Kontor before/after text")


def _dbt_bytes(field: str, value: str) -> bytes:
    text = (
        "Table Description:\n"
        f'"id" INT 0 |"label" STRING 0 |"{field}" STRING 0 |\n\n'
        "Data:\n"
        f'1 "_KR_TEST_+0" "{value}" |\n'
    )
    return text.encode("utf-16-le")
