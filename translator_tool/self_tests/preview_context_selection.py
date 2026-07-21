from __future__ import annotations

from pathlib import Path

from ..code_index import CodeReference
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
    if selection.window.kind != "short" or selection.window.background != "dark_panel":
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
