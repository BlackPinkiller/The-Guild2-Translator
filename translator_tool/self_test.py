from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid

from . import project as project_module
from .ai import GoogleTranslateProvider, OpenAICompatibleProvider, TranslationProviderError
from .codec_adapter import Guild2Codec, default_codec_path
from .git_history import LanguageGit, TranslationLogEntry, combine_entries, format_entries
from .history import OperationHistory, TranslationOperation, UnitChange
from .format_io import load_dbt, load_plain_text, row_key
from .project import (
    MISSING_WORK_STATUSES,
    Project,
    STATUS_MISSING_ROW,
    STATUS_MODIFIED,
    STATUS_REVIEW,
    STATUS_TRANSLATED,
    SaveValidationError,
)
from .settings import AppSettings, load_settings, save_settings
from .validation import format_tokens, validate_translation


def tool_root() -> Path:
    return Path(__file__).resolve().parents[1]


def project_root() -> Path:
    root = tool_root()
    if (root / "languages").is_dir():
        return root
    sources = root / "sources"
    if sources.is_dir():
        for candidate in sorted(sources.iterdir()):
            if candidate.is_dir() and (candidate / "languages").is_dir():
                return candidate
    return root


def assert_round_trip(root: Path) -> None:
    for path in sorted((root / "languages").rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".dbt", ".txt"}:
            continue
        doc = load_dbt(path) if path.suffix.lower() == ".dbt" else load_plain_text(path)
        rendered = doc.render_bytes()
        if rendered != path.read_bytes():
            raise AssertionError(f"round-trip changed bytes: {path}")


def assert_statuses(root: Path) -> None:
    project = Project.load(root, "#chinese", codec_root=tool_root())
    statuses = {unit.status for unit in project.units}
    expected = {"译文缺行", "未翻译(同原文)", "译文多余"}
    missing = expected - statuses
    if missing:
        raise AssertionError(f"expected statuses not found: {sorted(missing)}")
    if not any(unit.file_rel == "Text.dbt" and unit.status == STATUS_MISSING_ROW for unit in project.units):
        raise AssertionError("Text.dbt missing rows were not detected")
    if not any(unit.status in MISSING_WORK_STATUSES for unit in project.units):
        raise AssertionError("missing-work filter would be empty")
    if not any(unit.file_rel == "Guides/StartPage.txt" and unit.source_text for unit in project.units):
        raise AssertionError("Guides source files were not matched to translated Guides")
    if any(unit.file_rel == "Tables.dbt" for unit in project.units):
        raise AssertionError("Tables.dbt must not be exposed as a translation unit")


def assert_loaded_order_matches_file_lines(root: Path) -> None:
    project = Project.load(root, "#chinese", codec_root=tool_root())
    last_position: dict[str, tuple[int, int]] = {}
    for unit in project.units:
        if unit.ref.kind != "dbt":
            continue
        position = (unit.ref.display_order, unit.ref.field_order)
        previous = last_position.get(unit.file_rel)
        if previous is not None and position < previous:
            raise AssertionError(f"table order diverged from {unit.file_rel} line order")
        last_position[unit.file_rel] = position


def copy_project_subset(src_root: Path, dst_root: Path) -> None:
    (dst_root / "encoder" / "data").mkdir(parents=True)
    shutil.copy2(tool_root() / "encoder" / "data" / "guild2_chinese_codec.json", dst_root / "encoder" / "data")
    (dst_root / "languages" / "#chinese").mkdir(parents=True)
    for name in ["Text.dbt", "Tooltips.dbt"]:
        shutil.copy2(src_root / "languages" / name, dst_root / "languages" / name)
        shutil.copy2(src_root / "languages" / "#chinese" / name, dst_root / "languages" / "#chinese" / name)


def make_temp_project(root: Path, prefix: str) -> Path:
    temp = Path(tempfile.gettempdir()) / f"{prefix}{uuid.uuid4().hex[:8]}"
    temp.mkdir(parents=True, exist_ok=False)
    copy_project_subset(root, temp)
    return temp


def safe_rmtree(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except OSError:
        pass


def assert_save_existing(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_existing_")
    project = Project.load(temp, "#chinese")
    unit = next(unit for unit in project.units if unit.file_rel == "Text.dbt" and unit.status != STATUS_MISSING_ROW)
    target_path = temp / "languages" / "#chinese" / "Text.dbt"
    original = target_path.read_bytes()
    before_doc = load_dbt(target_path)
    unit.set_text(unit.current_text + "!")
    result = project.save([unit])
    if not result.changed_files:
        raise AssertionError("save_existing did not write a file")
    if target_path.read_bytes() == original:
        raise AssertionError("save_existing did not update the target file")
    after_doc = load_dbt(target_path)
    changed_key = (int(unit.record_id), unit.label)
    if len(before_doc.rows) != len(after_doc.rows):
        raise AssertionError("save_existing changed the existing row count")
    for before_row, after_row in zip(before_doc.rows, after_doc.rows):
        if row_key("Text.dbt", before_row) != changed_key and before_row.original_line != after_row.original_line:
            raise AssertionError("save_existing rewrote an untouched DBT line")
    if (temp / "backups").exists():
        raise AssertionError("Git-backed save unexpectedly created a backup directory")
    safe_rmtree(temp)


def assert_save_missing(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_missing_")
    project = Project.load(temp, "#chinese")
    unit = next(unit for unit in project.units if unit.file_rel == "Text.dbt" and unit.status == STATUS_MISSING_ROW)
    unit.set_text(unit.source_text or "test")
    result = project.save([unit])
    if not result.changed_files:
        raise AssertionError("save_missing did not write a file")
    reloaded = Project.load(temp, "#chinese")
    saved = [item for item in reloaded.units if item.file_rel == unit.file_rel and item.record_id == unit.record_id and item.label == unit.label]
    if not saved or saved[0].status == STATUS_MISSING_ROW:
        raise AssertionError("inserted missing row did not reload as an existing row")
    safe_rmtree(temp)


def assert_missing_insertions_follow_file_order(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_missing_order_")
    project = Project.load(temp, "#chinese")
    missing = [
        unit
        for unit in project.units
        if unit.file_rel == "Text.dbt" and unit.status == STATUS_MISSING_ROW and unit.source_text
    ][:2]
    if len(missing) < 2:
        safe_rmtree(temp)
        return
    for unit in missing:
        unit.set_text(unit.source_text)
    project.save(list(reversed(missing)))
    after = load_dbt(temp / "languages" / "#chinese" / "Text.dbt")
    positions = []
    for unit in missing:
        key = (int(unit.record_id), unit.label)
        row = after.row_index.get(key)
        if row is None:
            raise AssertionError("missing row was not inserted")
        positions.append((unit.ref.source_order, row.line_index))
    if [line for _source, line in sorted(positions)] != sorted(line for _source, line in positions):
        raise AssertionError("missing rows were not inserted in original file order")
    safe_rmtree(temp)


def assert_unsaved_translation_status(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_status_")
    project = Project.load(temp, "#chinese")
    unit = next(item for item in project.units if item.status == STATUS_MISSING_ROW and item.source_text)
    if unit.display_status() != STATUS_MISSING_ROW:
        raise AssertionError("an untouched missing row no longer reported missing status")
    unit.set_text("AI translated")
    if unit.display_status() != "已翻译" or unit.filter_status() != "已翻译":
        raise AssertionError("an unsaved translated unit did not report translated status")
    translated = next(item for item in project.units if item.status == STATUS_TRANSLATED)
    translated.set_text(translated.current_text + "x")
    if translated.display_status() != STATUS_MODIFIED or translated.filter_status() != STATUS_TRANSLATED:
        raise AssertionError("an edited translated unit did not keep a translated filter state with a modified marker")
    before_bytes = (temp / "languages" / "#chinese" / translated.file_rel).read_bytes()
    translated.set_text("")
    removed = project.save([translated])
    if not removed.changed_files or [item.uid for item in removed.cleared_empty_units] != [translated.uid]:
        raise AssertionError("an empty translation did not remove its existing override")
    if (temp / "languages" / "#chinese" / translated.file_rel).read_bytes() == before_bytes:
        raise AssertionError("an empty translation left its existing target row intact")
    safe_rmtree(temp)


def assert_mod_label_match_inserts_source_formatted_row(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_label_match_")
    try:
        target_path = temp / "languages" / "#chinese" / "Text.dbt"
        target_doc = load_dbt(target_path)
        target_row = target_doc.rows[0]
        original_key = row_key("Text.dbt", target_row)
        source_doc = load_dbt(temp / "languages" / "Text.dbt")
        source_row = source_doc.row_index[original_key]
        old_line = target_row.original_line
        new_line = old_line.replace(str(target_row.row_id), str(target_row.row_id + 900000), 1)
        target_path.write_bytes(target_doc.text.replace(old_line, new_line, 1).encode(target_doc.profile.encoding))

        project = Project.load(temp, "#chinese")
        unit = next(
            item
            for item in project.units
            if item.file_rel == "Text.dbt" and item.record_id == str(source_row.row_id) and item.label == original_key[1]
        )
        if not unit.needs_review or unit.display_status() != STATUS_REVIEW:
            raise AssertionError("a unique mod label match was not marked for review")
        if unit.ref.target_row is not None or not unit.is_dirty:
            raise AssertionError("label match did not stage a source-row insertion")
        unit.set_text("")
        removed = project.save([unit])
        if not removed.changed_files or [item.uid for item in removed.cleared_empty_units] != [unit.uid]:
            raise AssertionError("an empty label-match translation did not remove its old override")
        if original_key in load_dbt(target_path).row_index:
            raise AssertionError("an empty label-match translation inserted a source row")

        # Restore the temporary legacy row so the next branch verifies that a
        # non-empty review edit inserts a new source-formatted row.
        target_path.write_bytes(target_doc.text.replace(old_line, new_line, 1).encode(target_doc.profile.encoding))
        project = Project.load(temp, "#chinese")
        unit = next(
            item
            for item in project.units
            if item.file_rel == "Text.dbt" and item.record_id == str(source_row.row_id) and item.label == original_key[1]
        )
        unit.set_text(unit.current_text + "x")
        if unit.needs_review or unit.display_status() != STATUS_MODIFIED:
            raise AssertionError("editing a review item did not clear its temporary review state")
        project.save([unit])
        saved = load_dbt(target_path)
        inserted = saved.row_index.get(original_key)
        legacy_key = (target_row.row_id + 900000, original_key[1])
        if inserted is None or legacy_key not in saved.row_index:
            raise AssertionError("label match did not retain the legacy extra row and insert the source key")
        source_prefix = source_row.original_line[: source_row.fields[0].end]
        inserted_prefix = inserted.original_line[: inserted.fields[0].end]
        if source_prefix != inserted_prefix:
            raise AssertionError("inserted label-match row did not preserve the source file layout")
    finally:
        safe_rmtree(temp)


def assert_project_history_settings(root: Path) -> None:
    temp = Path(tempfile.gettempdir()) / f"translator_tool_smoke_settings_{uuid.uuid4().hex[:8]}"
    previous = os.environ.get("LOCALAPPDATA")
    try:
        os.environ["LOCALAPPDATA"] = str(temp)
        expected = [str(root / f"project-{number}") for number in range(10)]
        save_settings(AppSettings(last_project_root=expected[0], recent_project_roots=expected))
        loaded = load_settings()
        if loaded.last_project_root != expected[0] or loaded.recent_project_roots != expected[:8]:
            raise AssertionError("project folder history was not persisted safely")
    finally:
        if previous is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = previous
        safe_rmtree(temp)


def assert_external_project_uses_tool_codec(root: Path) -> None:
    temp = Path(tempfile.gettempdir()) / f"translator_tool_smoke_external_codec_{uuid.uuid4().hex[:8]}"
    temp.mkdir(parents=True, exist_ok=False)
    (temp / "languages" / "#chinese").mkdir(parents=True)
    for name in ["Text.dbt", "Tooltips.dbt"]:
        shutil.copy2(root / "languages" / name, temp / "languages" / name)
        shutil.copy2(root / "languages" / "#chinese" / name, temp / "languages" / "#chinese" / name)
    project = Project.load(temp, "#chinese", codec_root=tool_root())
    if not project.units:
        raise AssertionError("external project did not load with the tool codec")
    LanguageGit(temp, codec_root=tool_root())
    safe_rmtree(temp)


def assert_validation_blocks(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_validation_")
    project = Project.load(temp, "#chinese")
    unit = next(unit for unit in project.units if "%1n" in unit.source_text)
    unit.set_text("坏％1ｎ")
    try:
        project.save([unit])
    except SaveValidationError:
        safe_rmtree(temp)
        return
    raise AssertionError("fullwidth placeholder damage was not blocked")


def assert_ignore_cache(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_ignore_")
    project = Project.load(temp, "#chinese")
    unit = next(item for item in project.units if item.status in MISSING_WORK_STATUSES)
    uid = unit.uid
    project.set_unit_ignored(unit, True)
    reloaded = Project.load(temp, "#chinese")
    reloaded_unit = next(item for item in reloaded.units if item.uid == uid)
    if not reloaded_unit.ignored:
        raise AssertionError("ignored translation unit was not persisted in cache")
    reloaded.set_unit_ignored(reloaded_unit, False)
    reloaded_again = Project.load(temp, "#chinese")
    if next(item for item in reloaded_again.units if item.uid == uid).ignored:
        raise AssertionError("ignored translation unit was not removed from cache")
    safe_rmtree(temp)


def assert_codec(root: Path) -> None:
    codec = Guild2Codec.load(default_codec_path(tool_root()))
    text = "测试"
    if codec.decode(codec.encode(text)) != text:
        raise AssertionError("codec encode/decode did not round-trip")


def assert_font_glyph_validation(root: Path) -> None:
    project = Project.load(root, "#chinese", codec_root=tool_root())
    unit = next(unit for unit in project.units if unit.source_text)
    unit.set_text("字😀")
    if not any(issue.code == "font-glyph" and "😀" in issue.message for issue in unit.issues()):
        raise AssertionError("font glyph validation did not flag an unsupported character")
    previous = project_module.ENABLE_FONT_GLYPH_VALIDATION
    try:
        project_module.ENABLE_FONT_GLYPH_VALIDATION = False
        if any(issue.code == "font-glyph" for issue in unit.issues()):
            raise AssertionError("internal font glyph switch did not disable validation")
    finally:
        project_module.ENABLE_FONT_GLYPH_VALIDATION = previous


class FakeGoogleTransport:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_url = ""

    def get_json(self, url: str):
        self.last_url = url
        return [[[self.response, "", None, None]]]

    def post_json(self, url: str, payload, headers):
        raise AssertionError("Google provider must not issue a POST request")


def assert_ai_token_protection() -> None:
    transport = FakeGoogleTransport("测试 __TG_FMT_0000__")
    provider = GoogleTranslateProvider("https://example.invalid/translate", "en", "zh-CN", transport)
    translated = provider.translate("Cost: %1t", dbt_field=True)
    if translated != "测试 %1t" or "__TG_FMT_0000__" not in transport.last_url:
        raise AssertionError("AI translation did not preserve protected format tokens")
    broken = GoogleTranslateProvider(
        "https://example.invalid/translate", "en", "zh-CN", FakeGoogleTransport("测试内容")
    )
    try:
        broken.translate("Cost: %1t", dbt_field=True)
    except TranslationProviderError:
        return
    raise AssertionError("AI result missing a protected token was accepted")


def assert_linebreak_format_is_ignored() -> None:
    issues = validate_translation("First$NSecond", "First Second", dbt_field=True)
    if any(issue.blocks_save and "$N" in issue.message for issue in issues):
        raise AssertionError("$N line-break differences must not block translation saves")


def assert_guild2_format_grammar() -> None:
    syntax = (
        "%1NAME %2n %3i %4f %5t %6c %7z %8j %9s %10l "
        "%11GG %12GN %13GT %% %> %< %14SN %15Sn %16SV %17Sv %18SZ %19Sz "
        "%20SK %21ST %22SA %23SD %24SB %25SL %26DN %27DS "
        "$N $Z $L $R $T $> $< $C[1,2,3,255] $F[Body] $S[12] $B[label] "
        "$[ornament$] #E[NT_NEUTRAL] #SP+ #SP- @L_TEST_KEY_+n @T\"fallback\""
    )
    tokens = format_tokens(syntax)
    required = {"%1NAME", "%11GG", "%14SN", "$C[1,2,3,255]", "$[ornament$]", "#SP+", "@L_TEST_KEY_+n"}
    if not required.issubset(tokens):
        raise AssertionError("Guild 2 format grammar did not recognize all core token forms")
    colors = format_tokens("$C[255,0,0] $C[115, 5,20] $C[255,90,90,255]")
    if len(colors) != 3:
        raise AssertionError("RGB/RGBA color directives with optional whitespace were not recognized")
    if any(issue.blocks_save for issue in validate_translation(syntax, syntax, dbt_field=False)):
        raise AssertionError("valid Guild 2 syntax was rejected")
    compatible = validate_translation("Name: %1SN", "姓名：%1SV", dbt_field=True)
    if any(issue.blocks_save for issue in compatible) or not any(issue.code == "argument-variant" for issue in compatible):
        raise AssertionError("SN/SV compatible character-name variant was not accepted")
    wrong_index = validate_translation("Name: %1SN", "姓名：%2SN", dbt_field=True)
    if not any(issue.blocks_save and issue.code == "argument-index" for issue in wrong_index):
        raise AssertionError("invalid argument index did not block saving")
    wrong_type = validate_translation("Name: %1SN", "数值：%1n", dbt_field=True)
    if not any(issue.blocks_save and issue.code == "argument-type" for issue in wrong_type):
        raise AssertionError("incompatible argument type did not block saving")
    unknown = validate_translation("Plain text", "未知 %A", dbt_field=True)
    if any(issue.blocks_save for issue in unknown) or not any(issue.code == "unknown-format" for issue in unknown):
        raise AssertionError("unknown format token was not reduced to a non-blocking warning")


class FakeStreamingTransport:
    def get_json(self, url: str):
        raise AssertionError("LLM suggestion must not issue a GET request")

    def post_json(self, url: str, payload, headers):
        raise AssertionError("stream-capable transport should use SSE")

    def post_sse(self, url: str, payload, headers):
        if not payload.get("stream"):
            raise AssertionError("LLM suggestion did not request streaming")
        yield {"choices": [{"delta": {"content": "推荐译文：测试"}}]}
        yield {"choices": [{"delta": {"content": "\n说明：保留 %1s"}}]}


def assert_llm_suggestion_stream() -> None:
    provider = OpenAICompatibleProvider(
        "https://example.invalid/v1", "test-model", "test-key", FakeStreamingTransport()
    )
    response = "".join(provider.stream_suggestion("Hello %1s", ""))
    if "推荐译文：测试" not in response or "说明：保留 %1s" not in response:
        raise AssertionError("LLM suggestion stream was not assembled correctly")


def assert_operation_history() -> None:
    values = {"first": "旧一", "second": "旧二"}
    history = OperationHistory()
    history.push(TranslationOperation("连续编辑", (UnitChange("first", "旧一", "新一"),)))
    values["first"] = "新一"
    history.push(
        TranslationOperation(
            "AI 批量翻译",
            (UnitChange("first", "新一", "AI 一"), UnitChange("second", "旧二", "AI 二")),
        )
    )
    values.update({"first": "AI 一", "second": "AI 二"})
    history.undo(lambda uid, text: values.__setitem__(uid, text))
    if values != {"first": "新一", "second": "旧二"}:
        raise AssertionError("batch undo did not restore exactly one whole operation")
    history.undo(lambda uid, text: values.__setitem__(uid, text))
    if values["first"] != "旧一" or values["second"] != "旧二":
        raise AssertionError("undo crossed or missed a translation unit")
    history.redo(lambda uid, text: values.__setitem__(uid, text))
    if values["first"] != "新一":
        raise AssertionError("redo did not restore the expected operation")


def assert_git_history(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_git_")
    git = LanguageGit(temp)
    git.ensure_repository(AppSettings())
    project = Project.load(temp, "#chinese")
    unit = next(item for item in project.units if item.file_rel == "Text.dbt" and item.status != STATUS_MISSING_ROW)
    unit.set_text(unit.current_text + "测试")
    result = project.save([unit])
    commit = git.commit_saved(result.changed_files, result.saved_units)
    if commit is None:
        raise AssertionError("Git commit was not created after saving")
    entries = git.entries_for_commit(commit.full_hash)
    if not entries or entries[0].translated_text != unit.current_text:
        raise AssertionError("Git history did not decode the saved translation entry")
    rendered = format_entries(entries)
    if "→" not in rendered or "Text.dbt" not in rendered:
        raise AssertionError("Git history is not rendering original-to-translation output")
    safe_rmtree(temp)


def assert_git_pending_is_scoped_to_active_language(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_git_scope_")
    git = LanguageGit(temp)
    git.ensure_repository(AppSettings())
    source_path = temp / "languages" / "Text.dbt"
    source_path.write_bytes(source_path.read_bytes() + b"\n")
    if git.has_pending_changes():
        raise AssertionError("source-language changes must not show as pending translation commits")
    target_path = temp / "languages" / "#chinese" / "Text.dbt"
    target_path.write_bytes(target_path.read_bytes() + b"\n")
    if not git.has_pending_changes():
        raise AssertionError("active-language changes were not detected as pending")
    safe_rmtree(temp)


def assert_git_recovers_stale_index_lock(root: Path) -> None:
    temp = make_temp_project(root, "translator_tool_smoke_git_lock_")
    try:
        git = LanguageGit(temp)
        git.ensure_repository(AppSettings())
        target = temp / "languages" / "#chinese" / "Text.dbt"
        target.write_bytes(target.read_bytes() + b"\n")
        lock = temp / "languages" / ".git" / "index.lock"
        lock.write_bytes(b"")
        stale = time.time() - LanguageGit.STALE_INDEX_LOCK_SECONDS - 1
        os.utime(lock, (stale, stale))
        if git.commit_pending() is None:
            raise AssertionError("stale Git index lock was not recovered for pending commit")
        if lock.exists():
            raise AssertionError("stale Git index lock was not removed")
    finally:
        safe_rmtree(temp)


def assert_combined_git_history_format() -> None:
    early = TranslationLogEntry("新增", "Text.dbt", "10", "Greeting", "Text", "Hello", "你好")
    later = TranslationLogEntry("更新", "Text.dbt", "10", "Greeting", "Text", "Hello", "您好")
    other = TranslationLogEntry("新增", "Tooltips.dbt", "2", "Tip", "Text", "Save", "保存")
    combined = combine_entries(((early, other), (later,)))
    if combined != [later, other]:
        raise AssertionError("combined history did not keep the final entry revision")
    rendered = format_entries(combined)
    if rendered.count("Text.dbt") != 1 or rendered.count("Tooltips.dbt") != 1:
        raise AssertionError("history format repeated a file heading")
    if "Hello → 您好" not in rendered:
        raise AssertionError("history format did not render the final translation")


def main() -> int:
    root = project_root()
    assert_codec(root)
    assert_font_glyph_validation(root)
    assert_round_trip(root)
    assert_statuses(root)
    assert_loaded_order_matches_file_lines(root)
    assert_save_existing(root)
    assert_save_missing(root)
    assert_missing_insertions_follow_file_order(root)
    assert_unsaved_translation_status(root)
    assert_mod_label_match_inserts_source_formatted_row(root)
    assert_project_history_settings(root)
    assert_external_project_uses_tool_codec(root)
    assert_validation_blocks(root)
    assert_ignore_cache(root)
    assert_operation_history()
    assert_ai_token_protection()
    assert_linebreak_format_is_ignored()
    assert_guild2_format_grammar()
    assert_llm_suggestion_stream()
    assert_git_history(root)
    assert_git_pending_is_scoped_to_active_language(root)
    assert_git_recovers_stale_index_lock(root)
    assert_combined_git_history_format()
    print("translator_tool self-test ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
