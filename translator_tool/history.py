from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class UnitChange:
    uid: str
    before: str
    after: str
    before_deleted: bool = False
    after_deleted: bool = False


@dataclass(frozen=True)
class TranslationOperation:
    """One user-visible edit, possibly affecting more than one translation unit."""

    label: str
    changes: tuple[UnitChange, ...]

    @property
    def is_empty(self) -> bool:
        return not self.changes


class OperationHistory:
    """Application-level history that deliberately never belongs to a text widget."""

    DEFAULT_MAX_OPERATIONS = 500
    DEFAULT_MAX_CHANGES = 200_000
    DEFAULT_MAX_TEXT_CHARS = 20_000_000

    def __init__(
        self,
        *,
        max_operations: int = DEFAULT_MAX_OPERATIONS,
        max_changes: int = DEFAULT_MAX_CHANGES,
        max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
    ) -> None:
        if min(max_operations, max_changes, max_text_chars) <= 0:
            raise ValueError("history limits must be positive")
        self.max_operations = max_operations
        self.max_changes = max_changes
        self.max_text_chars = max_text_chars
        self._undo: list[TranslationOperation] = []
        self._redo: list[TranslationOperation] = []
        self._undo_changes = 0
        self._undo_text_chars = 0
        self._redo_changes = 0
        self._redo_text_chars = 0

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def push(self, operation: TranslationOperation) -> None:
        if operation.is_empty:
            return
        self._undo.append(operation)
        changes, text_chars = _operation_cost(operation)
        self._undo_changes += changes
        self._undo_text_chars += text_chars
        self._redo.clear()
        self._redo_changes = 0
        self._redo_text_chars = 0
        while len(self._undo) > 1 and (
            len(self._undo) > self.max_operations
            or self._undo_changes > self.max_changes
            or self._undo_text_chars > self.max_text_chars
        ):
            removed = self._undo.pop(0)
            changes, text_chars = _operation_cost(removed)
            self._undo_changes -= changes
            self._undo_text_chars -= text_chars

    def undo(self, apply: Callable[[str, str, bool], None]) -> TranslationOperation | None:
        operation = self.take_undo()
        if operation is None:
            return None
        for change in operation.changes:
            apply(change.uid, change.before, change.before_deleted)
        return operation

    def redo(self, apply: Callable[[str, str, bool], None]) -> TranslationOperation | None:
        operation = self.take_redo()
        if operation is None:
            return None
        for change in operation.changes:
            apply(change.uid, change.after, change.after_deleted)
        return operation

    def take_undo(self) -> TranslationOperation | None:
        if not self._undo:
            return None
        operation = self._undo.pop()
        changes, text_chars = _operation_cost(operation)
        self._undo_changes -= changes
        self._undo_text_chars -= text_chars
        self._redo.append(operation)
        self._redo_changes += changes
        self._redo_text_chars += text_chars
        return operation

    def take_redo(self) -> TranslationOperation | None:
        if not self._redo:
            return None
        operation = self._redo.pop()
        changes, text_chars = _operation_cost(operation)
        self._redo_changes -= changes
        self._redo_text_chars -= text_chars
        self._undo.append(operation)
        self._undo_changes += changes
        self._undo_text_chars += text_chars
        return operation

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._undo_changes = 0
        self._undo_text_chars = 0
        self._redo_changes = 0
        self._redo_text_chars = 0


def _operation_cost(operation: TranslationOperation) -> tuple[int, int]:
    return (
        len(operation.changes),
        sum(len(change.before) + len(change.after) for change in operation.changes),
    )
