from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

from ..code_index import (
    CodeFileSpec,
    CodeReference,
    CodeReferenceIndex,
    CrossFileSemanticLinker,
    analyze_code_file,
    scan_scripts_root,
)
from ..preview_context_selection import select_preview_context
from ..preview_placeholders import _placeholder_expression
from ..script_semantics import analyze_script


def assert_code_semantics_are_scope_and_role_aware() -> None:
    script = "\n".join(
        (
            '-- MsgQuick("", "@L_COMMENT_ONLY_+0", WrongValue)',
            "function ProduceEvidence(GenderType)",
            '    MsgSay("accuser", "@L_CASE_SABOTAGE"..GenderType, GetID("accused"), EvidenceTime)',
            "end",
            "function Main()",
            "    local GenderType",
            '    if Female then GenderType = "_TOFEMALE" else GenderType = "_TOMALE" end',
            "    trial_ProduceEvidence(GenderType)",
            '    MsgBox("dynasty", "", "@B[1,@L_BUTTON_+0]",',
            '        "@L_STRONGBOX_HEAD_+0", "@L_STRONGBOX_BODY_+1", DynID, strongboxvalue)',
            "end",
        )
    )
    uses = analyze_script(script, Path("Trial.lua"))
    labels = {use.label for use in uses}
    if "comment_only_+0" in labels:
        raise AssertionError("a localization label inside a Lua line comment was indexed")
    for label in ("case_sabotage_tofemale", "case_sabotage_tomale"):
        matches = [use for use in uses if use.label == label]
        if len(matches) != 1 or matches[0].runtime_arguments != ('GetID("accused")', "EvidenceTime"):
            raise AssertionError(f"function-parameter label propagation was wrong for {label}: {matches!r}")
    head = next(use for use in uses if use.label == "strongbox_head_+0")
    body = next(use for use in uses if use.label == "strongbox_body_+1")
    if head.role != "header" or body.role != "body":
        raise AssertionError(f"MsgBox label roles were not taken from its call contract: {head!r}, {body!r}")
    if head.runtime_arguments != ("DynID", "strongboxvalue") or body.runtime_arguments != head.runtime_arguments:
        raise AssertionError("MsgBox header and body did not share the same runtime argument list")


def assert_code_semantics_follow_fields_panels_and_initdata() -> None:
    script = "\n".join(
        (
            "function Main()",
            '    Strings.Body = "@L_MEASURE_ORDERCREDIT_BODY_+1"',
            '    MsgBox("", "Owner", "", "@L_CREDIT_HEAD_+0", Strings.Body, Account)',
            '    local Options = "@P"',
            '    Options = Options .. "@B["..i..",@L_TWP_SUPPLYWORKSHOP_MARKET_+"..i..",]"',
            '    MsgBox("", "Owner", Options, "@L_SUPPLY_HEAD_+0", "@L_SUPPLY_BODY_+0", helpfuncs_UnpackTable(LabelIds))',
            '    local OptionTable = {"@B[5,@L_ROUTE_OPTION_+5,]", "@B[6,@L_ROUTE_OPTION_+6,]"}',
            "    local SelectedOptions = OptionTable[1]..OptionTable[2]",
            '    MsgBox("", "Owner", SelectedOptions, "@L_ROUTE_HEAD_+0", "@L_ROUTE_BODY_+0", Limit, Interval)',
            '    InitData("@P", 0, "@L_INIT_HEAD_+0", "@L_INIT_BODY_+0", Price)',
            "end",
        )
    )
    uses = analyze_script(script, Path("DataFlow.lua"))
    field_use = next(
        use
        for use in uses
        if use.label == "measure_ordercredit_body_+1" and use.call_name == "MsgBox"
    )
    if field_use.role != "body" or field_use.runtime_arguments != ("Account",):
        raise AssertionError(f"table-field label did not flow into MsgBox: {field_use!r}")
    panel_use = next(
        use
        for use in uses
        if use.label == "twp_supplyworkshop_market_+*" and use.call_name == "MsgBox"
    )
    if panel_use.role != "button" or panel_use.runtime_arguments != ("helpfuncs_UnpackTable(LabelIds)",):
        raise AssertionError(f"accumulated panel label did not flow into MsgBox: {panel_use!r}")
    table_buttons = [
        use
        for use in uses
        if use.label in {"route_option_+5", "route_option_+6"} and use.call_name == "MsgBox"
    ]
    if len(table_buttons) != 2 or any(use.role != "button" for use in table_buttons):
        raise AssertionError(f"table-constructor buttons did not flow into MsgBox: {table_buttons!r}")
    init_head = next(use for use in uses if use.label == "init_head_+0")
    init_body = next(use for use in uses if use.label == "init_body_+0")
    if init_head.role != "header" or init_body.role != "body":
        raise AssertionError(f"InitData label slots were not recognized: {init_head!r}, {init_body!r}")
    if init_body.runtime_arguments != ("Price",):
        raise AssertionError(f"InitData runtime arguments started at the wrong slot: {init_body!r}")


def assert_code_index_handles_families_and_binary_gui() -> None:
    temp = Path(tempfile.mkdtemp(prefix="translator_tool_code_semantics_"))
    try:
        (temp / "Family.lua").write_text(
            '\n'.join((
                'MsgSay("", "@L_FAMILY_BASE", GetID("Owner"))',
                'MsgQuick("", "@L_EXACT_BASE")',
                'MsgQuick("", "@L_FIXED_+3")',
            )),
            encoding="utf-8",
        )
        (temp / "Panel.gui").write_bytes(b"\x03\x00binary@L_GUI_RESOURCE_+0\x00(random bytes)")
        references = scan_scripts_root(
            temp,
            label_catalog=frozenset({"family_base_+0", "exact_base", "exact_base_+1"}),
        )
        index = CodeReferenceIndex(references)
        family = index.references_for("_FAMILY_BASE_+1").project
        if len(family) != 1 or family[0].runtime_arguments != ('GetID("Owner")',):
            raise AssertionError(f"a base label did not match its numbered text family: {family!r}")
        if index.references_for("FIXED_+1").project:
            raise AssertionError("a concrete fixed suffix was broadened into a label family")
        if index.references_for("EXACT_BASE_+1").project:
            raise AssertionError("an exact base label was broadened into a numbered family")
        fixed = index.references_for("FIXED_+3").project
        if len(fixed) != 1:
            raise AssertionError("the exact fixed label stopped matching itself")
        gui = index.references_for("GUI_RESOURCE_+0").project
        if len(gui) != 1 or not gui[0].binary or gui[0].call_name is not None or gui[0].role != "gui_resource":
            raise AssertionError(f"binary GUI data produced a script call context: {gui!r}")
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def assert_placeholder_reference_selection_is_coherent() -> None:
    building = CodeReference(
        "same",
        Path("Building.lua"),
        1,
        1,
        "MsgQuick",
        1,
        ('""', '"@L_SAME_+0"', 'GetID("Owner")', 'GetID("WorkBuilding")'),
        runtime_arguments=('GetID("Owner")', 'GetID("WorkBuilding")'),
        role="body",
        confidence=100,
    )
    item = CodeReference(
        "same",
        Path("Item.lua"),
        1,
        1,
        "MsgQuick",
        1,
        ('""', '"@L_SAME_+0"', 'GetID("Owner")', "ItemLabel[item1]"),
        runtime_arguments=('GetID("Owner")', "ItemLabel[item1]"),
        role="body",
        confidence=100,
    )
    selected = select_preview_context("%1SN found %2l", (building, item), "SAME").references
    if selected != (item,):
        raise AssertionError(f"placeholder selection mixed or chose the weaker call site: {selected!r}")


def assert_variadic_runtime_arguments_map_to_placeholder_positions() -> None:
    privileges = CodeReference(
        "privileges",
        Path("Privileges.lua"),
        1,
        1,
        runtime_arguments=(
            "TitleLabel",
            "BuildLabel",
            "maxworkshops",
            "buildingcount",
            "chr_GeneratePrivilegeListLabels(GetCompletePrivilegeList())",
        ),
    )
    if _placeholder_expression(privileges, 5) != "chr_GeneratePrivilegeListLabels(GetCompletePrivilegeList())[1]":
        raise AssertionError("the first value from a multi-return helper was not mapped to %5")
    if _placeholder_expression(privileges, 24) != "chr_GeneratePrivilegeListLabels(GetCompletePrivilegeList())[20]":
        raise AssertionError("the twentieth value from a multi-return helper was not mapped to %24")
    unpacked = CodeReference(
        "market",
        Path("SupplyWorkshop.lua"),
        1,
        1,
        runtime_arguments=("helpfuncs_UnpackTable(LabelIds)",),
    )
    if _placeholder_expression(unpacked, 9) != "LabelIds[9]":
        raise AssertionError("an unpacked table did not map its ninth value to %9")


def assert_cross_file_return_labels_flow_only_to_real_callers() -> None:
    temp = Path(tempfile.mkdtemp(prefix="translator_tool_cross_file_semantics_"))
    try:
        helper = temp / "helper.lua"
        caller = temp / "caller.lua"
        dead = temp / "dead.lua"
        helper.write_text(
            "\n".join(
                (
                    "function MakeBody(kind)",
                    '    local Label = "@L_REMOTE_BODY_+"',
                    "    return Label..kind",
                    "end",
                )
            ),
            encoding="utf-8",
        )
        caller.write_text(
            "\n".join(
                (
                    "function Main()",
                    "    local Body = helper_MakeBody(Variant)",
                    '    MsgQuick("", Body, Actor)',
                    "end",
                )
            ),
            encoding="utf-8",
        )
        dead.write_text(
            'function Unused() return "@L_DEAD_RETURN_+0" end',
            encoding="utf-8",
        )
        linker = CrossFileSemanticLinker()
        index = linker.add(analyze_code_file(CodeFileSpec(caller, "project")))
        index.merge(linker.add(analyze_code_file(CodeFileSpec(helper, "project"))))
        references = index.references_for("REMOTE_BODY_+3").project
        linked = next((item for item in references if item.call_name == "MsgQuick"), None)
        if linked is None:
            raise AssertionError(f"returned label did not reach its cross-file UI caller: {references!r}")
        if linked.path != caller or linked.line != 3 or linked.role != "body":
            raise AssertionError(f"cross-file label was attached to the wrong call site: {linked!r}")
        if linked.runtime_arguments != ("Actor",):
            raise AssertionError(f"cross-file label lost the caller runtime arguments: {linked!r}")

        dead_index = CrossFileSemanticLinker().add(
            analyze_code_file(CodeFileSpec(dead, "project"))
        )
        dead_references = dead_index.references_for("DEAD_RETURN_+0").project
        if not dead_references or any(item.call_name is not None for item in dead_references):
            raise AssertionError(f"an uncalled return helper fabricated a UI call: {dead_references!r}")
        if dead_references[0].role != "return_value":
            raise AssertionError(f"an uncalled returned label lost its honest source role: {dead_references!r}")
    finally:
        shutil.rmtree(temp, ignore_errors=True)
