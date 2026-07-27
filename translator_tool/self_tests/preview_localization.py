from __future__ import annotations

from pathlib import Path

from ..code_index import CodeReference
from ..preview import PreviewService


def assert_project_localization_updates_invalidate_placeholder_previews() -> None:
    service = PreviewService(None, "#chinese")
    service.set_project_localization(
        {"_PROMPT_DAUGHTER_+0": "What do you want to name your daughter?"},
        {"_PROMPT_DAUGHTER_+0": "你想为你的女儿取什么名字？"},
    )
    reference = CodeReference(
        "birth_body_daughter_+0",
        Path("Birth.lua"),
        10,
        1,
        "MsgQuick",
        1,
        runtime_arguments=("Child", "Prompt"),
        runtime_argument_values=((), ("@L_PROMPT_DAUGHTER_+0",)),
        runtime_argument_kinds=((), ("label",)),
        role="body",
    )

    def render() -> str:
        return service.render(
            "%2l",
            unit_key="birth-body",
            label="_BIRTH_BODY_DAUGHTER_+0",
            file_rel="Text.dbt",
            kind="dbt",
            target=True,
            references=(reference,),
        ).display_text

    if render() != "你想为你的女儿取什么名字？":
        raise AssertionError("project localization did not override the installed game text")
    service.update_project_localization(
        "_PROMPT_DAUGHTER_+0",
        "What do you want to name your daughter?",
        "你想给你的女儿取什么名字？",
    )
    if render() != "你想给你的女儿取什么名字？":
        raise AssertionError("a cached placeholder preview ignored the edited project text")


def assert_editor_changes_reach_preview_localization() -> None:
    from types import SimpleNamespace

    from ..app import TranslatorWindow

    updates: list[tuple[str, str, str]] = []
    window = SimpleNamespace(
        preview_service=SimpleNamespace(
            update_project_localization=lambda label, source, target: updates.append(
                (label, source, target)
            )
        ),
        _game_preview_cache={"old": object()},
    )
    unit = SimpleNamespace(
        label="_PROMPT_DAUGHTER_+0",
        source_text="What do you want to name your daughter?",
        current_text="你想给你的女儿取什么名字？",
    )
    TranslatorWindow._update_preview_localization(window, (unit,))
    if updates != [
        (
            "_PROMPT_DAUGHTER_+0",
            "What do you want to name your daughter?",
            "你想给你的女儿取什么名字？",
        )
    ]:
        raise AssertionError("the authoritative editor state did not reach localization")
    if window._game_preview_cache:
        raise AssertionError("an editor localization change left a stale game preview cache")
