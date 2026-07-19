from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

from ..git_history import LanguageGit
from ..settings import AppSettings


def assert_tracked_git_commit_skips_redundant_add() -> None:
    root = Path(tempfile.mkdtemp(prefix="translator_git_commit_"))
    try:
        language_root = root / "languages" / "#chinese"
        language_root.mkdir(parents=True)
        tracked = language_root / "Guide.txt"
        tracked.write_text("before", encoding="utf-8")
        git = LanguageGit(root, "#chinese", enable_codec=False)
        git.ensure_repository(AppSettings())

        calls: list[tuple[str, ...]] = []
        original_run = git._run

        def tracked_run(*args: str, **kwargs: object):
            calls.append(args)
            return original_run(*args, **kwargs)

        git._run = tracked_run  # type: ignore[method-assign]
        tracked.write_text("after", encoding="utf-8")
        commit = git.commit_saved((tracked,), ())
        if commit is None:
            raise AssertionError("tracked Git save did not create a commit")
        if any(args and args[0] == "add" for args in calls):
            raise AssertionError("tracked Git save redundantly staged a file before commit --only")

        calls.clear()
        untracked = language_root / "New.txt"
        untracked.write_text("new", encoding="utf-8")
        commit = git.commit_saved((untracked,), ())
        if commit is None or not any(args and args[0] == "add" for args in calls):
            raise AssertionError("new Git file was not staged before its first commit")
    finally:
        shutil.rmtree(root, ignore_errors=True)
