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
from ..code_window_context import best_window_context
from ..preview_context_selection import select_preview_context
from ..preview_coverage import preview_placeholder_coverage, preview_reference_coverage
from ..preview_placeholders import (
    GLYPH_MARK,
    PlaceholderContext,
    PlaceholderLabelRecord,
    PlaceholderValueBuilder,
    _placeholder_expression,
    placeholder_arguments,
)
from ..script_semantics import (
    analyze_script,
    call_contract,
    resolve_native_semantic_function,
    semantic_literal,
)


def assert_display_call_contracts_match_engine_signatures() -> None:
    expected = {
        "MsgBox": (((3, "header"), (4, "body")), 5, (2,), "messagebox"),
        "MsgBoxNoWait": (((2, "header"), (3, "body")), 4, (), "messagebox"),
        "MsgNews": (((6, "header"), (7, "body")), 8, (2,), "news"),
        "MsgNewsNoWait": (((5, "header"), (6, "body")), 7, (2,), "news"),
        "MsgQuest": (((3, "header"), (4, "body")), 5, (2,), "questbox"),
        "MsgQuestNoWait": (((2, "header"), (3, "body")), 4, (), "questbox"),
        "MsgQuestIntro": (((0, "body"),), 1, (), "quest_intro"),
        "MsgSayInteraction": (((5, "body"),), 6, (3,), "dialog"),
        "MsgSayInteractionNoWait": (((5, "body"),), 6, (3,), "dialog"),
        "MsgSystem": (((1, "body"),), 2, (), "system_message"),
        "ShowTutorialBox": (((7, "header"), (8, "body")), 10, (6,), "tutorial"),
        "ShowTutorialBoxNoWait": (((6, "header"), (7, "body")), 9, (), "tutorial"),
        "SetQuestTitle": (((0, "header"),), 1, (), "questbook"),
        "SetQuestDescription": (((0, "body"),), 2, (), "questbook"),
        "SetQuestDescriptionByQuestname": (((2, "body"),), 4, (), "questbook"),
        "SetMainQuestTitle": (((1, "header"),), 2, (), "questbook"),
        "SetMainQuestDescription": (((1, "body"),), 2, (), "questbook"),
        "feedback_MessagePolitics": (((1, "header"), (2, "body")), 3, (), "news"),
        "feedback_MessageMission": (((1, "header"), (2, "body")), 3, (), "news"),
        "feedback_MessageOffice": (((2, "header"), (3, "body")), 4, (), "news"),
        "InitAlias": (((3, "body"),), 5, (), "measure_choice"),
        "AddSheetToTabGroup": (((2, "header"),), 3, (), "gui_embedded"),
        "SetTabGroupHeader": (((1, "header"),), 2, (), "gui_embedded"),
        "CreateImportantPersonSection": (((1, "header"),), 2, (), "important_persons"),
        "BlackBoardAddPamphlet": (((2, "body"),), 3, (), "pamphlet"),
    }
    for call_name, values in expected.items():
        contract = call_contract(call_name)
        if contract is None:
            raise AssertionError(f"documented display call is missing a contract: {call_name}")
        actual = (
            contract.label_roles,
            contract.runtime_start,
            contract.button_arguments,
            contract.surface,
        )
        if actual != values:
            raise AssertionError(
                f"display contract does not match the documented {call_name} signature: {actual!r}"
            )

    news = call_contract("MsgNewsNoWait")
    if news is None or news.panel_argument != 2 or news.category_argument != 3:
        raise AssertionError(f"news presentation arguments were not described separately: {news!r}")
    interaction = call_contract("MsgSayInteraction")
    if (
        interaction is None
        or interaction.panel_argument != 3
        or interaction.speaker_argument != 1
    ):
        raise AssertionError(f"dialog presentation arguments were not retained: {interaction!r}")


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

    scheduled = analyze_script(
        (
            'feedback_MessageSchedule("", '
            '"@L_FAMILY_SCHOOL_HEAD", '
            '"@L_FAMILY_SCHOOL_BODY", GetID(""), Money)'
        ),
        Path("School.lua"),
        label_catalog=frozenset(
            {
                "family_school_head_+0",
                "family_school_body_+0",
            }
        ),
    )
    scheduled_by_role = {use.role: use for use in scheduled}
    if scheduled_by_role["header"].label != "family_school_head_+*":
        raise AssertionError("an omitted DBT suffix was not represented as a header family")
    if scheduled_by_role["body"].label != "family_school_body_+*":
        raise AssertionError("an omitted DBT suffix was not represented as a body family")
    expected_arguments = (
        ("",),
        ("@L_family_school_head_+*",),
        ("@L_family_school_body_+*",),
        (),
        (),
    )
    if scheduled_by_role["body"].resolved_arguments != expected_arguments:
        raise AssertionError(
            "family semantics did not reach the call's paired label arguments"
        )

    datebook = analyze_script(
        (
            'SimAddDatebookEntry("Sim", EventTime, "Court", '
            '"@L_TRIAL_DATEBOOK_HEAD_+0", "@L_TRIAL_DATEBOOK_BODY_+0", '
            'GetID("Accuser"), GetSettlementID(""))'
        ),
        Path("Trial.lua"),
    )
    datebook_by_role = {use.role: use for use in datebook}
    if datebook_by_role["header"].argument_index != 3:
        raise AssertionError("datebook semantics lost the entry title argument")
    datebook_body = datebook_by_role["body"]
    if datebook_body.argument_index != 4 or datebook_body.runtime_arguments != (
        'GetID("Accuser")',
        'GetSettlementID("")',
    ):
        raise AssertionError("datebook title/body did not share the runtime arguments")

    office = analyze_script(
        (
            'feedback_MessageOffice("", PrivilegeList, '
            '"@L_OFFICE_HEAD_+0", "@L_OFFICE_BODY_+0", '
            'GetID(""), GetSettlementID(""))'
        ),
        Path("Office.lua"),
    )
    office_by_role = {use.role: use for use in office}
    if office_by_role["header"].argument_index != 2:
        raise AssertionError(
            "semantic call analysis did not locate the shifted office header"
        )
    office_body = office_by_role["body"]
    if office_body.argument_index != 3 or office_body.runtime_arguments != (
        'GetID("")',
        'GetSettlementID("")',
    ):
        raise AssertionError(
            "semantic call analysis used a fixed feedback-message layout"
        )
    if office_body.runtime_argument_kinds != ((), ("settlement",)):
        raise AssertionError(
            "shifted feedback-message parameters lost their runtime types"
        )

    dynamic = analyze_script(
        "\n".join(
            (
                "function Main()",
                "    local Choice = 0",
                "    Choice = RuntimeChoice()",
                '    MsgQuick("", "@L_DYNAMIC_BODY_+"..Choice, GetID(""))',
                "end",
            )
        ),
        Path("DynamicChoice.lua"),
        label_catalog=frozenset(
            {
                "dynamic_body_+0",
                "dynamic_body_+2",
                "dynamic_body_+3",
            }
        ),
    )
    dynamic_labels = {use.label for use in dynamic}
    if "dynamic_body_+*" not in dynamic_labels:
        raise AssertionError(
            "a dynamic label collapsed to its stale initialized value"
        )


def assert_code_semantics_resolve_local_function_returns() -> None:
    script = "\n".join(
        (
            "function MakeItemLabel(kind, suffix)",
            '    local prefix = "@L_ITEM_"',
            '    local label = prefix..kind.."_NAME_+"..suffix',
            "    return label",
            "end",
            "function BranchLabel()",
            '    if Enabled then return "@L_OPTION_ENABLED_+0" end',
            '    return "@L_OPTION_DISABLED_+0"',
            "end",
            "function RecursiveLabel(value)",
            "    return RecursiveLabel(value)",
            "end",
            "function EstablishImplicitObject()",
            '    CityIsKontor("")',
            "end",
            "function ReportImplicitObject()",
            '    MsgQuick("", "@L_BODY_IMPLICIT_SETTLEMENT_+0", GetID(""))',
            "end",
            "function Main()",
            '    MsgQuick("", "@L_BODY_BREAD_+0", MakeItemLabel("BREAD", "0"))',
            '    MsgQuick("", "@L_BODY_CAKE_+0", MakeItemLabel("CAKE", "1"))',
            '    MsgQuick("", "@L_BODY_BRANCH_+0", BranchLabel())',
            '    MsgQuick("", "@L_BODY_RECURSIVE_+0", RecursiveLabel("BREAD"))',
            '    MsgQuick("", "@L_BODY_ITEM_+0", ItemGetLabel("BoozyBreathBeer", true))',
            '    MsgQuick("", "@L_BODY_ITEM_NUMERIC_+0", ItemGetLabel("BoozyBreathBeer", 1))',
            '    MsgQuick("", "@L_BODY_ITEM_PLURAL_+0", ItemGetLabel(ItemId, false))',
            "    local citylabel = CityLevel2Label(2)",
            '    MsgQuick("", "@L_BODY_CITY_+0", citylabel)',
            "    local titlelabel = GetNobilityTitleLabel(CurrentTitle)",
            '    MsgQuick("", "@L_BODY_TITLE_+0", titlelabel)',
            '    local badge = dyn_GetFlagLabel("Destination")',
            '    MsgQuick("", "@L_BODY_BADGE_+0", badge)',
            '    local SettlementId = GetSettlementID("")',
            '    MsgQuick("", "@L_BODY_SETTLEMENT_+0", SettlementId)',
            "    local Place",
            "    if UseSettlement then",
            '        Place = GetSettlementID("")',
            "    else",
            '        Place = GetHomeBuildingId("")',
            "    end",
            '    MsgQuick("", "@L_BODY_AMBIGUOUS_OBJECT_+0", Place)',
            '    GetSettlement("", "AnyPlace")',
            '    MsgQuick("", "@L_BODY_ALIAS_SETTLEMENT_+0", GetID("AnyPlace"))',
            '    GetDynasty("", "AnyFamily")',
            '    MsgQuick("", "@L_BODY_ALIAS_DYNASTY_+0", GetID("AnyFamily"))',
            '    MsgQuick("", "@L_BODY_FIXED_CITY_+0", GetID("City"))',
            '    MsgQuick("", "@L_BODY_UNKNOWN_ALIAS_+0", GetID("TemporaryAlias"))',
            '    SimGetGender("AnyOfficer")',
            '    MsgQuick("", "@L_BODY_INPUT_CHARACTER_+0", GetID("AnyOfficer"))',
            '    CityGetRandomBuilding(TravelAlias, -1, -1, -1, -1, 0, "Market")',
            '    MsgQuick("", "@L_BODY_INPUT_SETTLEMENT_+0", GetID(TravelAlias))',
            '    if IsType(TargetAlias, "Building") then',
            '        MsgQuick("", "@L_BODY_PREDICATE_BUILDING_+0", GetID(TargetAlias))',
            "    else",
            '        MsgQuick("", "@L_BODY_PREDICATE_UNKNOWN_+0", GetID(TargetAlias))',
            "    end",
            "end",
        )
    )
    uses = analyze_script(
        script,
        Path("FunctionValues.lua"),
        label_catalog=frozenset(
            {
                "body_bread_+0",
                "body_cake_+0",
                "body_branch_+0",
                "body_recursive_+0",
                "body_item_+0",
                "body_item_numeric_+0",
                "body_item_plural_+0",
                "body_city_+0",
                "body_title_+0",
                "item_bread_name_+0",
                "item_cake_name_+1",
                "_item_boozybreathbeer_name_+0",
                "option_enabled_+0",
                "option_disabled_+0",
            }
        ),
    )
    by_label = {
        use.label: use
        for use in uses
        if use.role == "body"
    }
    if by_label["body_bread_+0"].runtime_argument_values != (("@L_ITEM_BREAD_NAME_+0",),):
        raise AssertionError(
            "a local function return did not bind the first call's concrete parameters"
        )
    if by_label["body_bread_+0"].runtime_argument_kinds != (("label",),):
        raise AssertionError(
            "the semantic analyzer did not type a local label-returning function"
        )
    if by_label["body_cake_+0"].runtime_argument_values != (("@L_ITEM_CAKE_NAME_+1",),):
        raise AssertionError(
            "a local function return leaked parameter values from another call site"
        )
    if set(by_label["body_branch_+0"].runtime_argument_values[0]) != {
        "@L_OPTION_ENABLED_+0",
        "@L_OPTION_DISABLED_+0",
    }:
        raise AssertionError("branching function returns did not remain an explicit candidate set")
    if by_label["body_branch_+0"].runtime_argument_kinds != (("label", "label"),):
        raise AssertionError("branching label candidates lost their semantic type")
    if by_label["body_recursive_+0"].runtime_argument_values != ((),):
        raise AssertionError("recursive label evaluation did not stop at the bounded call guard")
    if by_label["body_item_+0"].runtime_argument_values != (
        ("_ITEM_BoozyBreathBeer_NAME_+0",),
    ):
        raise AssertionError("ItemGetLabel did not apply its documented singular label contract")
    if by_label["body_item_numeric_+0"].runtime_argument_values != (
        ("_ITEM_BoozyBreathBeer_NAME_+0",),
    ):
        raise AssertionError("ItemGetLabel did not recognize the game's numeric true convention")
    if by_label["body_item_plural_+0"].runtime_argument_values != (
        ("_ITEM_*_NAME_+1",),
    ):
        raise AssertionError("an unknown item did not retain its documented plural label family")
    if by_label["body_city_+0"].runtime_argument_values != (
        ("_GENERAL_INFORMATION_CITY_LEVEL_NAME_+2",),
    ):
        raise AssertionError(
            "a shared engine contract did not resolve through a local variable"
        )
    if by_label["body_city_+0"].runtime_argument_kinds != (("label",),):
        raise AssertionError("a local engine label result lost its semantic type")
    if by_label["body_title_+0"].runtime_argument_values != (
        ("_CHARACTERS_3_TITLES_NAME_+*",),
    ):
        raise AssertionError(
            "an uncertain local engine result guessed an exact title label"
        )
    badge = by_label["body_badge_+0"]
    if badge.runtime_argument_values != (("",),):
        raise AssertionError("a runtime dynasty crest invented a fixed glyph")
    if badge.runtime_argument_kinds != (("dynasty_crest",),):
        raise AssertionError("dyn_GetFlagLabel did not preserve its crest display domain")
    settlement = by_label["body_settlement_+0"]
    if settlement.runtime_argument_values != (("",),):
        raise AssertionError("an unknown runtime object ID leaked into preview text")
    if settlement.runtime_argument_kinds != (("settlement",),):
        raise AssertionError(
            "GetSettlementID type did not propagate through a local variable"
        )
    ambiguous_object = by_label["body_ambiguous_object_+0"]
    if set(ambiguous_object.runtime_argument_kinds[0]) != {
        "building",
        "settlement",
    }:
        raise AssertionError(
            "branching runtime object types did not remain explicitly ambiguous"
        )
    if by_label["body_alias_settlement_+0"].runtime_argument_kinds != (
        ("settlement",),
    ):
        raise AssertionError(
            "GetID did not consume the type established by GetSettlement"
        )
    if by_label["body_alias_dynasty_+0"].runtime_argument_kinds != (
        ("dynasty",),
    ):
        raise AssertionError(
            "GetID did not consume the type established by GetDynasty"
        )
    if by_label["body_fixed_city_+0"].runtime_argument_kinds != (
        ("settlement",),
    ):
        raise AssertionError("GetID did not recognize a fixed engine alias")
    if by_label["body_unknown_alias_+0"].runtime_argument_kinds != ((),):
        raise AssertionError("an unproven temporary alias was typed from its name")
    if by_label["body_input_character_+0"].runtime_argument_kinds != (
        ("character",),
    ):
        raise AssertionError(
            "GetID did not consume character evidence from an engine API input"
        )
    if by_label["body_input_settlement_+0"].runtime_argument_kinds != (
        ("settlement",),
    ):
        raise AssertionError(
            "GetID did not consume settlement evidence carried by a Lua variable"
        )
    if by_label["body_implicit_settlement_+0"].runtime_argument_kinds != (
        ("settlement",),
    ):
        raise AssertionError(
            "a consistent script-wide implicit alias type was not reused"
        )
    if by_label["body_predicate_building_+0"].runtime_argument_kinds != (
        ("building",),
    ):
        raise AssertionError("IsType did not narrow the true branch")
    if by_label["body_predicate_unknown_+0"].runtime_argument_kinds != ((),):
        raise AssertionError("IsType leaked its type evidence into the false branch")

    branch_script = "\n".join(
        (
            "function Main()",
            "    local BodyLabel",
            "    local SubstLabel",
            "    if IsMale then",
            '        BodyLabel = "@L_BIRTH_BODY_SON_+0"',
            '        SubstLabel = "@L_BIRTH_PROMPT_SON_+0"',
            "    else",
            '        BodyLabel = "@L_BIRTH_BODY_DAUGHTER_+0"',
            '        SubstLabel = "@L_BIRTH_PROMPT_DAUGHTER_+0"',
            "    end",
            "    if NameAlreadySet then SubstLabel = \"\" end",
            '    MsgQuick("", BodyLabel, GetID("Child"), SubstLabel)',
            "end",
        )
    )
    branch_uses = {
        use.label: use
        for use in analyze_script(branch_script, Path("BranchCorrelation.lua"))
        if use.role == "body"
    }
    son_values = branch_uses["birth_body_son_+0"].runtime_argument_values[1]
    daughter_values = branch_uses[
        "birth_body_daughter_+0"
    ].runtime_argument_values[1]
    if son_values != ("@L_BIRTH_PROMPT_SON_+0", ""):
        raise AssertionError(
            f"the son message inherited an incompatible branch value: {son_values!r}"
        )
    if daughter_values != ("@L_BIRTH_PROMPT_DAUGHTER_+0", ""):
        raise AssertionError(
            f"the daughter message inherited an incompatible branch value: {daughter_values!r}"
        )


def assert_code_semantics_follow_fields_panels_and_initdata() -> None:
    script = "\n".join(
        (
            "function Main()",
            '    Strings.Body = "@L_MEASURE_ORDERCREDIT_BODY_+1"',
            '    MsgBox("", "Owner", "", "@L_CREDIT_HEAD_+0", Strings.Body, Account)',
            '    local Options = "@P"',
            '    Options = Options .. "@B["..i..",@L_TWP_SUPPLYWORKSHOP_MARKET_+"..i..",]"',
            "    local LabelIds = {}",
            "    for i=1, 10 do LabelIds[i] = GetSettlementID(Target[i]) end",
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
    if (
        len(panel_use.runtime_argument_kinds) != 24
        or any(kinds != ("settlement",) for kinds in panel_use.runtime_argument_kinds)
    ):
        raise AssertionError(
            f"an unpacked dynamic table lost its per-position semantics: {panel_use!r}"
        )
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


def assert_feedback_message_contracts_do_not_depend_on_label_names() -> None:
    uses = analyze_script(
        "\n".join(
            (
                'feedback_MessagePolitics("Patron", "@L_GUILDMASTER_HEAD",',
                '    "@L_GUILDMASTER_PLAYER_FEMALE", GetID("City"), PatronID,',
                '    GetYear(), "@L_GUILDMASTER_TITLE_FEMALE")',
                'feedback_MessageMission("Actor", "@L_MISSION_NAME_+0",',
                '    "@L_MISSION_NAME_+1", Goal)',
                'feedback_MessageOffice("", GetPrivilegeList, "@L_OFFICE_GAIN_HEAD",',
                '    "@L_OFFICE_GAIN_DESCRIPTION", GetID(""))',
            )
        ),
        Path("FeedbackContracts.lua"),
    )
    by_label = {
        use.label: use
        for use in uses
        if use.label
    }
    politics = by_label["guildmaster_player_female"]
    if politics.role != "body":
        raise AssertionError(
            f"a feedback body depended on BODY/TEXT in its label name: {politics!r}"
        )
    if politics.runtime_arguments != (
        'GetID("City")',
        "PatronID",
        "GetYear()",
        '"@L_GUILDMASTER_TITLE_FEMALE"',
    ):
        raise AssertionError(
            f"feedback runtime placeholders were shifted by the body label: {politics!r}"
        )
    if politics.runtime_argument_kinds[0] != ("settlement",):
        raise AssertionError(
            f"feedback runtime semantics no longer started at %1: {politics!r}"
        )
    if politics.runtime_argument_values[3] != ("@L_GUILDMASTER_TITLE_FEMALE",):
        raise AssertionError(
            f"the fourth feedback placeholder did not retain its label: {politics!r}"
        )
    mission = by_label["mission_name_+1"]
    if mission.role != "body" or mission.runtime_arguments != ("Goal",):
        raise AssertionError(
            f"Mission feedback used label spelling to locate its body: {mission!r}"
        )
    office = by_label["office_gain_description"]
    if office.role != "body" or office.runtime_arguments != ('GetID("")',):
        raise AssertionError(
            f"Office feedback ignored its extra privilege-list parameter: {office!r}"
        )


def assert_dynamic_table_and_engine_label_semantics_are_preserved() -> None:
    uses = analyze_script(
        "\n".join(
            (
                "function Run()",
                '    local Skills = {"@L_TALENTS_DEXTERITY_NAME_+0",',
                '        "@L_TALENTS_RHETORIC_NAME_+0"}',
                "    local SkillIndex = Rand(2) + 1",
                '    MsgQuick("", "@L_RANDOM_TALENT_BODY_+0", Skills[SkillIndex])',
                '    MsgQuick("", "@L_OFFICE_BODY_+0", OfficeGetTextLabel(OfficeID))',
                "    local LabelIds = {}",
                "    for i=1, 10 do LabelIds[i] = GetSettlementID(Target[i]) end",
                '    MsgQuick("", "@L_MARKET_BODY_+0", LabelIds[4])',
                "    local function getLabel(value)",
                '        return value == 1 and "@L_MESSAGES_ON_+0" or "@L_MESSAGES_OFF_+0"',
                "    end",
                "    local Labels = {Take = getLabel(1)}",
                '    MsgQuick("", "@L_MESSAGES_BODY_+0", Labels.Take)',
                '    MsgQuick("", "@L_PROFESSION_BODY_+0", ProfessionGetLabel(Profession, Gender))',
                '    MsgQuick("", "@L_ROUNDED_BODY_+0", math.floor(Amount * Factor))',
                '    MsgQuick("", "@L_VEHICLE_BODY_+0", GetID("Destination"))',
                '    local VehicleType = CartGetType("Destination")',
                "end",
            )
        ),
        Path("RuntimeCollections.lua"),
    )
    by_label = {
        use.label: use
        for use in uses
        if use.role == "body"
    }
    talents = by_label["random_talent_body_+0"]
    if talents.runtime_argument_values != (
        (
            "@L_TALENTS_DEXTERITY_NAME_+0",
            "@L_TALENTS_RHETORIC_NAME_+0",
        ),
    ):
        raise AssertionError(
            f"a dynamic table index lost its concrete candidate values: {talents!r}"
        )
    if talents.runtime_argument_kinds != (("label", "label"),):
        raise AssertionError(
            f"dynamic table label candidates lost their semantic type: {talents!r}"
        )
    office = by_label["office_body_+0"]
    if office.runtime_argument_values != (
        ("_CHARACTERS_3_OFFICES_NAME_*_+*",),
    ):
        raise AssertionError(
            f"OfficeGetTextLabel did not retain its documented label family: {office!r}"
        )
    if office.runtime_argument_kinds != (("label",),):
        raise AssertionError(
            f"OfficeGetTextLabel lost its label semantic type: {office!r}"
        )
    market = by_label["market_body_+0"]
    if market.runtime_argument_kinds != (("settlement",),):
        raise AssertionError(
            f"a fixed index did not follow a dynamic table assignment: {market!r}"
        )
    messages = by_label["messages_body_+0"]
    if messages.runtime_argument_values != (
        ("@L_MESSAGES_ON_+0", "@L_MESSAGES_OFF_+0"),
    ):
        raise AssertionError(
            f"a conditional local-function return lost its label candidates: {messages!r}"
        )
    profession = by_label["profession_body_+0"]
    if profession.runtime_argument_values != (
        ("_CHARACTERS_2_PROFESSIONS_*_NAME_+*",),
    ):
        raise AssertionError(
            f"ProfessionGetLabel lost its documented label family: {profession!r}"
        )
    rounded = by_label["rounded_body_+0"]
    if rounded.runtime_argument_kinds != (("number",),):
        raise AssertionError(
            f"a numeric engine function lost its scalar result type: {rounded!r}"
        )
    vehicle = by_label["vehicle_body_+0"]
    if vehicle.runtime_argument_kinds != (("vehicle",),):
        raise AssertionError(
            f"a stable alias ignored later engine type evidence: {vehicle!r}"
        )


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
        (temp / "Panel.gui").write_bytes(
            b"\x03\x00binary@L_GUI_RESOURCE_+0\x00"
            b"Hud/NoCompression/Priority3/PanelBackground_01.tga\x00"
            b"Hud/borders/Border_Gold_02.tga\x00"
            b"Hud/NoCompression/header_red.tga\x00"
        )
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
        gui_context = best_window_context(gui, "GUI_RESOURCE_+0")
        if (
            gui_context is None
            or gui_context.surface != "gui_embedded"
            or gui_context.layout != "panel"
            or gui_context.gui_resource != "Panel.gui"
            or gui_context.background_asset
            != "Hud/NoCompression/Priority3/PanelBackground_01.tga"
            or gui_context.frame_asset != "Hud/borders/Border_Gold_02.tga"
            or gui_context.title_asset != "Hud/NoCompression/header_red.tga"
        ):
            raise AssertionError(
                f"binary GUI resources did not drive their preview presentation: {gui_context!r}"
            )
        (temp / "Panel.gui").write_bytes(
            b"\x03\x00binary@L_GUI_RESOURCE_+0\x00"
            b"Hud/messagebox/mbback0.tga\x00"
        )
        refreshed_gui_context = best_window_context(gui, "GUI_RESOURCE_+0")
        if (
            refreshed_gui_context is None
            or refreshed_gui_context.background != "parchment"
            or refreshed_gui_context.layout != "parchment"
            or refreshed_gui_context.background_asset != "Hud/messagebox/mbback0.tga"
        ):
            raise AssertionError(
                f"changed GUI resources reused stale presentation data: {refreshed_gui_context!r}"
            )
        coverage = preview_reference_coverage(index)
        if (
            coverage.indexed_labels != 4
            or coverage.semantic_display_labels != 3
            or coverage.gui_resource_labels != 1
            or coverage.non_display_labels != 0
            or coverage.low_confidence_labels != 1
            or coverage.runtime_argument_positions != 1
            or coverage.resolved_runtime_positions != 0
            or coverage.unresolved_runtime_positions != 1
        ):
            raise AssertionError(f"preview reference coverage was counted incorrectly: {coverage!r}")
        placeholder_coverage = preview_placeholder_coverage(
            index,
            (
                ("FAMILY_BASE_+0", "%1NAME"),
                ("EXACT_BASE", "%1l"),
            ),
        )
        if (
            placeholder_coverage.labels_with_placeholders != 2
            or placeholder_coverage.placeholder_positions != 2
            or placeholder_coverage.expression_only_positions != 1
            or placeholder_coverage.missing_positions != 1
        ):
            raise AssertionError(
                f"actual placeholder evidence was counted incorrectly: {placeholder_coverage!r}"
            )
        quality_index = CodeReferenceIndex(
            {
                "structure_+0": (
                    CodeReference(
                        "structure_+0",
                        temp / "Structure.lua",
                        1,
                        1,
                        runtime_arguments=("Separator",),
                        runtime_argument_values=(("$N",),),
                        runtime_argument_kinds=(("structure",),),
                    ),
                ),
                "bad_format_+0": (
                    CodeReference(
                        "bad_format_+0",
                        temp / "BadFormat.lua",
                        1,
                        1,
                        runtime_arguments=("math.floor(Money)",),
                        runtime_argument_values=(("",),),
                        runtime_argument_kinds=(("number",),),
                    ),
                ),
            }
        )
        quality_coverage = preview_placeholder_coverage(
            quality_index,
            (
                ("STRUCTURE_+0", "%1l"),
                ("BAD_FORMAT_+0", "%1l"),
            ),
        )
        if (
            quality_coverage.structural_positions != 1
            or quality_coverage.incompatible_positions != 1
            or quality_coverage.expression_only_positions != 0
        ):
            raise AssertionError(
                f"preview quality evidence was misclassified: {quality_coverage!r}"
            )
        parsed = placeholder_arguments("%2it | %1Sn | %2.1f/d")
        if parsed != ((2, "i"), (1, "Sn")):
            raise AssertionError(
                f"preview placeholder parsing disagreed with engine token boundaries: {parsed!r}"
            )
        late_reference = CodeReference(
            "late_*_+*",
            temp / "Late.lua",
            1,
            1,
            source="project",
            match_kind="dynamic",
        )
        index.merge(CodeReferenceIndex({"late_*_+*": (late_reference,)}))
        late = index.references_for("LATE_VALUE_+2").project
        if late != (late_reference,):
            raise AssertionError(
                "merging new code evidence did not invalidate the wildcard lookup cache"
            )
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

    variable_item = CodeReference(
        "same",
        Path("VariableItem.lua"),
        1,
        1,
        "MsgQuick",
        1,
        ('""', '"@L_SAME_+0"', "Choice"),
        runtime_arguments=("Choice",),
        runtime_argument_values=(("ItemLabel[item1]",),),
        role="body",
        confidence=100,
    )
    variable_character = CodeReference(
        "same",
        Path("VariableCharacter.lua"),
        1,
        1,
        "MsgQuick",
        1,
        ('""', '"@L_SAME_+0"', "Choice"),
        runtime_arguments=("Choice",),
        runtime_argument_values=(('GetID("Owner")',),),
        role="body",
        confidence=100,
    )
    selected = select_preview_context("Selected %1l", (variable_character, variable_item), "SAME").references
    if selected != (variable_item,):
        raise AssertionError(f"resolved custom-variable semantics did not affect placeholder ranking: {selected!r}")

    character_projection = select_preview_context(
        "%1ST %1SA %1SN",
        (variable_item, variable_character),
        "SAME",
    ).references
    if character_projection != (variable_character,):
        raise AssertionError(
            f"character field suffixes preferred a label value over a sim object: {character_projection!r}"
        )


def assert_placeholder_values_avoid_ambiguous_random_branches() -> None:
    class Localization:
        target_language = "en"

        @staticmethod
        def character_name(_seed: str, _number: int, _target: bool, *, forename_only: bool = False) -> str:
            return "Alex" if forename_only else "Alex Smith"

        @staticmethod
        def character_name_parts(_seed: str, _number: int, _target: bool) -> tuple[str, str, str]:
            return "Alex", "Smith", "male"

        @staticmethod
        def sample_label(prefix: str, _suffix: str, _seed: str, _number: int, _target: bool) -> str:
            if prefix == "_CITY_NAME_":
                return "York"
            if prefix == "_GENERAL_INFORMATION_CITY_LEVEL_NAME_+":
                return "Town"
            if prefix == "_CHARACTERS_3_TITLES_NAME_+":
                return "Citizen"
            if prefix == "_CHARACTERS_2_PROFESSIONS_":
                return "Blacksmith"
            if prefix == "_PRIVILEGE_":
                return "May trade goods"
            return "Supreme Commander"

        @staticmethod
        def sample_label_record(
            prefix: str,
            _suffixes: tuple[str, ...],
            _seed: str,
            _number: int,
            _target: bool,
        ) -> PlaceholderLabelRecord:
            if prefix == "_CHARACTERS_1_CLASSES_":
                return PlaceholderLabelRecord(
                    "_CHARACTERS_1_CLASSES_patron",
                    ("Patron", "Worker"),
                )
            if prefix == "_CHARACTERS_2_PROFESSIONS_":
                return PlaceholderLabelRecord(
                    "_CHARACTERS_2_PROFESSIONS_blacksmith_NAME_+0",
                    ("Blacksmith",),
                )
            return PlaceholderLabelRecord("_BUILDING_Bakery", ("Bakery", "Bread & Butter"))

        @staticmethod
        def sample_character_profession(
            _class_identity: str,
            _seed: str,
            _number: int,
            _target: bool,
        ) -> str:
            return "Baker"

        @staticmethod
        def sample_character_office(_seed: str, _number: int, _target: bool) -> str:
            return "Mayor"

        @staticmethod
        def localized(label: str, _target: bool) -> str:
            return {
                "_OPTION_BEGGAR_+0": "Beggar",
                "_OPTION_EMPEROR_+0": "Emperor",
                "_ITEM_BoozyBreathBeer_NAME_+0": "Drunkard Brew beer",
                "_PRIVILEGE_CanTrade_MESSAGETEXT_+0": "May trade goods",
                "PlainOfficeKey": "Bailiff",
                "_CHARACTERS_3_TITLES_NAME_+9": "Citizen",
                "_BIRTH_PROMPT_DAUGHTER_+0": "What do you want to name your daughter?",
                "SubstSimFullDescOffice_+0": "%1ST %1SV %1SD, %1SA in %2NAME",
            }.get(label, label)

    ambiguous = CodeReference(
        "branch_body_+0",
        Path("Branches.lua"),
        10,
        1,
        "MsgQuick",
        1,
        ('""', '"@L_BRANCH_BODY_+0"', "Choice"),
        runtime_arguments=("Choice",),
        runtime_argument_values=(("@L_OPTION_BEGGAR_+0", "@L_OPTION_EMPEROR_+0"),),
        role="body",
    )
    context = PlaceholderContext("BRANCH_BODY_+0", "Text.dbt", False, "en", (ambiguous,))
    builder = PlaceholderValueBuilder(Localization())
    crest_reference = CodeReference(
        "badge_body_+0",
        Path("Badge.lua"),
        8,
        1,
        "MsgQuick",
        1,
        runtime_arguments=("Badge",),
        runtime_argument_values=(("",),),
        runtime_argument_kinds=(("dynasty_crest",),),
        role="body",
    )
    crest = builder.argument_value(
        1,
        "l",
        PlaceholderContext(
            "BADGE_BODY_+0",
            "Text.dbt",
            False,
            "en",
            (crest_reference,),
        ),
    )
    if crest.text != GLYPH_MARK or crest.glyph_id is None:
        raise AssertionError("a proven dynasty crest did not render as a game glyph")
    value = builder.argument_value(1, "l", context).text
    if value in {"Beggar", "Emperor"}:
        raise AssertionError(f"an unresolved runtime branch was presented as a certain value: {value!r}")

    optional_label = CodeReference(
        "birth_body_daughter_+0",
        Path("Birth.lua"),
        15,
        1,
        "MsgQuick",
        1,
        runtime_arguments=('GetID("Child")', "SubstLabel"),
        runtime_argument_values=(
            (),
            ("@L_BIRTH_PROMPT_DAUGHTER_+0", ""),
        ),
        runtime_argument_kinds=((), ("label", "structure")),
        role="body",
    )
    optional_context = PlaceholderContext(
        "BIRTH_BODY_DAUGHTER_+0",
        "Text.dbt",
        False,
        "en",
        (optional_label,),
        ((2, ("l",)),),
    )
    if (
        builder.argument_value(2, "l", optional_context).text
        != "What do you want to name your daughter?"
    ):
        raise AssertionError(
            "an optional structural blank hid the one semantically compatible label"
        )

    character = CodeReference(
        "speech_+0",
        Path("Speech.lua"),
        20,
        1,
        "MsgSay",
        1,
        ('"Speaker"', '"@L_SPEECH_+0"', 'GetID("Destination")'),
        runtime_arguments=('GetID("Destination")',),
        role="body",
    )
    character_context = PlaceholderContext(
        "SPEECH_+0",
        "Text.dbt",
        False,
        "en",
        (character,),
        ((1, ("ST", "SA")),),
    )
    title = builder.argument_value(1, "ST", character_context).text
    office = builder.argument_value(1, "SA", character_context).text
    if title == "Supreme Commander" or office == "Supreme Commander":
        raise AssertionError("character metadata placeholders still sampled unrelated extreme DB entries")

    ambiguous_name = CodeReference(
        "plain_name_+0",
        Path("Branches.lua"),
        30,
        1,
        "MsgQuick",
        1,
        ('""', '"@L_PLAIN_NAME_+0"', "Choice"),
        runtime_arguments=("Choice",),
        runtime_argument_values=(('GetID("City")', 'GetID("WorkBuilding")'),),
        role="body",
    )
    ambiguous_context = PlaceholderContext("PLAIN_NAME_+0", "Text.dbt", False, "en", (ambiguous_name,))
    plain_name = builder.argument_value(1, "NAME", ambiguous_context).text
    if plain_name != "Object 1":
        raise AssertionError(f"an ambiguous NAME branch was forced to one object type: {plain_name!r}")

    typed_settlement = CodeReference(
        "settlement_name_+0",
        Path("Settlement.lua"),
        31,
        1,
        "MsgQuick",
        1,
        runtime_arguments=("SettlementId",),
        runtime_argument_values=(("",),),
        runtime_argument_kinds=(("settlement",),),
        role="body",
    )
    settlement_context = PlaceholderContext(
        "SETTLEMENT_NAME_+0",
        "Text.dbt",
        False,
        "en",
        (typed_settlement,),
    )
    if builder.argument_value(1, "NAME", settlement_context).text != "York":
        raise AssertionError(
            "NAME ignored a settlement type proven by runtime data flow"
        )
    typed_vehicle = CodeReference(
        "vehicle_name_+0",
        Path("Vehicle.lua"),
        32,
        1,
        "MsgQuick",
        1,
        runtime_arguments=('GetID("Destination")',),
        runtime_argument_values=(("",),),
        runtime_argument_kinds=(("vehicle",),),
        role="body",
    )
    vehicle_context = PlaceholderContext(
        "VEHICLE_NAME_+0",
        "Text.dbt",
        False,
        "en",
        (typed_vehicle,),
    )
    if builder.argument_value(1, "NAME", vehicle_context).text != "Sea Hawk":
        raise AssertionError(
            "NAME ignored a vehicle type proven by runtime data flow"
        )
    profession_label = CodeReference(
        "profession_label_+0",
        Path("Profession.lua"),
        33,
        1,
        "MsgQuick",
        1,
        runtime_arguments=("ProfessionGetLabel(Profession, Gender)",),
        runtime_argument_values=(
            ("_CHARACTERS_2_PROFESSIONS_*_NAME_+*",),
        ),
        runtime_argument_kinds=(("label",),),
        role="body",
    )
    profession_context = PlaceholderContext(
        "PROFESSION_LABEL_+0",
        "Text.dbt",
        False,
        "en",
        (profession_label,),
    )
    if builder.argument_value(1, "l", profession_context).text != "Blacksmith":
        raise AssertionError(
            "a proven profession label family did not reach the rendered preview"
        )

    engine_settlement_context = PlaceholderContext(
        "_SETTLEMENTSTATE_HEADLINE_+0",
        "Text.dbt",
        False,
        "en",
    )
    if (
        builder.argument_value(1, "NAME", engine_settlement_context).text
        != "York"
    ):
        raise AssertionError(
            "an engine-owned settlement format lacked its object contract"
        )
    engine_office_context = PlaceholderContext(
        "SubstSimFullDescOffice_+0",
        "Text.dbt",
        False,
        "en",
    )
    if builder.argument_value(2, "NAME", engine_office_context).text != "York":
        raise AssertionError(
            "an engine-owned office format lost its settlement parameter"
        )
    unknown_engine_context = PlaceholderContext(
        "_UNPROVEN_ENGINE_FORMAT_+0",
        "Text.dbt",
        False,
        "en",
    )
    if (
        builder.argument_value(1, "NAME", unknown_engine_context).text
        != "Object 1"
    ):
        raise AssertionError(
            "an unknown engine format was typed from its label spelling"
        )

    character_name_context = PlaceholderContext(
        "CHARACTER_NAME_+0",
        "Text.dbt",
        False,
        "en",
        (character,),
        ((1, ("SN", "NAME")),),
    )
    if builder.argument_value(1, "NAME", character_name_context).text != "Alex Smith":
        raise AssertionError("NAME did not reuse direct character-suffix evidence from the same argument")

    building_name_context = PlaceholderContext(
        "BUILDING_NAME_+0",
        "Text.dbt",
        False,
        "en",
        (character,),
        ((1, ("GG", "NAME")),),
    )
    if builder.argument_value(1, "NAME", building_name_context).text != "Bread & Butter":
        raise AssertionError("NAME did not reuse direct building-suffix evidence from the same argument")

    dynasty_context = PlaceholderContext(
        "DYNASTY_NAME_+0",
        "Text.dbt",
        False,
        "en",
        (character,),
        ((1, ("DN", "NAME", "DS")),),
    )
    if builder.argument_value(1, "DN", dynasty_context).text != "Smith":
        raise AssertionError("DN did not project the dynasty name from its coherent entity")
    if builder.argument_value(1, "NAME", dynasty_context).text != "Smith":
        raise AssertionError("NAME did not reuse direct dynasty-name evidence from the same argument")
    crest = builder.argument_value(1, "DS", dynasty_context)
    if crest.text != GLYPH_MARK or crest.glyph_id is None:
        raise AssertionError("DS did not project the dynasty crest from its coherent entity")

    crest_only_context = PlaceholderContext(
        "CREST_OWNER_+0",
        "Text.dbt",
        False,
        "en",
        (character,),
        ((1, ("DS", "NAME")),),
    )
    if builder.argument_value(1, "NAME", crest_only_context).text != "Object 1":
        raise AssertionError("DS alone incorrectly forced its owner object to be a dynasty")

    literal_reference = CodeReference(
        "literal_values_+0",
        Path("LiteralValues.lua"),
        40,
        1,
        "MsgQuick",
        1,
        ('""', '"@L_LITERAL_VALUES_+0"', "42", "7", "3.50", "1250", '"ready"'),
        runtime_arguments=("42", "7", "3.50", "1250", '"ready"'),
        role="body",
    )
    literal_context = PlaceholderContext(
        "LITERAL_VALUES_+0",
        "Text.dbt",
        False,
        "en",
        (literal_reference,),
        ((1, ("n",)), (2, ("i",)), (3, ("f",)), (4, ("t",)), (5, ("s",))),
    )
    literal_values = tuple(
        builder.argument_value(number, suffix, literal_context).text
        for number, suffix in ((1, "n"), (2, "i"), (3, "f"), (4, "t"), (5, "s"))
    )
    if literal_values != ("42", "7", "3.5", "1250", "ready"):
        raise AssertionError(f"direct scalar caller evidence was not preserved: {literal_values!r}")

    branching_number = CodeReference(
        "branching_number_+0",
        Path("LiteralValues.lua"),
        50,
        1,
        "MsgQuick",
        1,
        ('""', '"@L_BRANCHING_NUMBER_+0"', "Count"),
        runtime_arguments=("Count",),
        runtime_argument_values=(("1", "2"),),
        role="body",
    )
    branching_context = PlaceholderContext(
        "BRANCHING_NUMBER_+0",
        "Text.dbt",
        False,
        "en",
        (branching_number,),
        ((1, ("n",)),),
    )
    if builder.argument_value(1, "n", branching_context).text in {"1", "2"}:
        raise AssertionError("an ambiguous numeric branch was presented as a certain caller value")

    function_label = CodeReference(
        "function_label_+0",
        Path("FunctionLabel.lua"),
        60,
        1,
        "MsgQuick",
        1,
        ('""', '"@L_FUNCTION_LABEL_+0"', 'ItemGetLabel("BoozyBreathBeer", true)'),
        runtime_arguments=('ItemGetLabel("BoozyBreathBeer", true)',),
        runtime_argument_values=(("_ITEM_BoozyBreathBeer_NAME_+0",),),
        role="body",
    )
    function_label_context = PlaceholderContext(
        "FUNCTION_LABEL_+0",
        "Text.dbt",
        False,
        "en",
        (function_label,),
        ((1, ("l",)),),
    )
    if builder.argument_value(1, "l", function_label_context).text != "Drunkard Brew beer":
        raise AssertionError("preview ignored a proven localization value returned by a function")

    privilege_values = CodeReference(
        "privilege_values_+0",
        Path("PrivilegeValues.lua"),
        70,
        1,
        "MsgQuick",
        1,
        runtime_arguments=("chr_GeneratePrivilegeListLabels(Privileges())",),
        runtime_argument_values=(
            ("_PRIVILEGE_CanTrade_MESSAGETEXT_+0",),
            ("$N",),
            ("",),
        ),
        role="body",
    )
    privilege_context = PlaceholderContext(
        "PRIVILEGE_VALUES_+0",
        "Text.dbt",
        False,
        "en",
        (privilege_values,),
        ((1, ("l",)), (2, ("l",)), (3, ("l",))),
    )
    projected = tuple(
        builder.argument_value(number, "l", privilege_context).text
        for number in (1, 2, 3)
    )
    if projected != ("May trade goods", "", "\u200b"):
        raise AssertionError(
            f"localized multi-return labels or structural blank slots were wrong: {projected!r}"
        )

    typed_values = CodeReference(
        "typed_values_+0",
        Path("TypedValues.lua"),
        80,
        1,
        "MsgQuick",
        1,
        runtime_arguments=(
            "LabelValue",
            "TextValue",
            'ItemGetLabel("BoozyBreathBeer", true)',
            "CityLevel",
        ),
        runtime_argument_values=(
            ("PlainOfficeKey",),
            ("PlainOfficeKey",),
            ("_ITEM_BoozyBreathBeer_NAME_+0",),
            ("_GENERAL_INFORMATION_CITY_LEVEL_NAME_+*",),
        ),
        runtime_argument_kinds=(
            ("text",),
            ("text",),
            ("label",),
            ("label",),
        ),
        role="body",
    )
    typed_context = PlaceholderContext(
        "TYPED_VALUES_+0",
        "Text.dbt",
        False,
        "en",
        (typed_values,),
        ((1, ("l",)), (2, ("s",)), (3, ("s",)), (4, ("l",))),
    )
    if builder.argument_value(1, "l", typed_context).text != "Bailiff":
        raise AssertionError("%l did not localize a proven plain label key")
    if builder.argument_value(2, "s", typed_context).text != "PlainOfficeKey":
        raise AssertionError("%s localized a plain runtime string instead of displaying it")
    if builder.argument_value(3, "s", typed_context).text == "Drunkard Brew beer":
        raise AssertionError("%s incorrectly consumed a localization-label runtime value")
    if builder.argument_value(4, "l", typed_context).text != "Town":
        raise AssertionError("%l did not sample a proven dynamic label family")
    wildcard_field = CodeReference(
        "wildcard_field_+0",
        Path("WildcardField.lua"),
        85,
        1,
        "MsgQuick",
        1,
        runtime_arguments=("PrivilegeLabel",),
        runtime_argument_values=(("_PRIVILEGE_*_MESSAGETEXT_+0",),),
        runtime_argument_kinds=(("label",),),
        role="body",
    )
    if (
        builder.argument_value(
            1,
            "l",
            PlaceholderContext(
                "WILDCARD_FIELD_+0",
                "Text.dbt",
                False,
                "en",
                (wildcard_field,),
            ),
        ).text
        != "May trade goods"
    ):
        raise AssertionError(
            "a wildcard inside a label family was not sampled as a real localized entry"
        )

    named_string = CodeReference(
        "named_string_+0",
        Path("NamedString.lua"),
        90,
        1,
        "MsgQuick",
        1,
        runtime_arguments=("GetName(CityAlias)",),
        role="body",
    )
    named_string_context = PlaceholderContext(
        "NAMED_STRING_+0",
        "Text.dbt",
        False,
        "en",
        (named_string,),
        ((1, ("s",)),),
    )
    if builder.argument_value(1, "s", named_string_context).text != "York":
        raise AssertionError("%s did not project the object name proven by its caller")


def assert_variadic_runtime_arguments_map_to_placeholder_positions() -> None:
    generic_privileges = resolve_native_semantic_function(
        "chr_GeneratePrivilegeListLabels",
        ((semantic_literal("*"),),),
    )
    if (
        generic_privileges is None
        or len(generic_privileges) != 21
        or generic_privileges[0][0].text != "_PRIVILEGE_*_MESSAGETEXT_+0"
        or generic_privileges[1][0].text != "$N"
    ):
        raise AssertionError(
            "a privilege-list call with runtime-only contents lost its known return structure"
        )
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


def assert_cross_file_function_summaries_bind_arguments_and_expand_returns() -> None:
    temp = Path(tempfile.mkdtemp(prefix="translator_tool_cross_file_values_"))
    try:
        helper = temp / "helper.lua"
        caller = temp / "caller.lua"
        helper.write_text(
            "\n".join(
                (
                    "function MakeValues(kind)",
                    '    local First = "@L_ITEM_"..kind.."_NAME_+0"',
                    '    return First, "@L_ITEM_SECOND_NAME_+0"',
                    "end",
                    "function MakeText(name)",
                    '    return "Office "..name',
                    "end",
                )
            ),
            encoding="utf-8",
        )
        caller.write_text(
            "\n".join(
                (
                    "function Main()",
                    '    MsgQuick("", "@L_REMOTE_VALUES_BODY_+0", helper_MakeValues("BREAD"))',
                    '    MsgQuick("", "@L_REMOTE_TEXT_BODY_+0", helper_MakeText("Bailiff"))',
                    '    MsgQuick("", "@L_CITY_LEVEL_BODY_+0", CityLevel2Label(2))',
                    '    MsgQuick("", "@L_TITLE_LABEL_BODY_+0", GetNobilityTitleLabel(7))',
                    "end",
                )
            ),
            encoding="utf-8",
        )
        linker = CrossFileSemanticLinker()
        index = linker.add(analyze_code_file(CodeFileSpec(caller, "project")))
        if ("project", "helper_makevalues") not in linker.unresolved_value_aliases():
            raise AssertionError("the caller did not expose its missing cross-file value provider")
        helper_analysis = analyze_code_file(CodeFileSpec(helper, "project"))
        value_summary = next(
            summary
            for summary in helper_analysis.function_summaries
            if summary.alias == "helper_makevalues"
        )
        if {
            candidate.kind
            for values in value_summary.return_values
            for candidate in values
        } != {"label"}:
            raise AssertionError(
                f"label-producing returns lost their semantic type: {value_summary!r}"
            )
        text_summary = next(
            summary
            for summary in helper_analysis.function_summaries
            if summary.alias == "helper_maketext"
        )
        if text_summary.return_values[0][0].kind != "text":
            raise AssertionError(
                f"plain text return was encoded as a label or string marker: {text_summary!r}"
            )
        index.merge(linker.add(helper_analysis))
        references = index.references_for("REMOTE_VALUES_BODY_+0").project
        resolved = next(
            (
                item
                for item in references
                if item.runtime_argument_values
                == (
                    ("@L_ITEM_BREAD_NAME_+0",),
                    ("@L_ITEM_SECOND_NAME_+0",),
                )
            ),
            None,
        )
        if resolved is None:
            raise AssertionError(
                f"cross-file function arguments or multi-return positions were lost: {references!r}"
            )
        if resolved.runtime_argument_kinds != (("label",), ("label",)):
            raise AssertionError(
                f"cross-file label types did not reach the caller: {resolved!r}"
            )
        text_reference = index.references_for("REMOTE_TEXT_BODY_+0").project[0]
        if text_reference.runtime_argument_values != (("Office Bailiff",),):
            raise AssertionError(
                f"typed text summary did not bind its caller argument: {text_reference!r}"
            )
        if text_reference.runtime_argument_kinds != (("text",),):
            raise AssertionError(
                f"cross-file text type did not reach the caller: {text_reference!r}"
            )
        if ("project", "helper_makevalues") in linker.unresolved_value_aliases():
            raise AssertionError("a loaded function summary remained marked as unresolved")
        city = index.references_for("CITY_LEVEL_BODY_+0").project[0]
        if city.runtime_argument_values != (
            ("_GENERAL_INFORMATION_CITY_LEVEL_NAME_+2",),
        ):
            raise AssertionError(
                f"CityLevel2Label did not expose its exact label semantics: {city!r}"
            )
        if city.runtime_argument_kinds != (("label",),):
            raise AssertionError(f"engine label type was not preserved: {city!r}")
        title = index.references_for("TITLE_LABEL_BODY_+0").project[0]
        if title.runtime_argument_values != (
            ("_CHARACTERS_3_TITLES_NAME_+*",),
        ):
            raise AssertionError(
                f"GetNobilityTitleLabel guessed an exact gendered title: {title!r}"
            )
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def assert_cross_file_function_summaries_follow_nested_dependencies() -> None:
    temp = Path(tempfile.mkdtemp(prefix="translator_tool_nested_values_"))
    try:
        rank0 = temp / "rank0.lua"
        rank1 = temp / "rank1.lua"
        caller = temp / "caller.lua"
        rank0.write_text(
            'function GetPrivileges() return "CanTrade" end',
            encoding="utf-8",
        )
        rank1.write_text(
            'function GetPrivileges() return "CanApplyForOffice", rank0_GetPrivileges() end',
            encoding="utf-8",
        )
        caller.write_text(
            "\n".join(
                (
                    "function Main()",
                    '    MsgQuick("", "@L_PRIVILEGE_BODY_+0",',
                    "        chr_GeneratePrivilegeListLabels(rank1_GetPrivileges()))",
                    "end",
                )
            ),
            encoding="utf-8",
        )
        linker = CrossFileSemanticLinker()
        index = linker.add(analyze_code_file(CodeFileSpec(caller, "project")))
        index.merge(linker.add(analyze_code_file(CodeFileSpec(rank1, "project"))))
        if ("project", "rank0_getprivileges") not in linker.unresolved_value_aliases():
            raise AssertionError("a dependency discovered inside a loaded summary was not queued")
        index.merge(linker.add(analyze_code_file(CodeFileSpec(rank0, "project"))))
        references = index.references_for("PRIVILEGE_BODY_+0").project
        values = next(
            (
                item.runtime_argument_values
                for item in references
                if len(item.runtime_argument_values) == 21
                and item.runtime_argument_values[0]
                == ("_PRIVILEGE_CanApplyForOffice_MESSAGETEXT_+0",)
                and item.runtime_argument_values[2]
                == ("_PRIVILEGE_CanTrade_MESSAGETEXT_+0",)
            ),
            None,
        )
        if values is None:
            raise AssertionError(
                f"nested summaries did not produce the privilege helper's 21 return slots: {references!r}"
            )
        if values[1] != ("$N",) or any(value != ("",) for value in values[4:]):
            raise AssertionError(f"privilege separator or padding slots were wrong: {values!r}")
    finally:
        shutil.rmtree(temp, ignore_errors=True)
