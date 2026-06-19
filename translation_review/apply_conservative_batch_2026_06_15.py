# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translator_tool.project import MISSING_WORK_STATUSES, Project, SaveValidationError


LANGUAGE = "#chinese"
REVIEW_PATH = ROOT / "translation_review" / "conservative_batch_2026-06-15.csv"

# Keep this batch deliberately conservative: exact reuse where possible, and short UI
# strings built only from terms already present in the current Chinese files.
TRANSLATIONS: dict[tuple[str, str, str, str], str] = {
    # Multiplayer.dbt
    ("Multiplayer.dbt", "10287", "_MP_LOBBY_SYNC_LOCAL", "chinese"): "本地设置与房主不同",
    ("Multiplayer.dbt", "10288", "_MP_LOBBY_SYNC_BLOCK", "chinese"): "无法开始: 房间设置还在同步。",
    ("Multiplayer.dbt", "10289", "_MP_LOBBY_BTN_SHARE_SAVE", "chinese"): "分享存档",
    ("Multiplayer.dbt", "10290", "_MP_LOBBY_MIDJOIN_DISABLED", "chinese"): "目前禁止中途加入(不稳定)。",
    ("Multiplayer.dbt", "10292", "_MP_LOBBY_NO_CLASS_ALLOWED", "chinese"): "至少必须允许一个角色职业才能开始游戏。",
    ("Multiplayer.dbt", "10293", "_MP_LOBBY_SAVE_DYN_HEADER", "chinese"): "此存档中的玩家世家:",
    ("Multiplayer.dbt", "10294", "_MP_LOBBY_SAVE_DYN_NONE", "chinese"): "(此存档没有记录玩家世家信息)",
    ("Multiplayer.dbt", "10295", "_MP_SYS_SAVE_SELECTION_CANCELLED", "chinese"): "已取消选择存档",
    ("Multiplayer.dbt", "10296", "_MP_OVERVIEW_TITLE", "chinese"): "Steam 多人模式",
    ("Multiplayer.dbt", "10297", "_MP_OVERVIEW_COL_PLAYER", "chinese"): "玩家",
    ("Multiplayer.dbt", "10298", "_MP_OVERVIEW_COL_ASSETS", "chinese"): "资产",
    ("Multiplayer.dbt", "10299", "_MP_OVERVIEW_COL_PING", "chinese"): "延迟",
    ("Multiplayer.dbt", "10300", "_MP_OVERVIEW_COL_FAMILY", "chinese"): "当前家族",
    ("Multiplayer.dbt", "10301", "_MP_OVERVIEW_ROLE_HOST", "chinese"): "主机",
    ("Multiplayer.dbt", "10302", "_MP_OVERVIEW_ROLE_YOU", "chinese"): "你",
    ("Multiplayer.dbt", "10303", "_MP_OVERVIEW_ROLE_CLIENT", "chinese"): "客户端",
    ("Multiplayer.dbt", "10304", "_MP_OVERVIEW_UNKNOWN_PLAYER", "chinese"): "玩家",
    ("Multiplayer.dbt", "10305", "_MP_OVERVIEW_NO_PLAYERS", "chinese"): "未找到 Steam 玩家。",
    ("Multiplayer.dbt", "10307", "_MP_LOBBY_ADV_MARKETITEMS", "chinese"): "市场初始商品",
    ("Multiplayer.dbt", "10308", "_MP_LOBBY_ADV_PLEVEL", "chinese"): "玩家初始等级",
    ("Multiplayer.dbt", "10309", "_MP_LOBBY_ADV_ALEVEL", "chinese"): "AI 初始等级",
    ("Multiplayer.dbt", "10313", "_MP_LOBBY_ADV_AITARGET", "chinese"): "AI 以玩家为目标",
    ("Multiplayer.dbt", "10315", "_MP_LOBBY_ADV_AITRUCE", "chinese"): "AI 休战(回合)",
    ("Multiplayer.dbt", "10317", "_MP_LOBBY_ADV_TIP_PMONEY", "chinese"): "每个玩家世家的初始金钱。",
    ("Multiplayer.dbt", "10318", "_MP_LOBBY_ADV_TIP_AMONEY", "chinese"): "每个 AI 世家的初始金钱。",
    ("Multiplayer.dbt", "10319", "_MP_LOBBY_ADV_TIP_PTITLE", "chinese"): "每个玩家开始时的贵族头衔。",
    ("Multiplayer.dbt", "10320", "_MP_LOBBY_ADV_TIP_ATITLE", "chinese"): "每个 AI 世家开始时的贵族头衔。",
    ("Multiplayer.dbt", "10321", "_MP_LOBBY_ADV_TIP_CLASSES", "chinese"): "点击职业以禁止或允许。被禁止的职业无法在创建角色时选择。",
    ("Multiplayer.dbt", "10322", "_MP_LOBBY_ADV_TIP_PRES", "chinese"): "每个玩家世家开始游戏时拥有的建筑物。",
    ("Multiplayer.dbt", "10323", "_MP_LOBBY_ADV_TIP_ARES", "chinese"): "每个 AI 世家开始游戏时拥有的建筑物。",
    ("Multiplayer.dbt", "10324", "_MP_LOBBY_ADV_TIP_MARKET", "chinese"): "游戏开始时城镇市场的库存量。",
    ("Multiplayer.dbt", "10325", "_MP_LOBBY_ADV_TIP_PLEVEL", "chinese"): "每个玩家的第一个角色开始时的等级。每个等级都会提供可分配的技能点。",
    ("Multiplayer.dbt", "10326", "_MP_LOBBY_ADV_TIP_ALEVEL", "chinese"): "AI 世家首领开始时的角色等级。",

    # Tables.dbt
    ("Tables.dbt", "1", "", "name"): "文本",
    ("Tables.dbt", "2", "", "name"): "成就",
    ("Tables.dbt", "3", "", "name"): "多人模式",

    # TipLabels.dbt
    ("TipLabels.dbt", "1", "_TIP_BUILDING_PRICE_LABEL_+0", "english"): "价格:",
    ("TipLabels.dbt", "2", "_TIP_BUILDING_LEVEL_LABEL_+0", "english"): "等级:",
    ("TipLabels.dbt", "4", "_TIP_BUILDING_HP_UNIT_+0", "english"): "生命值",
    ("TipLabels.dbt", "5", "_TIP_TITLE_PRICE_LABEL_+0", "english"): "价格:",
    ("TipLabels.dbt", "6", "_TIP_TITLE_IMPFAME_LABEL_+0", "english"): "需要国家名声:",
    ("TipLabels.dbt", "9", "_TIP_PENALTY_SEVERITY_LABEL_+0", "english"): "判决严峻度:",
    ("TipLabels.dbt", "11", "_TIP_LAW_EVIDENCE_+0", "english"): "在法院中视为证据",
    ("TipLabels.dbt", "12", "_TIP_LAW_DURATION_LABEL_+0", "english"): "有效时间:",
    ("TipLabels.dbt", "13", "_TIP_LAW_DURATION_UNIT_+0", "english"): "天",
    ("TipLabels.dbt", "14", "_TIP_ABILITY_SKILLREQ_LABEL_+0", "english"): "需要技能:",
    ("TipLabels.dbt", "15", "_TIP_ABILITY_SKILLREQ_LEVEL_+0", "english"): "等级",
    ("TipLabels.dbt", "16", "_TIP_ABILITY_MINLEVEL_LABEL_+0", "english"): "最小角色等级:",
    ("TipLabels.dbt", "18", "_TIP_OFFICE_LEVEL_LABEL_+0", "english"): "需要拓居地等级",
    ("TipLabels.dbt", "19", "_TIP_OFFICE_LEVEL_AT_LEVEL_+0", "english"): "等级为",
    ("TipLabels.dbt", "20", "_TIP_OFFICE_LEVEL_ONLY_LEVEL_+0", "english"): "只有等级",
    ("TipLabels.dbt", "21", "_TIP_OFFICE_LEVEL_NOT_YET_+0", "english"): "尚未可用",
    ("TipLabels.dbt", "22", "_TIP_OFFICE_INCOME_LABEL_+0", "english"): "收入：",
    ("TipLabels.dbt", "23", "_TIP_OFFICE_REPLACED_BY_PREFIX_+0", "english"): "不在此拓居地等级 -",
    ("TipLabels.dbt", "24", "_TIP_OFFICE_REPLACED_BY_USES_+0", "english"): "使用",
    ("TipLabels.dbt", "25", "_TIP_OFFICE_REPLACED_BY_SUFFIX_+0", "english"): "替代",
    ("TipLabels.dbt", "27", "_TIP_OFFICE_NOT_APPLICABLE_PREFIX_+0", "english"): "不适用于此拓居地等级",
    ("TipLabels.dbt", "29", "_TIP_UPGRADE_PRODUCES_LABEL_+0", "english"): "生产:",
    ("TipLabels.dbt", "30", "_TIP_UPGRADE_GRANTS_MEASURE_LABEL_+0", "english"): "授予行动:",
    ("TipLabels.dbt", "31", "_TIP_NAV_JUMP_PREFIX_+0", "english"): "按 F 切换画面到",
    ("TipLabels.dbt", "32", "_TIP_NAV_CYCLE_+0", "english"): "按 Page Up / Page Down 切换拓居地",

    # Text.dbt, exact or short UI strings.
    ("Text.dbt", "16544", "_TIP_OFFICE_LEVEL_NOCTX_+0", "chinese"): "需要拓居地等级 %d",
    ("Text.dbt", "16545", "_TIP_OFFICE_LEVEL_MET_+0", "chinese"): "需要拓居地等级 %d - %s 等级为 %d",
    ("Text.dbt", "16546", "_TIP_OFFICE_LEVEL_UNMET_+0", "chinese"): "需要拓居地等级 %d - %s 只有等级 %d (尚未可用)",
    ("Text.dbt", "16547", "_TIP_OFFICE_INCOME_+0", "chinese"): "收入：%d",
    ("Text.dbt", "16548", "_TIP_OFFICE_HELD_BY_+0", "chinese"): "当前由以下角色担任: %s",
    ("Text.dbt", "16549", "_TIP_OFFICE_VACANT_+0", "chinese"): "%s 当前有空缺",
    ("Text.dbt", "16550", "_TIP_UPGRADE_PRODUCES_+0", "chinese"): "生产: %s",
    ("Text.dbt", "16551", "_TIP_UPGRADE_GRANTS_MEASURE_+0", "chinese"): "授予行动: %s",
    ("Text.dbt", "16552", "_TIP_NAV_JUMP_+0", "chinese"): "按 F 切换画面到 %s",
    ("Text.dbt", "16553", "_TIP_NAV_CYCLE_+0", "chinese"): "按 Page Up / Page Down 切换拓居地",
    ("Text.dbt", "16555", "_TIP_TITLE_PRICE_+0", "chinese"): "价格: %d",
    ("Text.dbt", "16556", "_TIP_TITLE_IMPFAME_+0", "chinese"): "需要国家名声: %d",
    ("Text.dbt", "16560", "_TIP_LAW_EVIDENCE_+0", "chinese"): "在法院中视为证据",
    ("Text.dbt", "16561", "_TIP_LAW_DURATION_+0", "chinese"): "有效时间: %.0f 天",
    ("Text.dbt", "16562", "_TIP_ABILITY_SKILLREQ_+0", "chinese"): "需要技能: %s 等级 %d",
    ("Text.dbt", "16563", "_TIP_ABILITY_MINLEVEL_+0", "chinese"): "最小角色等级: %d",
    ("Text.dbt", "16567", "_TIP_BUILDING_PRICE_+0", "chinese"): "价格: %d",
    ("Text.dbt", "16569", "_TIP_BUILDING_LEVEL_+0", "chinese"): "等级: %d",
    ("Text.dbt", "16571", "PENALTY_FINE", "chinese"): "罚款",
    ("Text.dbt", "16574", "_BUILDBUILDING_OFFICE_+0", "chinese"): "公职",
    ("Text.dbt", "16575", "_BUILDBUILDING_OFFICE_COUNT_+0", "chinese"): "%1n / %2n 此拓居地最多",
    ("Text.dbt", "16576", "_BUILDBUILDING_OFFICE_LOCKED_+0", "chinese"): "不适用于此拓居地等级",
    ("Text.dbt", "16577", "_MEASURE_ToggleCityNeeds_NAME_+0", "chinese"): "城市需求",
    ("Text.dbt", "16579", "CityNeedsSheet_+0", "chinese"): "城市需求",
    ("Text.dbt", "16580", "_CITYNEEDS_TITLE", "chinese"): "城市需求",
    ("Text.dbt", "16582", "_CITYNEEDS_CLOSE", "chinese"): "关闭",
    ("Text.dbt", "16584", "_CITYNEEDS_CAT_FOOD", "chinese"): "食物",
    ("Text.dbt", "16585", "_CITYNEEDS_CAT_AMUSEMENT", "chinese"): "娱乐",
    ("Text.dbt", "16586", "_CITYNEEDS_CAT_CLOTHES", "chinese"): "衣服",
    ("Text.dbt", "16587", "_CITYNEEDS_CAT_WEAPON", "chinese"): "武器",
    ("Text.dbt", "16588", "_CITYNEEDS_CAT_ARMOR", "chinese"): "防具",
    ("Text.dbt", "16589", "_CITYNEEDS_CAT_ILLNESS", "chinese"): "健康",
    ("Text.dbt", "16590", "_CITYNEEDS_CAT_SPECIAL", "chinese"): "特殊",
    ("Text.dbt", "16591", "_CITYNEEDS_CAT_LUXORY", "chinese"): "奢侈品",
    ("Text.dbt", "16592", "_CITYNEEDS_CAT_SOCIAL", "chinese"): "社会的",
    ("Text.dbt", "16593", "_CITYNEEDS_CAT_REPAIR", "chinese"): "修理",
    ("Text.dbt", "16594", "_CITYNEEDS_COL_ITEM", "chinese"): "物品",
    ("Text.dbt", "16595", "_CITYNEEDS_COL_CATEGORY", "chinese"): "类别",
    ("Text.dbt", "16596", "_CITYNEEDS_COL_DEMAND", "chinese"): "需求",
    ("Text.dbt", "16597", "_CITYNEEDS_COL_STOCK", "chinese"): "库存",
    ("Text.dbt", "16598", "_CITYNEEDS_COL_PRICE", "chinese"): "价格",
    ("Text.dbt", "16600", "_CITYNEEDS_CAT_RESOURCE", "chinese"): "资源",
    ("Text.dbt", "16601", "_CITYNEEDS_CAT_GATHERING", "chinese"): "原料",
    ("Text.dbt", "16603", "_CITYNEEDS_CAT_OTHER", "chinese"): "其它",
    ("Text.dbt", "16604", "_CITYNEEDS_FILTER_ALL", "chinese"): "全部",
    ("Text.dbt", "16605", "_CITYNEEDS_FILTER_RES", "chinese"): "资源",
    ("Text.dbt", "16608", "_CITYNEEDS_COL_BUY", "chinese"): "买",
    ("Text.dbt", "16609", "_CITYNEEDS_COL_SELL", "chinese"): "卖",
    ("Text.dbt", "16611", "_CITYNEEDS_COL_SHORTAGE", "chinese"): "状态",
    ("Text.dbt", "16612", "_CITYNEEDS_COL_REVENUE", "chinese"): "收入",
    ("Text.dbt", "16613", "_CITYNEEDS_RANK_DESTITUTE", "chinese"): "一贫如洗",
    ("Text.dbt", "16616", "_CITYNEEDS_RANK_RICH", "chinese"): "富裕",
    ("Text.dbt", "16620", "_CITYNEEDS_STATUS_LOW", "chinese"): "低",
    ("Text.dbt", "16621", "_CITYNEEDS_STATUS_OK", "chinese"): "正常",
    ("Text.dbt", "16623", "_CITYNEEDS_PANEL_TITLE", "chinese"): "城市需求",
    ("Text.dbt", "16624", "_CITYNEEDS_TITLE_FOR_CITY", "chinese"): "城市需求 - %s",
    ("Text.dbt", "16643", "_CITYNEEDS_CAT_USEABLE", "chinese"): "可用",
    ("Text.dbt", "16644", "_CITYNEEDS_CAT_SUPPLY", "chinese"): "补给",
    ("Text.dbt", "16645", "_CITYNEEDS_CAT_ARTIFACT", "chinese"): "道具",
    ("Text.dbt", "16646", "_CITYNEEDS_CAT_ANIMAL", "chinese"): "动物",
    ("Text.dbt", "16648", "_CITYNEEDS_CAT_QUEST", "chinese"): "任务",
    ("Text.dbt", "16704", "_MP_UNSTUCK_CANCEL_BTN", "chinese"): "取消",
    ("Text.dbt", "16709", "_MP_UNSTUCK_DENY_BTN", "chinese"): "拒绝",
    ("Text.dbt", "16714", "_MP_SAVE_PLAYERS_HEADER", "chinese"): "此存档中的玩家世家:",
    ("Text.dbt", "16715", "_MP_SAVE_PLAYERS_NONE", "chinese"): "(此存档没有记录玩家世家信息)",
    ("Text.dbt", "16719", "_CITYNEEDS_SEARCH", "chinese"): "搜索:",
    ("Text.dbt", "16725", "_TOOLTIP_FORSALE", "chinese"): "可购买:",
    ("Text.dbt", "16744", "_KR_BTN_CANCEL_+0", "chinese"): "取消",
    ("Text.dbt", "16745", "_KR_BTN_OK_+0", "chinese"): "确认",
    ("Text.dbt", "16760", "_KR_CAT_1_+0", "chinese"): "资源",
    ("Text.dbt", "16761", "_KR_CAT_2_+0", "chinese"): "食物",
    ("Text.dbt", "16762", "_KR_CAT_3_+0", "chinese"): "武器与铁匠制品",
    ("Text.dbt", "16763", "_KR_CAT_4_+0", "chinese"): "纺织品",
    ("Text.dbt", "16764", "_KR_CAT_5_+0", "chinese"): "炼金术",
    ("Text.dbt", "16765", "_KR_CAT_7_+0", "chinese"): "手艺",
    ("Text.dbt", "16766", "_KR_LORD_1_+0", "chinese"): "荷兰",
    ("Text.dbt", "16767", "_KR_LORD_2_+0", "chinese"): "汉萨",
    ("Text.dbt", "16768", "_KR_LORD_3_+0", "chinese"): "德意志",
    ("Text.dbt", "16769", "_KR_LORD_4_+0", "chinese"): "奥地利",
    ("Text.dbt", "16770", "_KR_LORD_5_+0", "chinese"): "匈牙利",
    ("Text.dbt", "16771", "_KR_LORD_6_+0", "chinese"): "德拉根瑟尔",
    ("Text.dbt", "16772", "_KR_LORD_7_+0", "chinese"): "法兰西",
    ("Text.dbt", "16773", "_KR_LORD_8_+0", "chinese"): "英格兰",

    # Tooltips.dbt, exact title reuse only.
    ("Tooltips.dbt", "1", "GOLD", "title"): "金",
    ("Tooltips.dbt", "8", "ALDERMAN", "title"): "议员",
    ("Tooltips.dbt", "12", "DYNASTY", "title"): "世家",
    ("Tooltips.dbt", "15", "EVIDENCE", "title"): "证据",
    ("Tooltips.dbt", "21", "APPRENTICE", "title"): "学徒",
    ("Tooltips.dbt", "27", "BLACK_DEATH", "title"): "黑死病",
    ("Tooltips.dbt", "31", "IMMUNITY", "title"): "免诉权",
    ("Tooltips.dbt", "32", "BRIBERY", "title"): "贿赂",
    ("Tooltips.dbt", "33", "SABOTAGE", "title"): "破坏",
    ("Tooltips.dbt", "34", "ESPIONAGE", "title"): "间谍",
    ("Tooltips.dbt", "39", "MARRIAGE", "title"): "结婚",
    ("Tooltips.dbt", "42", "CRUSADE", "title"): "十字军圣战",
    ("Tooltips.dbt", "298", "HOSPITAL", "title"): "医疗所",
    ("Tooltips.dbt", "299", "BANK", "title"): "银行",
    ("Tooltips.dbt", "304", "FARM", "title"): "农场",
    ("Tooltips.dbt", "305", "TAVERN", "title"): "小客栈",
    ("Tooltips.dbt", "309", "BUILDING", "title"): "建筑物",
    ("Tooltips.dbt", "310", "CITY", "title"): "城市",
    ("Tooltips.dbt", "313", "WOOL", "title"): "羊毛",
    ("Tooltips.dbt", "323", "LEVEL", "title"): "等级",
    ("Tooltips.dbt", "326", "CHARISMA", "title"): "魅力",
    ("Tooltips.dbt", "327", "RHETORIC", "title"): "口才",
    ("Tooltips.dbt", "328", "EMPATHY", "title"): "洞察力",
    ("Tooltips.dbt", "329", "WORKER", "title"): "工人",
    ("Tooltips.dbt", "330", "DOCTOR", "title"): "医生",
    ("Tooltips.dbt", "331", "ALCHEMIST", "title"): "炼金术士",
    ("Tooltips.dbt", "332", "SCHOLAR", "title"): "学者",
    ("Tooltips.dbt", "334", "ROBBER", "title"): "强盗",
    ("Tooltips.dbt", "336", "JUGGLER", "title"): "骗子",
    ("Tooltips.dbt", "337", "BARD", "title"): "诗人",
}

FORBIDDEN_TERMS = ("商馆", "商管")
FORBIDDEN_FULLWIDTH_SYNTAX = ("％", "＄", "＃", "［", "］", "｜")


def main() -> int:
    apply_changes = "--apply" in sys.argv
    project = Project.load(ROOT, LANGUAGE)
    by_key = {
        (unit.file_rel, unit.record_id, unit.label, unit.field_name): unit
        for unit in project.units
    }

    selected = []
    errors: list[str] = []
    review_rows = []

    for key, text in TRANSLATIONS.items():
        unit = by_key.get(key)
        if unit is None:
            errors.append(f"missing unit: {key}")
            continue
        if any(term in text for term in FORBIDDEN_TERMS):
            errors.append(f"forbidden term in {key}: {text}")
            continue
        if any(mark in text for mark in FORBIDDEN_FULLWIDTH_SYNTAX):
            errors.append(f"fullwidth syntax mark in {key}: {text}")
            continue
        if text.count("????"):
            errors.append(f"question-mark corruption marker in {key}: {text}")
            continue
        review_row = {
            "file": unit.file_rel,
            "id": unit.record_id,
            "label": unit.label,
            "field": unit.field_name,
            "status": unit.status,
            "english": unit.source_text,
            "before": unit.current_text,
            "current": unit.current_text,
            "after": text,
            "applied": "yes" if unit.current_text == text else "no",
            "selected": "no",
            "issues": unit.issue_text(),
        }
        if unit.status not in MISSING_WORK_STATUSES and unit.current_text == text:
            review_rows.append(review_row)
            continue
        if unit.status not in MISSING_WORK_STATUSES:
            review_rows.append(review_row)
            errors.append(f"unit is not pending: {key} status={unit.status}")
            continue
        before = unit.current_text
        unit.set_text(text)
        blocking = [issue.message for issue in unit.issues() if issue.blocks_save]
        if blocking:
            review_row["issues"] = unit.issue_text()
            review_rows.append(review_row)
            errors.append(f"blocking issue in {key}: {'; '.join(blocking)}")
            continue
        try:
            project.codec.encode(text)
        except Exception as exc:  # noqa: BLE001 - report exact codec failure.
            review_rows.append(review_row)
            errors.append(f"encode failed in {key}: {exc}")
            continue
        selected.append(unit)
        review_row["before"] = before
        review_row["current"] = unit.current_text
        review_row["applied"] = "yes" if before == text else "no"
        review_row["selected"] = "yes"
        review_row["issues"] = unit.issue_text()
        review_rows.append(review_row)

    if errors:
        print("ERRORS")
        for error in errors:
            print(error)
        return 2

    REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REVIEW_PATH.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "file",
                "id",
                "label",
                "field",
                "status",
                "english",
                "before",
                "current",
                "after",
                "applied",
                "selected",
                "issues",
            ],
        )
        writer.writeheader()
        writer.writerows(review_rows)

    print(f"selected={len(selected)}")
    print(f"review={REVIEW_PATH}")
    if not apply_changes:
        print("dry_run=1")
        return 0

    try:
        backups = project.save(selected)
    except SaveValidationError as exc:
        print("SAVE_VALIDATION_ERROR")
        for message in exc.messages:
            print(message)
        return 3

    print(f"dry_run=0")
    print(f"backups={len(backups)}")
    for record in backups:
        print(f"backup={record.backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
