from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile

from ..diagnostics import configure_diagnostics, log_exception, log_metrics, shutdown_diagnostics


def assert_diagnostics_are_bounded_and_content_free() -> None:
    root = Path(tempfile.mkdtemp(prefix="translator_diagnostics_"))
    secret = "DO_NOT_LOG_TRANSLATION_OR_API_KEY"
    previous_hook = sys.excepthook
    try:
        path = configure_diagnostics(root, max_bytes=320, backup_count=2)
        if path != root / "diagnostics.log":
            raise AssertionError("diagnostic log did not use the requested isolated directory")
        for number in range(80):
            log_metrics("save_complete", total_ms=number / 3, saved_units=number, git_failed=False)
        try:
            log_metrics("unsafe_metric", content=secret)  # type: ignore[arg-type]
        except TypeError:
            pass
        else:
            raise AssertionError("diagnostic logger accepted non-numeric content")
        try:
            raise RuntimeError(secret)
        except RuntimeError:
            error_type, _error, tb = sys.exc_info()
            assert error_type is not None
            log_exception("test_failure", error_type, tb)
        shutdown_diagnostics()

        logs = sorted(root.glob("diagnostics.log*"))
        if not logs or len(logs) > 3:
            raise AssertionError("diagnostic log rotation exceeded its configured file bound")
        combined = b"".join(path.read_bytes() for path in logs).decode("utf-8", "replace")
        if secret in combined:
            raise AssertionError("diagnostic log recorded exception content or a text metric")
        if "error_type=RuntimeError" not in combined:
            raise AssertionError("diagnostic log omitted the safe exception type")
        if sum(path.stat().st_size for path in logs) > 3 * 640:
            raise AssertionError("diagnostic log rotation exceeded its conservative size bound")
        if sys.excepthook is not previous_hook:
            raise AssertionError("diagnostic shutdown did not restore the previous exception hook")
    finally:
        shutdown_diagnostics()
        shutil.rmtree(root, ignore_errors=True)
