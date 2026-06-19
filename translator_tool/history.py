from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class UnitChange:
    uid: str
    before: str
    after: str


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

    def __init__(self) -> None:
        self._undo: list[TranslationOperation] = []
        self._redo: list[TranslationOperation] = []

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
        self._redo.clear()

    def undo(self, apply: Callable[[str, str], None]) -> TranslationOperation | None:
        if not self._undo:
            return None
        operation = self._undo.pop()
        for change in operation.changes:
            apply(change.uid, change.before)
        self._redo.append(operation)
        return operation

    def redo(self, apply: Callable[[str, str], None]) -> TranslationOperation | None:
        if not self._redo:
            return None
        operation = self._redo.pop()
        for change in operation.changes:
            apply(change.uid, change.after)
        self._undo.append(operation)
        return operation

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
