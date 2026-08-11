from __future__ import annotations

import ctypes
import os
from pathlib import Path
from queue import Empty
from typing import Callable

from .code_index import CodeReferenceIndex
from .code_index_lazy import LazyCodeIndexBuilder


_IDLE_PRIORITY_CLASS = 0x00000040


class IndexRequestScheduler:
    """Keep only the newest request at each priority and preempt stale work."""

    def __init__(self) -> None:
        self._requested: dict[str, tuple[int, int]] = {}

    def replace(self, priority: int, generation: int, labels: tuple[str, ...]) -> None:
        self._requested = {
            label: request
            for label, request in self._requested.items()
            if request[0] != priority
        }
        for label in labels:
            self._requested[label] = (priority, generation)

    def take(self) -> tuple[int, int, tuple[str, ...]] | None:
        if not self._requested:
            return None
        priority = min(request[0] for request in self._requested.values())
        generation = max(
            request[1]
            for request in self._requested.values()
            if request[0] == priority
        )
        labels = tuple(
            label
            for label, request in self._requested.items()
            if request == (priority, generation)
        )
        self._requested = {
            label: request
            for label, request in self._requested.items()
            if request[0] != priority
        }
        return priority, generation, labels

    def supersedes(self, priority: int, generation: int) -> bool:
        return any(
            requested_priority < priority
            or (
                requested_priority == priority
                and requested_generation > generation
            )
            for requested_priority, requested_generation in self._requested.values()
        )

    @property
    def pending(self) -> bool:
        return bool(self._requested)


def _set_process_priority(priority_class: int) -> None:
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), priority_class)
    except (AttributeError, OSError):
        pass


def _handle_command(
    command: object,
    scheduler: IndexRequestScheduler,
    background_enabled: bool,
) -> tuple[bool, bool]:
    if not isinstance(command, tuple) or not command:
        return background_enabled, False
    kind = command[0]
    if kind == "request" and len(command) == 4:
        _kind, priority, generation, labels = command
        if isinstance(priority, int) and isinstance(generation, int) and isinstance(labels, tuple):
            scheduler.replace(priority, generation, labels)
    elif kind == "background" and len(command) == 2:
        background_enabled = bool(command[1])
    elif kind == "cancel":
        return background_enabled, True
    return background_enabled, False


def _drain_commands(
    command_queue,
    scheduler: IndexRequestScheduler,
    background_enabled: bool,
) -> tuple[bool, bool]:
    cancelled = False
    while True:
        try:
            command = command_queue.get_nowait()
        except Empty:
            break
        background_enabled, command_cancelled = _handle_command(
            command,
            scheduler,
            background_enabled,
        )
        cancelled = cancelled or command_cancelled
    return background_enabled, cancelled


def run_code_index_loop(
    builder: LazyCodeIndexBuilder,
    command_queue,
    result_queue,
    stop_requested: Callable[[], bool],
) -> None:
    """Run one stateful lazy builder without sharing Python CPU time with Qt."""

    scheduler = IndexRequestScheduler()
    background_enabled = False
    cancelled = False
    _set_process_priority(_IDLE_PRIORITY_CLASS)
    while not cancelled and not stop_requested():
        background_enabled, command_cancelled = _drain_commands(
            command_queue,
            scheduler,
            background_enabled,
        )
        cancelled = cancelled or command_cancelled
        if cancelled or stop_requested():
            break

        request = scheduler.take()
        if request is None and not background_enabled:
            try:
                command = command_queue.get(timeout=0.05)
            except Empty:
                continue
            background_enabled, command_cancelled = _handle_command(
                command,
                scheduler,
                background_enabled,
            )
            cancelled = cancelled or command_cancelled
            continue

        labels_ready: tuple[str, ...] = ()
        if request is not None:
            priority, generation, labels = request

            def request_cancelled() -> bool:
                nonlocal background_enabled, cancelled
                background_enabled, command_cancelled = _drain_commands(
                    command_queue,
                    scheduler,
                    background_enabled,
                )
                cancelled = cancelled or command_cancelled
                return (
                    cancelled
                    or stop_requested()
                    or scheduler.supersedes(priority, generation)
                )

            index = builder.analyze_labels(labels, cancelled=request_cancelled)
            if not request_cancelled():
                labels_ready = labels
        else:

            def background_cancelled() -> bool:
                nonlocal background_enabled, cancelled
                background_enabled, command_cancelled = _drain_commands(
                    command_queue,
                    scheduler,
                    background_enabled,
                )
                cancelled = cancelled or command_cancelled
                return (
                    cancelled
                    or stop_requested()
                    or scheduler.pending
                    or not background_enabled
                )

            # Small batches bound IPC and main-thread merge work. Background
            # scanning is paused as soon as interaction or a priority request arrives.
            index = builder.analyze_next_batch(2, cancelled=background_cancelled)

        progress = builder.progress
        if not index.is_empty:
            result_queue.put(("partial", index, progress))
        if labels_ready:
            result_queue.put(("labels_ready", labels_ready))
        if progress.complete:
            result_queue.put(("finished",))
            return


def code_index_process_main(
    game_root: str,
    project_root: str,
    vanilla_project_name: str,
    command_queue,
    result_queue,
    stop_event,
) -> None:
    builder = LazyCodeIndexBuilder(
        Path(game_root),
        Path(project_root),
        vanilla_project_name=vanilla_project_name,
    )
    try:
        run_code_index_loop(builder, command_queue, result_queue, stop_event.is_set)
    except Exception as exc:
        result_queue.put(("failed", type(exc).__name__, str(exc)))
    finally:
        builder.close()
