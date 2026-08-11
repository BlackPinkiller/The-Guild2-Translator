from __future__ import annotations

from pathlib import Path

import tempfile

from ..code_index import CodeFileSpec, CodeReference, index_code_file
from ..code_window_context import PreviewWindowButton, window_context_for_reference
from ..preview_context_selection import rank_preview_references, select_preview_context


def assert_preview_context_selection_keeps_arguments_and_style_coherent() -> None:
    quick = CodeReference(
        "same_body_+0",
        Path("Quick.lua"),
        10,
        1,
        "MsgQuick",
        1,
        ('""', '"@L_SAME_BODY_+0"', 'GetID("Owner")'),
        role="body",
        runtime_arguments=('GetID("Owner")',),
        confidence=100,
    )
    message = CodeReference(
        "same_body_+0",
        Path("Message.lua"),
        20,
        1,
        "MsgBox",
        4,
        (
            '""',
            '""',
            '"@B[1,@L_SAME_BUTTON_+0]"',
            '"@L_SAME_HEAD_+0"',
            '"@L_SAME_BODY_+0"',
            'GetID("Owner")',
            "ItemLabel[item1]",
        ),
        role="body",
        runtime_arguments=('GetID("Owner")', "ItemLabel[item1]"),
        confidence=100,
    )
    selection = select_preview_context(
        "%1SN found %2l",
        (quick, message),
        "SAME_BODY_+0",
    )
    if selection.references != (message,):
        raise AssertionError(f"placeholder evidence and window style chose different calls: {selection!r}")
    if selection.window is None or selection.window.kind != "message":
        raise AssertionError(f"the chosen MsgBox did not drive the preview style: {selection.window!r}")
    if selection.window.header_label != "same_head_+0" or selection.window.body_label != "same_body_+0":
        raise AssertionError(f"the chosen call lost its header/body structure: {selection.window!r}")
    if not selection.window.buttons or selection.window.buttons[0].label != "same_button_+0":
        raise AssertionError(f"the chosen call lost its button structure: {selection.window!r}")
    if rank_preview_references("%1SN found %2l", (quick, message), "SAME_BODY_+0")[0] != message:
        raise AssertionError("the source-code candidate order disagreed with the preview selection")

    branch_reference = CodeReference(
        "branch_head_+0",
        Path("Branches.lua"),
        25,
        1,
        "MsgBox",
        3,
        ('""', '""', "Buttons", '"@L_BRANCH_HEAD_+0"', '"@L_BRANCH_BODY_+0"'),
        role="header",
        resolved_arguments=(
            ("",),
            ("",),
            (
                "@P@B[1,1]@B[0,@LBack_+0]",
                "@P@B[5,5]@B[0,@LBack_+0]",
                "@P@B[10,10]@B[0,@LBack_+0]",
            ),
            ("@L_BRANCH_HEAD_+0",),
            ("@L_BRANCH_BODY_+0",),
        ),
        confidence=100,
    )
    branch_context = window_context_for_reference(branch_reference, "BRANCH_HEAD_+0")
    if branch_context is None or tuple(
        (button.identifier, button.text, button.label)
        for button in branch_context.buttons
    ) != (("1", "1", ""), ("0", "", "back_+0")):
        raise AssertionError(
            "mutually exclusive runtime button branches were merged into one oversized preview: "
            f"{branch_context!r}"
        )
    unresolved_reference = CodeReference(
        "current_button_+0",
        Path("Branches.lua"),
        26,
        1,
        "MsgBox",
        2,
        ('""', '""', '"@P@B[9,*]@B[0,@LBack_+0]"', '"@L_BRANCH_HEAD_+0"', '"@L_BRANCH_BODY_+0"'),
        role="button",
        confidence=88,
    )
    unresolved_context = window_context_for_reference(
        unresolved_reference,
        "CURRENT_BUTTON_+0",
    )
    if unresolved_context is None or tuple(
        (button.identifier, button.label)
        for button in unresolved_context.buttons
    ) != (("9", "current_button_+0"), ("0", "back_+0")):
        raise AssertionError(
            "a resolved dynamic button label was appended beside its unresolved slot: "
            f"{unresolved_context!r}"
        )

    scheduled = CodeReference(
        "family_school_head_+*",
        Path("School.lua"),
        30,
        1,
        "feedback_MessageSchedule",
        1,
        (
            '""',
            '"@L_FAMILY_SCHOOL_HEAD"',
            '"@L_FAMILY_SCHOOL_BODY"',
            'GetID("")',
            "Money",
        ),
        role="header",
        runtime_arguments=('GetID("")', "Money"),
        resolved_arguments=(
            ("",),
            ("@L_family_school_head_+*",),
            ("@L_family_school_body_+*",),
            (),
            (),
        ),
        match_kind="family",
        confidence=88,
    )
    scheduled_selection = select_preview_context(
        "School",
        (scheduled,),
        "FAMILY_SCHOOL_HEAD_+0",
    )
    if (
        scheduled_selection.window is None
        or scheduled_selection.window.header_label != "family_school_head_+0"
        or scheduled_selection.window.body_label != "family_school_body_+0"
    ):
        raise AssertionError(
            "a suffix-free code family did not keep its scheduled header/body pair"
        )

    with tempfile.TemporaryDirectory(prefix="translator_questbook_context_") as directory:
        script = Path(directory) / "Mission.lua"
        script.write_text(
            'SetMainQuestTitle("MAIN_MISSION", "@L_QUEST_HEADER_+0", "@L_QUEST_NAME_+0")\n'
            'SetMainQuestDescription("MAIN_MISSION", "@L_QUEST_BODY_+0", Goal)\n',
            encoding="utf-8",
        )
        index = index_code_file(CodeFileSpec(script, "project"))
        body_reference = index.references_for("QUEST_BODY_+0").active[0]
        questbook = select_preview_context(
            "Complete %1i tasks.",
            (body_reference,),
            "QUEST_BODY_+0",
        )
        if questbook.window is None or questbook.window.kind != "questbook":
            raise AssertionError(f"main quest description lost its QuestbookSheet: {questbook!r}")
        if (
            questbook.window.header_label != "quest_header_+0"
            or questbook.window.body_label != "quest_body_+0"
        ):
            raise AssertionError(f"quest title and description were not joined by quest id: {questbook!r}")
        if len(questbook.references) != 2:
            raise AssertionError(f"questbook preview lost its title placeholder evidence: {questbook!r}")


def assert_preview_context_selection_understands_returned_label_roles() -> None:
    returned = CodeReference(
        "remote_body_+*",
        Path("Caller.lua"),
        30,
        1,
        "MsgSay",
        1,
        ('""', "talk_RemoteBody(Variant)"),
        role="body",
        confidence=62,
        match_kind="dynamic",
    )
    selection = select_preview_context(
        "A returned sentence",
        (returned,),
        "REMOTE_BODY_+2",
    )
    if selection.window is None:
        raise AssertionError("a returned body label did not inherit its final UI call style")
    if (
        selection.window.kind != "short"
        or selection.window.background != "overlay"
        or selection.window.gui_resource != "GUI/Hud/panel_dialog.gui"
    ):
        raise AssertionError(f"a returned MsgSay label got the wrong presentation: {selection.window!r}")
    if selection.window.body_label != "remote_body_+2":
        raise AssertionError(f"the returned label was not attached to the body slot: {selection.window!r}")


def assert_preview_context_selection_keeps_the_current_dynamic_branch() -> None:
    reference = CodeReference(
        "messages_slander_speech_theft_+0",
        Path("Slander.lua"),
        137,
        16,
        "MsgSay",
        1,
        ('"Bard"', '"@L_MESSAGES_SLANDER_SPEECH_"..EvidenceLabel.."_+0"', 'GetID("Destination")'),
        role="body",
        runtime_arguments=('GetID("Destination")',),
        resolved_arguments=(
            ("Bard",),
            (
                "@L_MESSAGES_SLANDER_SPEECH_INTRO_+0",
                "@L_MESSAGES_SLANDER_SPEECH_THEFT_+0",
                "@L_MESSAGES_SLANDER_SPEECH_MURDER_+0",
            ),
            (),
        ),
        match_kind="dynamic",
        confidence=78,
    )
    selection = select_preview_context(
        "%1ST %1SA %1SV is stealing again.",
        (reference,),
        "_MESSAGES_SLANDER_SPEECH_THEFT_+0",
    )
    if selection.window is None or selection.window.call_name != "msgsay":
        raise AssertionError(f"a concrete dynamic MsgSay branch lost its dialog style: {selection!r}")
    if selection.window.body_label != "messages_slander_speech_theft_+0":
        raise AssertionError(f"a dynamic call selected a sibling branch as its body: {selection.window!r}")


def assert_preview_context_selection_prefers_displayed_runtime_labels() -> None:
    broad_profession_family = CodeReference(
        "_characters_2_professions_*_name_+*",
        Path("chr.lua"),
        624,
        1,
        "MsgQuick",
        2,
        ('"Building"', '"@L_GENERAL_MEASURES_FAILURES_+12"', "Label"),
        role="runtime_label",
        runtime_arguments=("Label",),
        runtime_argument_values=(("_CHARACTERS_2_PROFESSIONS_*_NAME_+*",),),
        runtime_argument_kinds=(("label",),),
        confidence=100,
    )
    broad_selection = select_preview_context(
        "Purveyor",
        (broad_profession_family,),
        "_CHARACTERS_2_PROFESSIONS_skulldude_NAME_+0",
    )
    if broad_selection.window is not None:
        raise AssertionError(
            "a family-only runtime label was presented as proof of one concrete profession: "
            f"{broad_selection!r}"
        )

    init_data = CodeReference(
        "law_level_+0",
        Path("Law.lua"),
        10,
        1,
        "InitData",
        5,
        ('"Law"', '""', '"@L_LAW_HEAD_+0"', '"@L_LAW_BODY_+0"', "City", "Severity"),
        role="runtime_label",
        runtime_arguments=("City", "Severity"),
        confidence=100,
    )
    news = CodeReference(
        "law_level_+0",
        Path("Law.lua"),
        30,
        1,
        "MsgNewsNoWait",
        9,
        (
            '""',
            '""',
            '""',
            '""',
            '""',
            '"@L_LAW_HEAD_+0"',
            '"@L_LAW_BODY_+0"',
            "Actor",
            "City",
            "Severity",
        ),
        role="runtime_label",
        runtime_arguments=("Actor", "City", "Severity"),
        confidence=100,
    )
    selection = select_preview_context("liberal", (init_data, news), "LAW_LEVEL_+0")
    if selection.references != (news,) or selection.window is None:
        raise AssertionError(f"a setup call outranked the real runtime display call: {selection!r}")
    if selection.window.kind != "news":
        raise AssertionError(f"a displayed runtime label got the wrong window style: {selection.window!r}")
    if selection.window.body_label != "law_body_+0":
        raise AssertionError("the runtime label incorrectly replaced the actual window body")
    if selection.window.argument_labels != ("law_level_+0",):
        raise AssertionError(f"runtime-label membership was not retained: {selection.window!r}")

    displayed_init_data = CodeReference(
        "law_level_+0",
        Path("Law.lua"),
        9,
        1,
        "InitData",
        5,
        (
            '"@P@B[0,@L_LAW_LEVEL_+0,@L_LAW_LEVEL_+0,Hud/Buttons/free.tga]"',
            '""',
            '"@L_LAW_HEAD_+0"',
            '"@L_LAW_BODY_+0"',
            "City",
            "Severity",
        ),
        role="runtime_label",
        runtime_arguments=("City", "Severity"),
        confidence=100,
    )
    displayed_selection = select_preview_context(
        "liberal",
        (displayed_init_data, news),
        "LAW_LEVEL_+0",
    )
    if (
        displayed_selection.references != (displayed_init_data,)
        or displayed_selection.window is None
        or displayed_selection.window.kind != "measure_choice"
        or displayed_selection.window.argument_labels != ("law_level_+0",)
    ):
        raise AssertionError(
            "a label visibly occupying a choice button lost to an indirect runtime use: "
            f"{displayed_selection!r}"
        )

    obsolete_body = CodeReference(
        "law_body_+0",
        Path("Law.lua"),
        9,
        1,
        "InitData",
        3,
        displayed_init_data.arguments,
        role="body",
        runtime_arguments=("City", "Severity"),
        confidence=100,
    )
    obsolete_selection = select_preview_context(
        "Current law: %2l",
        (obsolete_body,),
        "LAW_BODY_+0",
    )
    if obsolete_selection.references != (obsolete_body,) or obsolete_selection.window is not None:
        raise AssertionError(
            "InitData's obsolete body slot hid the selected entry inside a window that cannot draw it: "
            f"{obsolete_selection!r}"
        )


def assert_game_preview_parts_use_the_selected_call_site() -> None:
    from types import SimpleNamespace

    from ..app import TranslatorWindow

    body = SimpleNamespace(
        label="SAME_BODY_+0",
        source_text="%1SN found %2l",
        file_rel="Text.dbt",
    )
    header = SimpleNamespace(label="SAME_HEAD_+0", source_text="Headline", file_rel="Text.dbt")
    button = SimpleNamespace(label="SAME_BUTTON_+0", source_text="Continue", file_rel="Text.dbt")
    quick = CodeReference(
        "same_body_+0",
        Path("Quick.lua"),
        10,
        1,
        "MsgQuick",
        1,
        ('""', '"@L_SAME_BODY_+0"', 'GetID("Owner")'),
        role="body",
        runtime_arguments=('GetID("Owner")',),
        confidence=100,
    )
    message = CodeReference(
        "same_body_+0",
        Path("Message.lua"),
        20,
        1,
        "MsgBox",
        4,
        (
            '""',
            '""',
            '"@B[1,@L_SAME_BUTTON_+0]"',
            '"@L_SAME_HEAD_+0"',
            '"@L_SAME_BODY_+0"',
            'GetID("Owner")',
            "ItemLabel[item1]",
        ),
        role="body",
        runtime_arguments=('GetID("Owner")', "ItemLabel[item1]"),
        confidence=100,
    )
    units = {
        "same_head_+0": header,
        "same_body_+0": body,
        "same_button_+0": button,
    }
    window = SimpleNamespace(
        _code_references_for_unit=lambda _unit: (quick, message),
        _unit_for_context_label=lambda _unit, label: units.get(label.lstrip("_").casefold()),
        _paired_preview_units=lambda _unit: (None, body),
    )
    context, selected_header, selected_body, buttons, references = TranslatorWindow._game_preview_parts(
        window,
        body,
    )
    if references != (message,):
        raise AssertionError("game-window assembly did not keep the authoritative selected call")
    if context is None or context.kind != "message":
        raise AssertionError(f"game-window assembly used a different style decision: {context!r}")
    if selected_header is not header or selected_body is not body or buttons != (button,):
        raise AssertionError("game-window assembly did not use the selected call structure")

    return_value_reference = CodeReference(
        "same_body_+0",
        Path("ReturnValues.lua"),
        30,
        1,
        "MsgBox",
        4,
        (
            '""',
            '""',
            '"@P@B[M,@L_INTERFACE_BUTTONS_ENDGAME]@B[S,@L_INTERFACE_BUTTONS_STATISTICS]"',
            '"@L_SAME_HEAD_+0"',
            '"@L_SAME_BODY_+0"',
        ),
        role="body",
        confidence=100,
    )
    fallback_window = SimpleNamespace(
        _code_references_for_unit=lambda _unit: (return_value_reference,),
        _unit_for_context_label=lambda _unit, label: (
            header if label == "same_head_+0" else body if label == "same_body_+0" else None
        ),
        _paired_preview_units=lambda _unit: (None, body),
    )
    _context, _header, _body, fallback_buttons, _references = (
        TranslatorWindow._game_preview_parts(fallback_window, body)
    )
    if (
        len(fallback_buttons) != 2
        or not all(isinstance(candidate, PreviewWindowButton) for candidate in fallback_buttons)
        or tuple(candidate.identifier for candidate in fallback_buttons) != ("M", "S")
        or any(isinstance(candidate, str) for candidate in fallback_buttons)
    ):
        raise AssertionError(
            "unresolved button captions fell back to their M/S return identifiers: "
            f"{fallback_buttons!r}"
        )

    sibling = SimpleNamespace(label="MISSING_PAIR_+1", file_rel="Text.dbt")
    lookup_window = SimpleNamespace(model=SimpleNamespace(units=(sibling,)))
    if TranslatorWindow._unit_for_normalized_label(
        lookup_window,
        "Text.dbt",
        "MISSING_PAIR_+0",
    ) is not None:
        raise AssertionError("a missing concrete cross-entry label borrowed text from a sibling suffix")
    if TranslatorWindow._unit_for_normalized_label(
        lookup_window,
        "Text.dbt",
        "MISSING_PAIR_+*",
    ) is not sibling:
        raise AssertionError("an explicit wildcard cross-entry label no longer resolves a representative branch")
    default = SimpleNamespace(label="DEFAULT_PAIR_+0", file_rel="Text.dbt")
    lookup_window.model.units = (sibling, default)
    if TranslatorWindow._unit_for_normalized_label(
        lookup_window,
        "Text.dbt",
        "DEFAULT_PAIR",
    ) is not default:
        raise AssertionError("a suffix-free cross-entry label did not resolve its explicit default +0 entry")

    upper = SimpleNamespace(label="_UPGRADE_WeaponRack_TOOLTIP_+0", file_rel="Text.dbt")
    lower = SimpleNamespace(label="_UPGRADE_Weaponrack_TOOLTIP_+0", file_rel="Text.dbt")
    if TranslatorWindow._preview_unit_for_labels(
        (upper, lower),
        "Text.dbt",
        ("_UPGRADE_Weaponrack_TOOLTIP_+0",),
    ) is not lower:
        raise AssertionError("exact-case NAME/TOOLTIP pairing lost to a case-fold collision")
    if TranslatorWindow._preview_unit_for_labels(
        (upper, lower),
        "Text.dbt",
        ("_upgrade_weaponrack_tooltip_+0",),
    ) is not None:
        raise AssertionError("an ambiguous case-fold fallback silently chose the wrong entry")

    row_key = (331, "ALCHEMIST")
    title = SimpleNamespace(
        label="ALCHEMIST",
        file_rel="Tooltips.dbt",
        source_text="Alchemist",
        ref=SimpleNamespace(source_field="title", row_key=row_key),
    )
    description = SimpleNamespace(
        label="ALCHEMIST",
        file_rel="Tooltips.dbt",
        source_text="A scholar who brews potions.",
        ref=SimpleNamespace(source_field="description", row_key=row_key),
    )
    tooltip_window = SimpleNamespace(
        model=SimpleNamespace(units=(title, description)),
        _code_references_for_unit=lambda _unit: (),
    )
    tooltip_window._paired_preview_units = lambda selected: TranslatorWindow._paired_preview_units(
        tooltip_window,
        selected,
    )
    paired_title, paired_description = tooltip_window._paired_preview_units(description)
    if paired_title is not title or paired_description is not description:
        raise AssertionError("Tooltips.dbt title and description were not paired by record identity")
    context, selected_title, selected_description, _buttons, _references = TranslatorWindow._game_preview_parts(
        tooltip_window,
        description,
    )
    if (
        context is None
        or context.kind != "tooltip"
        or selected_title is not title
        or selected_description is not description
    ):
        raise AssertionError("a Tooltips.dbt record did not use the combined tooltip preview surface")


def assert_cross_entry_labels_preserve_literal_suffixes() -> None:
    with tempfile.TemporaryDirectory(prefix="translator-preview-labels-") as temp_dir:
        path = Path(temp_dir) / "LiteralSuffix.lua"
        path.write_text(
            "function Run()\n"
            '  MsgBox("", "", "", "@L_BUY_CANNON_AMMU_AMOUNT_HEAD+0", '
            '"@L_BUY_CANNON_AMMU_AMOUNT_BODY+0")\n'
            "end\n",
            encoding="utf-8",
        )
        index = index_code_file(CodeFileSpec(path, "project"))
        references = index.references_for("_BUY_CANNON_AMMU_AMOUNT_HEAD+0").active
        if not references:
            raise AssertionError("a literal +0 label without an underscore was truncated by the code index")
        context = window_context_for_reference(references[0], "_BUY_CANNON_AMMU_AMOUNT_HEAD+0")
        if context is None or context.header_label != "buy_cannon_ammu_amount_head+0":
            raise AssertionError(f"a literal +0 window label lost its suffix: {context!r}")

    reference = CodeReference(
        "document_new_+1",
        Path("Document.lua"),
        10,
        1,
        "MsgBox",
        4,
        ('""', '""', '""', '"@L_DOCUMENT_HEADER"', '"@L_DOCUMENT_NEW_+"..Choice'),
        role="body",
        resolved_arguments=(
            (),
            (),
            (),
            ("@L_document_header_+*",),
            ("@L_document_new_+*",),
        ),
    )
    context = window_context_for_reference(reference, "_DOCUMENT_NEW_+1")
    if (
        context is None
        or context.header_label != "document_header"
        or context.body_label != "document_new_+1"
    ):
        raise AssertionError(f"the selected body suffix polluted a literal companion label: {context!r}")

    description = CodeReference(
        "church_button_+1",
        Path("Church.lua"),
        20,
        1,
        "MsgBox",
        2,
        (
            '""',
            '""',
            '"@B[1,@L_CHURCH_BUTTON_+1]"',
            'TextPrefix.."_HEAD"',
            'TextPrefix.."_DESCRIPTION"',
        ),
        role="button",
        resolved_arguments=(
            (),
            (),
            (),
            ("@L_church_head_+*",),
            ("@L_church_description_+*",),
        ),
    )
    context = window_context_for_reference(description, "_CHURCH_BUTTON_+1")
    if context is None or context.body_label != "church_description_+*":
        raise AssertionError(f"an unrelated button suffix polluted the description entry: {context!r}")
