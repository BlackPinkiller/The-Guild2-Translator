from __future__ import annotations

from pathlib import Path

from ..code_index import CodeReference
from ..preview import GameLocalization, PreviewService


def assert_preview_localization_fallback_keeps_selection_nonblocking() -> None:
    service = PreviewService(Path("missing-game"), "#chinese")
    service.set_project_localization(
        {"_PROMPT_+0": "Choose a name"},
        {"_PROMPT_+0": "选择一个名字"},
    )
    original_read_labels = GameLocalization._read_labels
    try:
        GameLocalization._read_labels = staticmethod(
            lambda _path: (_ for _ in ()).throw(
                AssertionError("a row selection synchronously read game localization")
            )
        )
        service.use_project_localization_fallback()
        reference = CodeReference(
            "body_+0",
            Path("Fixture.lua"),
            1,
            1,
            "MsgQuick",
            1,
            runtime_arguments=("Prompt",),
            runtime_argument_values=(("@L_PROMPT_+0",),),
            runtime_argument_kinds=(("label",),),
            role="body",
        )
        rendered = service.render(
            "%1l",
            unit_key="body",
            label="_BODY_+0",
            file_rel="Text.dbt",
            kind="dbt",
            target=True,
            references=(reference,),
        ).display_text
        if rendered != "选择一个名字":
            raise AssertionError(f"the immediate project fallback changed preview output: {rendered!r}")
        covered_game = GameLocalization(
            Path("covered-game"),
            "#chinese",
            {"_PROMPT_+0": "Choose a name"},
            {"_PROMPT_+0": "选择一个名字"},
            load_game_labels=False,
        )
        if covered_game.resolve_label("_PROMPT_+0", True) != "选择一个名字":
            raise AssertionError(
                "covered Vanilla labels changed when redundant game DB reads were skipped"
            )
    finally:
        GameLocalization._read_labels = staticmethod(original_read_labels)

    game_root, language, source, target, revision = service.localization_load_spec()
    prepared = GameLocalization(game_root, language, source, target)
    service.update_project_localization("_PROMPT_+0", "Choose", "请选择")
    if service.install_background_localization(prepared, revision):
        raise AssertionError("stale background localization replaced a newer editor value")

    font_scans = 0

    def count_font_scan(_target: bool) -> tuple[Path, ...]:
        nonlocal font_scans
        font_scans += 1
        return ()

    service = PreviewService(None, "#chinese")
    service._standard_font_files = count_font_scan  # type: ignore[method-assign]
    for number in range(3):
        service.render(
            f"plain text {number}",
            unit_key=f"plain-{number}",
            file_rel="Text.dbt",
            kind="dbt",
            target=False,
        )
    if font_scans != 1:
        raise AssertionError(f"row previews repeatedly rescanned the game font directory: {font_scans}")


def assert_project_localization_updates_invalidate_placeholder_previews() -> None:
    service = PreviewService(None, "#chinese")
    service.set_project_localization_entries(
        (
            ("tooltip-title", "ALCHEMIST", "Alchemist", "炼金术士"),
            ("tooltip-description", "ALCHEMIST", "A scholar.", "一位学者。"),
        )
    )
    if service.localization.resolve_label("ALCHEMIST", True) != "炼金术士":
        raise AssertionError("a duplicate tooltip description replaced the deterministic title owner")
    service.update_project_localization(
        "ALCHEMIST",
        "A scholar.",
        "编辑后的说明。",
        unit_key="tooltip-description",
    )
    if service.localization.resolve_label("ALCHEMIST", True) != "炼金术士":
        raise AssertionError("editing a duplicate tooltip field changed placeholder label meaning")

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

    service.set_project_localization(
        {"Hostility": "Hostility", "Neutral": "Neutral"},
        {"Hostility": "\u654c\u5bf9", "Neutral": "\u4e2d\u7acb"},
    )
    compact_reference = CodeReference(
        "diplomatic_state_changed",
        Path("Diplomacy.lua"),
        197,
        1,
        "MsgNewsNoWait",
        6,
        role="body",
        runtime_arguments=("StatusLabel", "CurrentLabel"),
        runtime_argument_values=(("@LHostility",), ("@LNeutral",)),
        runtime_argument_kinds=(("label",), ("label",)),
    )
    compact_labels = service.render(
        "%1l|%2l",
        unit_key="diplomatic-state-changed",
        label="DIPLOMATIC_STATE_CHANGED",
        file_rel="Text.dbt",
        kind="dbt",
        target=True,
        references=(compact_reference,),
    ).display_text
    if compact_labels != "\u654c\u5bf9|\u4e2d\u7acb":
        raise AssertionError(f"compact runtime localization labels stayed generic: {compact_labels!r}")

    service.set_project_localization(
        {
            "_HPFZ_KATASTR_KRANK_NAM_+0": "Sprain",
            "_HPFZ_KATASTR_KRANK_NAM_+1": "Cold",
        },
        {
            "_HPFZ_KATASTR_KRANK_NAM_+0": "\u626d\u4f24",
            "_HPFZ_KATASTR_KRANK_NAM_+1": "\u611f\u5192",
        },
    )
    raw_label_reference = CodeReference(
        "hpfz_katastr_sick_body_+0",
        Path("state_sick.lua"),
        48,
        1,
        "feedback_MessageCharacter",
        2,
        role="body",
        runtime_arguments=('GetID("")', "Label"),
        runtime_argument_values=(
            ("",),
            ("", "HPFZ_KATASTR_KRANK_NAM_+0", "HPFZ_KATASTR_KRANK_NAM_+1"),
        ),
        runtime_argument_kinds=(
            ("character",),
            ("structure", "text", "text"),
        ),
    )
    raw_labels = service.render(
        "%2l",
        unit_key="sick-body",
        label="_HPFZ_KATASTR_SICK_BODY_+0",
        file_rel="Text.dbt",
        kind="dbt",
        target=True,
        references=(raw_label_reference,),
    ).display_text
    if raw_labels != "\u626d\u4f24":
        raise AssertionError(f"suffix-bearing runtime labels stayed generic: {raw_labels!r}")

    service.set_project_localization(
        {
            "Hostility": "Hostility",
            "Neutral": "Neutral",
            "NAP": "Non-aggression pact",
            "Alliance": "Alliance",
        },
        {
            "Hostility": "\u4e16\u4ec7",
            "Neutral": "\u4e2d\u7acb",
            "NAP": "\u4e92\u4e0d\u4fb5\u72af",
            "Alliance": "\u540c\u76df",
        },
    )
    relationship_reference = CodeReference(
        "measure_administrate_diplomacy_special_body_+0",
        Path("ms_047_AdministrateDiplomacy.lua"),
        1594,
        1,
        "MsgBoxNoWait",
        4,
        role="body",
        runtime_arguments=("Unused",) * 10 + ("Label",),
        runtime_argument_values=((),) * 10
        + (("", "@LHostility", "@LNeutral", "@LNAP", "@LAlliance"),),
        runtime_argument_kinds=((),) * 10
        + (("structure", "label", "label", "label", "label"),),
    )
    relationship = service.render(
        "\u72b6\u6001\uff1a%11l",
        unit_key="relationship-overview",
        label="_MEASURE_ADMINISTRATE_DIPLOMACY_SPECIAL_BODY_+0",
        file_rel="Text.dbt",
        kind="dbt",
        target=True,
        references=(relationship_reference,),
    ).display_text
    if relationship != "\u72b6\u6001\uff1a\u4e16\u4ec7":
        raise AssertionError(
            f"mutually exclusive relationship labels leaked into the game preview: {relationship!r}"
        )

    service.set_project_localization(
        {
            "_OTHER_ROLE_+0": "Other role",
            "_CHARACTERS_2_PROFESSIONS_skulldude_NAME_+0": "Purveyor",
            "_CHARACTERS_2_PROFESSIONS_guildclerk_NAME_+0": "Guild clerk",
        },
        {
            "_OTHER_ROLE_+0": "\u5176\u4ed6\u804c\u4f4d",
            "_CHARACTERS_2_PROFESSIONS_skulldude_NAME_+0": "\u91c7\u529e\u4eba",
            "_CHARACTERS_2_PROFESSIONS_guildclerk_NAME_+0": "\u884c\u4f1a\u6587\u4e66",
        },
    )
    profession_reference = CodeReference(
        "_characters_2_professions_*_name_+*",
        Path("chr.lua"),
        624,
        1,
        "MsgQuick",
        2,
        role="runtime_label",
        runtime_arguments=("Other", "Label"),
        runtime_argument_values=(
            ("_OTHER_ROLE_+0",),
            (
                "_CHARACTERS_2_PROFESSIONS_skulldude_NAME_+0",
                "_CHARACTERS_2_PROFESSIONS_guildclerk_NAME_+0",
            ),
        ),
        runtime_argument_kinds=(("label",), ("label", "label")),
    )

    def render_profession(selected_label: str) -> str:
        return service.render(
            "%1l|%2l",
            unit_key="general-measures-failures-12",
            label="_GENERAL_MEASURES_FAILURES_+12",
            file_rel="Text.dbt",
            kind="dbt",
            target=True,
            references=(profession_reference,),
            selected_label=selected_label,
        ).display_text

    if render_profession("_CHARACTERS_2_PROFESSIONS_skulldude_NAME_+0") != "\u5176\u4ed6\u804c\u4f4d|\u91c7\u529e\u4eba":
        raise AssertionError("a selected runtime label was not bound to its proven placeholder slot")
    if render_profession("_CHARACTERS_2_PROFESSIONS_guildclerk_NAME_+0") != "\u5176\u4ed6\u804c\u4f4d|\u884c\u4f1a\u6587\u4e66":
        raise AssertionError("the preview cache ignored the selected cross-entry runtime label")


def assert_editor_changes_reach_preview_localization() -> None:
    from types import SimpleNamespace

    from ..app import TranslatorWindow

    updates: list[tuple[str, str, str]] = []
    window = SimpleNamespace(
        preview_service=SimpleNamespace(
            update_project_localization=lambda label, source, target, **_kwargs: updates.append(
                (label, source, target)
            )
        ),
        _game_preview_cache={"old": object()},
    )
    unit = SimpleNamespace(
        uid="prompt-daughter",
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

    rendered_labels: list[tuple[str, str]] = []
    selected = SimpleNamespace(
        uid="skulldude",
        label="_CHARACTERS_2_PROFESSIONS_skulldude_NAME_+0",
        file_rel="Text.dbt",
        source_text="Purveyor",
        current_text="\u91c7\u529e\u4eba",
        ref=SimpleNamespace(kind="dbt"),
    )
    body = SimpleNamespace(
        uid="hire-failure",
        label="_GENERAL_MEASURES_FAILURES_+12",
        file_rel="Text.dbt",
        source_text="Employment not possible - you cannot hire a woman as %1l.",
        current_text="\u65e0\u6cd5\u96c7\u7528 - \u4f60\u4e0d\u80fd\u96c7\u7528\u5973\u6027\u62c5\u4efb%1l\u3002",
        ref=SimpleNamespace(kind="dbt"),
    )
    context_reference = CodeReference(
        "_characters_2_professions_*_name_+*",
        Path("chr.lua"),
        624,
        1,
        "MsgQuick",
        2,
        role="runtime_label",
    )

    class PreviewStub:
        @staticmethod
        def render(_text: str, **kwargs: object) -> object:
            rendered_labels.append((str(kwargs["label"]), str(kwargs["selected_label"])))
            return object()

        @staticmethod
        def game_window_image(*_args: object, **_kwargs: object) -> object:
            return object()

    preview_window = SimpleNamespace(
        _current_unit=lambda: selected,
        _game_preview_parts=lambda _unit: (None, None, body, (), (context_reference,)),
        _game_preview_cache={},
        _code_references_for_unit=lambda _unit: (),
        preview_service=PreviewStub(),
        settings=SimpleNamespace(preview_window_scale_percent=100),
    )
    TranslatorWindow._game_preview_image(preview_window, True)
    if rendered_labels != [(body.label, selected.label)]:
        raise AssertionError(
            "a cross-entry window body lost the label whose runtime value it previews: "
            f"{rendered_labels!r}"
        )
