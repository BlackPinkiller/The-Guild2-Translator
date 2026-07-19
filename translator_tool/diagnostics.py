from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import re
import sys
import threading
import traceback
from types import TracebackType

from .settings import settings_dir


LOG_FILE_NAME = "diagnostics.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3
_EVENT_RE = re.compile(r"[a-z][a-z0-9_.\-_]{0,47}\Z")
_LOGGER = logging.getLogger("translator_tool.diagnostics")
_LOCK = threading.RLock()
_handler: RotatingFileHandler | None = None
_previous_sys_hook = sys.excepthook
_previous_thread_hook = threading.excepthook


def diagnostic_log_path() -> Path:
    return settings_dir() / LOG_FILE_NAME


def configure_diagnostics(
    log_dir: Path | None = None,
    *,
    max_bytes: int = LOG_MAX_BYTES,
    backup_count: int = LOG_BACKUP_COUNT,
) -> Path | None:
    """Start a bounded diagnostic log; failure to log must never stop the editor."""
    global _handler
    if max_bytes <= 0 or backup_count < 0:
        raise ValueError("diagnostic log bounds must be positive")
    with _LOCK:
        if _handler is not None:
            return Path(_handler.baseFilename)
        path = (log_dir or settings_dir()) / LOG_FILE_NAME
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
                delay=True,
            )
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            _LOGGER.setLevel(logging.INFO)
            _LOGGER.propagate = False
            _LOGGER.addHandler(handler)
            _handler = handler
            sys.excepthook = _sys_exception_hook
            threading.excepthook = _thread_exception_hook
        except OSError:
            return None
    log_metrics("session_start", pid=os.getpid())
    return path


def shutdown_diagnostics() -> None:
    global _handler
    with _LOCK:
        handler, _handler = _handler, None
        sys.excepthook = _previous_sys_hook
        threading.excepthook = _previous_thread_hook
        if handler is None:
            return
        _LOGGER.removeHandler(handler)
        try:
            handler.close()
        except OSError:
            pass


def log_metrics(event: str, **metrics: int | float | bool) -> None:
    """Log only numeric metrics, preventing translation text or secrets from entering the log."""
    _validate_event(event)
    fields: list[str] = []
    for key, value in sorted(metrics.items()):
        if not _EVENT_RE.fullmatch(key):
            raise ValueError(f"invalid diagnostic metric name: {key!r}")
        if isinstance(value, bool):
            rendered = "1" if value else "0"
        elif isinstance(value, int):
            rendered = str(value)
        elif isinstance(value, float) and math.isfinite(value):
            rendered = f"{value:.3f}"
        else:
            raise TypeError("diagnostic metrics must be finite numbers or booleans")
        fields.append(f"{key}={rendered}")
    try:
        _LOGGER.info("event=%s%s", event, " " + " ".join(fields) if fields else "")
    except Exception:
        pass


def log_failure(event: str, error: BaseException, **metrics: int | float | bool) -> None:
    """Record an error type and numeric context without the exception message."""
    _validate_event(event)
    safe_metrics = dict(metrics)
    try:
        details = " ".join(f"{key}={value}" for key, value in sorted(_render_metrics(safe_metrics).items()))
        suffix = f" {details}" if details else ""
        _LOGGER.error("event=%s error_type=%s%s", event, type(error).__name__, suffix)
    except Exception:
        pass


def log_exception(event: str, error_type: type[BaseException], tb: TracebackType | None) -> None:
    """Record bounded stack locations without exception messages, locals, or source text."""
    _validate_event(event)
    frames = traceback.extract_tb(tb, limit=24) if tb is not None else []
    stack = " > ".join(f"{Path(frame.filename).name}:{frame.name}:{frame.lineno}" for frame in frames)
    try:
        _LOGGER.critical("event=%s error_type=%s stack=%s", event, error_type.__name__, stack or "none")
    except Exception:
        pass


def _render_metrics(metrics: dict[str, int | float | bool]) -> dict[str, str]:
    rendered: dict[str, str] = {}
    for key, value in metrics.items():
        if not _EVENT_RE.fullmatch(key):
            raise ValueError(f"invalid diagnostic metric name: {key!r}")
        if isinstance(value, bool):
            rendered[key] = "1" if value else "0"
        elif isinstance(value, int):
            rendered[key] = str(value)
        elif isinstance(value, float) and math.isfinite(value):
            rendered[key] = f"{value:.3f}"
        else:
            raise TypeError("diagnostic metrics must be finite numbers or booleans")
    return rendered


def _validate_event(event: str) -> None:
    if not _EVENT_RE.fullmatch(event):
        raise ValueError(f"invalid diagnostic event name: {event!r}")


def _sys_exception_hook(error_type: type[BaseException], error: BaseException, tb: TracebackType | None) -> None:
    log_exception("uncaught_main", error_type, tb)
    _previous_sys_hook(error_type, error, tb)


def _thread_exception_hook(args: threading.ExceptHookArgs) -> None:
    log_exception("uncaught_thread", args.exc_type, args.exc_traceback)
    _previous_thread_hook(args)
