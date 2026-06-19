from __future__ import annotations

from pathlib import Path
import shutil
import uuid

from .ai import GoogleTranslateProvider, OpenAICompatibleProvider, TranslationProviderError
from .codec_adapter import Guild2Codec, default_codec_path
from .git_history import LanguageGit, format_entries
from .history import OperationHistory, TranslationOperation, UnitChange
from .format_io import load_dbt, load_plain_text
from .project import (
    MISSING_WORK_STATUSES,
    Project,
    STATUS_MISSING_ROW,
    SaveValidationError,
)
from .settings import AppSettings


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def assert_round_trip(root: Path) -> None:
    for path in sorted((root / "languages").rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".dbt", ".txt"}:
            continue
        doc = load_dbt(path) if path.suffix.lower() == ".dbt" else load_plain_text(path)
        rendered = doc.render_bytes()
        if rendered != path.read_bytes():
            raise AssertionError(f"round-trip changed bytes: {path}")


def assert_statuses(root: Path) -> None:
    project = Project.load(root, "#chinese")
    statuses = {unit.status for unit in project.units}
    expected = {"译文缺行", "译文为空", "未翻译(同原文)", "译文多余"}
    missing = expected - statuses
    if missing:
        raise AssertionError(f"expected statuses not found: {sorted(missing)}")
    if not any(unit.file_rel == "Text.dbt" and unit.status == STATUS_MISSING_ROW for unit in project.units):
        raise AssertionError("Text.dbt missing rows were not detected")
    if not any(unit.status in MISSING_WORK_STATUSES for unit in project.units):
        raise AssertionError("missing-work filter would be empty")
    if not any(unit.file_rel == "Guides/StartPage.txt" and unit.source_text for unit in project.units):
        raise AssertionError("Guides source files were not matched to translated Guides")


def copy_project_subset(src_root: Path, dst_root: Path) -> None:
    (dst_root / "encoder" / "data").mkdir(parents=True)
    shutil.copy2(src_root / "encoder" / "data" / "guild2_chinese_codec.json", dst_root / "encoder" / "data")
    (dst_root / "languages" / "#chinese").mkdir(parents=True)
    for name in ["Text.dbt", "Tooltips.dbt"]:
        shutil.copy2(src_root / "languages" / name, dst_root / "languages" / name)
        shutil.copy2(src_root / "languages" / "#chinese" / name, dst_root / "languages" / "#chinese" / name)


def make_temp_project(root: Path, prefix: str) -> Path:
    temp = root / f"_{prefix}{uuid.uuid4().hex[:8]}"
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
    original = (temp / "languages" / "#chinese" / "Text.dbt").read_bytes()
    unit.set_text(unit.current_text + "!")
    result = project.save([unit])
    if not result.changed_files:
        raise AssertionError("save_existing did not write a file")
    if (temp / "languages" / "#chinese" / "Text.dbt").read_bytes() == original:
        raise AssertionError("save_existing did not update the target file")
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
    codec = Guild2Codec.load(default_codec_path(root))
    text = "测试"
    if codec.decode(codec.encode(text)) != text:
        raise AssertionError("codec encode/decode did not round-trip")


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


def main() -> int:
    root = project_root()
    assert_codec(root)
    assert_round_trip(root)
    assert_statuses(root)
    assert_save_existing(root)
    assert_save_missing(root)
    assert_validation_blocks(root)
    assert_ignore_cache(root)
    assert_operation_history()
    assert_ai_token_protection()
    assert_llm_suggestion_stream()
    assert_git_history(root)
    print("translator_tool self-test ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
