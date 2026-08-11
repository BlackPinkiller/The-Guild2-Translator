from __future__ import annotations

import os
from types import SimpleNamespace

from ..code_window_context import surface_window_context
from ..guide_model import parse_guide_toc, render_guide_html
from ..preview import (
    GUIDE_BODY_TEXT,
    GUIDE_HEADER_TEXT,
    PREVIEW_MARK,
    GameGlyphAtlas,
    GlyphRecord,
    PreviewService,
)


def assert_guide_preview_uses_compact_scroll_page_semantics() -> None:
    from pathlib import Path

    from PySide6.QtGui import QColor, QImage

    class CountingTexture:
        def __init__(self) -> None:
            self.crop_count = 0

        def crop(self, _x: int, _y: int, _width: int, _height: int) -> QImage:
            self.crop_count += 1
            return QImage(8, 12, QImage.Format.Format_ARGB32_Premultiplied)

    fallback_key = ("fallback_font", ord("A"))
    texture_path = Path(__file__)
    texture = CountingTexture()
    atlas = object.__new__(GameGlyphAtlas)
    atlas.records = {fallback_key: GlyphRecord(texture_path, 0, 0, 8, 12)}
    atlas.keys_by_codepoint = {ord("A"): [fallback_key]}
    atlas.textures = {texture_path: texture}
    atlas.images = {}
    atlas.glyph(ord("A"))
    atlas.glyph(ord("A"))
    if texture.crop_count != 1:
        raise AssertionError("fallback game-font glyph lookup bypassed the shared atlas cache")

    sample = (
        "<header>Trade post</header>\n\n"
        "<text>Body one.</text>\n\n"
        "<text>Body two.</text>\n\n"
        "<separator></separator>\n\n"
        "<header>Interface</header>\n\n"
        '<list>\n  [type="bullet"]\n'
        "  <item><text>First item.</text></item>\n"
        "  <item><text>Second item.</text></item>\n"
        "</list>"
    )
    service = PreviewService(None, "#chinese")
    alpha_mask = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
    alpha_mask.fill(QColor(255, 255, 255, 128))
    tinted = service._scaled_game_glyph(alpha_mask, 1.0, QColor(120, 60, 30, 128))
    tinted_pixel = tinted.pixelColor(0, 0)
    if tinted_pixel.alpha() != 64 or any(
        abs(actual - expected) > 3
        for actual, expected in zip(
            (tinted_pixel.red(), tinted_pixel.green(), tinted_pixel.blue()),
            (120, 60, 30),
        )
    ):
        raise AssertionError(f"accelerated game-font tint changed glyph color/alpha: {tinted_pixel!r}")
    document = service.render(
        sample,
        unit_key="guide-preview",
        file_rel="Guides/CountingHouse.txt",
        kind="text",
        target=True,
    )
    visible = document.display_text.replace(PREVIEW_MARK, "")
    if "\n\n" in visible:
        raise AssertionError(f"Guide source indentation leaked into preview spacing: {visible!r}")
    if "• First item." not in visible or "• Second item." not in visible:
        raise AssertionError(f"Guide list items lost their compact bullet layout: {visible!r}")
    rules = [atom for atom in document.atoms if atom.layout == "guide_rule"]
    if len(rules) != 3:
        raise AssertionError(f"Guide headings and separator did not become page rules: {rules!r}")
    heading_text = "".join(
        atom.text
        for atom in document.atoms
        if atom.layout == "guide_header" and atom.color == GUIDE_HEADER_TEXT
    )
    if "Trade post" not in heading_text:
        raise AssertionError("Guide heading did not receive the in-game red heading treatment")
    body_text = "".join(
        atom.text
        for atom in document.atoms
        if not atom.layout and atom.color == GUIDE_BODY_TEXT
    )
    if "Body one." not in body_text:
        raise AssertionError("Guide body did not receive the parchment text color")
    literal_angles = service.render(
        "<text>Use >Building name< here.</text>",
        unit_key="guide-literal-angles",
        file_rel="Guides/CountingHouse.txt",
        kind="text",
        target=True,
    ).display_text.replace(PREVIEW_MARK, "")
    if ">Building name<" not in literal_angles or "『Building name』" in literal_angles:
        raise AssertionError(f"Guide literal angle brackets were rewritten: {literal_angles!r}")
    if document.line_height_percent != 112:
        raise AssertionError(
            f"Guide editor line height did not use the compact readable profile: {document.line_height_percent}"
        )

    context = surface_window_context("guide", body_label="CountingHouse")
    if (
        context.kind != "guide"
        or context.layout != "guide"
        or context.background_asset.casefold() != "hud/background_scroll.tga"
    ):
        raise AssertionError(f"Guide preview did not select its parchment surface: {context!r}")
    long_document = service.render(
        sample * 18,
        unit_key="long-guide-preview",
        file_rel="Guides/CountingHouse.txt",
        kind="text",
        target=True,
    )
    layout = service._game_window_layout(
        context,
        None,
        long_document,
        (),
        target=True,
    )
    if (
        layout.preset_id != "guide_page"
        or layout.width > 580
        or layout.height > 460
        or layout.body_scale != 0.64
    ):
        raise AssertionError(f"long Guide preview escaped its fixed page viewport: {layout!r}")

    from ..app import TranslatorWindow

    unit = SimpleNamespace(
        file_rel="Guides/CountingHouse.txt",
        label="CountingHouse",
        ref=SimpleNamespace(kind="text"),
    )
    selected_context, header, body, buttons, references = TranslatorWindow._game_preview_parts(
        object(),
        unit,
    )
    if (
        selected_context is None
        or selected_context.surface != "guide"
        or header is not None
        or body is not unit
        or buttons
        or references
    ):
        raise AssertionError("Guide txt did not bypass unrelated code-window preview selection")


def assert_guide_editor_preview_keeps_a_scrollable_asset_free_surface() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from ..app import PreviewPlainTextEdit

    app = QApplication.instance() or QApplication([])
    raw = "\n\n".join(
        f"<header>Section {index}</header>\n<text>Scrollable body {index}.</text>"
        for index in range(24)
    )
    service = PreviewService(None, "#chinese")
    editor = PreviewPlainTextEdit()
    editor.resize(320, 150)
    editor.set_preview_builder(
        lambda text: service.render(
            text,
            unit_key="guide-editor",
            file_rel="Guides/CountingHouse.txt",
            kind="text",
            target=True,
        ),
        lambda _glyph_id: None,
    )
    editor.set_preview_surface("guide")
    editor.setPlainText(raw)
    observed_text_changes: list[str] = []
    editor.textChanged.connect(lambda: observed_text_changes.append(editor.toPlainText()))
    editor.set_preview_enabled(True)
    editor.show()
    app.processEvents()
    try:
        if editor.toPlainText() != raw:
            raise AssertionError("Guide editor preview changed the stored txt content")
        if editor.document().documentMargin() != 18.0:
            raise AssertionError("Guide editor did not apply the parchment content inset")
        if hasattr(editor, "_preview_background_provider"):
            raise AssertionError("Guide editor preview still owns a game-window asset provider")
        scroll = editor.verticalScrollBar()
        if scroll.maximum() <= 0:
            raise AssertionError("long Guide editor preview did not remain scrollable")
        scroll.setValue(scroll.maximum())
        if scroll.value() != scroll.maximum():
            raise AssertionError("Guide editor preview scrollbar could not reach the document end")
        editor.set_preview_enabled(False)
        if editor.toPlainText() != raw:
            raise AssertionError("leaving Guide preview did not restore the raw txt")
        if observed_text_changes:
            raise AssertionError(
                "Guide preview toggle leaked presentation-only textChanged events: "
                f"{observed_text_changes!r}"
            )
    finally:
        editor.hide()
        editor.deleteLater()


def assert_guide_browser_is_responsive_and_navigable() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication

    from ..guide_widget import GuidePreviewPane

    app = QApplication.instance() or QApplication([])
    toc = "\n".join(
        ["[Category]Getting Started", "  [Page]StartPage|Welcome"]
        + [f"  [Page]Page{index}|Page {index}" for index in range(24)]
    )
    entries = parse_guide_toc(toc)
    if [entry.page_id for entry in entries if entry.page_id][:3] != [
        "StartPage",
        "Page0",
        "Page1",
    ]:
        raise AssertionError(f"Guide table-of-contents order was not preserved: {entries!r}")
    fragment = render_guide_html(
        '<text>[link="Page1"]Open page</text>'
        '<text>Use >Building< and {tip:DYNASTY}dynasty{/tip}.</text>'
        '<list>[type="bullet"]<item><text>Compact item</text></item></list>'
        '<table><row><cell><text>Compact cell</text></cell></row></table>',
        target=False,
    )
    if (
        'href="guide:Page1"' not in fragment
        or "&gt;Building&lt;" not in fragment
        or 'href="tip:DYNASTY"' not in fragment
        or "<li><span>Compact item</span></li>" not in fragment
        or "<td><span>Compact cell</span></td>" not in fragment
    ):
        raise AssertionError(f"Guide navigation semantics were flattened: {fragment!r}")

    pane = GuidePreviewPane()
    pane.resize(560, 260)
    app_family = QApplication.font().family()
    glyph_requests: list[tuple[str, bool, tuple[int, int, int, int] | None]] = []

    def shared_preview_glyph(
        char: str,
        target: bool,
        color: tuple[int, int, int, int] | None,
    ) -> QImage:
        glyph_requests.append((char, target, color))
        image = QImage(8, 12, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(*(color or (55, 38, 24, 255))))
        return image

    pane.set_font_providers(
        text_glyph=shared_preview_glyph,
        text_family=lambda _target: "",
    )
    requested: list[str] = []
    pane.pageRequested.connect(requested.append)
    page_text = '<text>[link="Page1"]Open page</text>' + "\n".join(
        f"<header>Section {index}</header><text>Body {index}</text>" for index in range(40)
    )
    pane.set_guide(
        toc,
        "StartPage",
        page_text,
        target=False,
    )
    pane.show()
    app.processEvents()
    try:
        if pane._surface_texture.width() < 1000 or pane.body_frame._texture.width() < 1300:
            raise AssertionError("Guide browser did not load the bundled HiRes surfaces")
        if pane.body.parentWidget() is not pane.body_frame:
            raise AssertionError("Guide body is no longer layered over its resizable parchment frame")
        if "background-image" in pane.styleSheet().casefold():
            raise AssertionError("Guide browser regressed to tiling its parchment through QSS")
        if pane.header.sizeHint().height() > 30 or pane.home_button.height() > 30:
            raise AssertionError("Guide title/toolbar regressed to the oversized preview chrome")
        if pane.close_button.width() > 20 or pane.close_button.height() > 20:
            raise AssertionError("Guide close button regressed to an oversized title-bar control")
        body_scroll = pane.body.verticalScrollBar()
        if body_scroll.width() != 17 or getattr(body_scroll, "_thumb").size() != QSize(17, 55):
            raise AssertionError("Guide browser did not retain the native scrollbar thumb dimensions")
        if "border-image" in pane.styleSheet().casefold():
            raise AssertionError("Guide scrollbar thumb regressed to a stretchable stylesheet image")
        if body_scroll.maximum() <= 0:
            raise AssertionError("resized Guide body did not retain its own scrollbar")
        body_scroll.setValue(body_scroll.minimum())
        first_slider = body_scroll.slider_rect()
        body_scroll.setValue(body_scroll.maximum())
        last_slider = body_scroll.slider_rect()
        if (
            first_slider.size() != QSize(17, 55)
            or last_slider.size() != QSize(17, 55)
            or last_slider.top() <= first_slider.top()
        ):
            raise AssertionError("Guide scrollbar thumb was stretched instead of moving at fixed size")
        if pane.toc_tree.verticalScrollBar().maximum() <= 0:
            raise AssertionError("resized Guide menu did not retain its own scrollbar")
        if not pane.preview_font_enabled or not pane.font_button.isChecked() or not glyph_requests:
            raise AssertionError("Guide preview did not default to the font configured in Settings")
        image_anchor_hrefs: set[str] = set()
        block = pane.body.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment_item = iterator.fragment()
                if fragment_item.isValid():
                    char_format = fragment_item.charFormat()
                    if char_format.isImageFormat() and char_format.isAnchor():
                        image_anchor_hrefs.add(char_format.anchorHref())
                iterator += 1
            block = block.next()
        if "guide:Page1" not in image_anchor_hrefs:
            raise AssertionError("configured preview-font glyphs discarded Guide link navigation")
        pane.font_button.click()
        app.processEvents()
        if pane.preview_font_enabled or pane.font_button.isChecked():
            raise AssertionError("Guide font toggle did not switch to the App font")
        if pane.body.document().defaultFont().family() != app_family:
            raise AssertionError("Guide App-font mode did not inherit QApplication.font()")
        if "Open page" not in pane.body.toPlainText() or "\ufffc" in pane.body.toPlainText():
            raise AssertionError("Guide App-font mode did not restore native document text")
        pane.font_button.click()
        app.processEvents()
        if not pane.preview_font_enabled or not pane.font_button.isChecked():
            raise AssertionError("Guide font toggle did not return to the Settings font")

        configured_family = "Guide Settings Font"
        pane.set_font_providers(
            text_glyph=shared_preview_glyph,
            text_family=lambda _target: configured_family,
        )
        app.processEvents()
        if pane.body.document().defaultFont().family() != configured_family:
            raise AssertionError("Guide Settings-font mode did not use the configured TTF family")
        if "Open page" not in pane.body.toPlainText() or "\ufffc" in pane.body.toPlainText():
            raise AssertionError("configured TTF font was unnecessarily rasterized into Guide glyph images")
        pane.font_button.click()
        app.processEvents()
        if pane.body.document().defaultFont().family() != app_family:
            raise AssertionError("Guide font toggle did not leave the configured TTF for the App font")
        page_item = next(
            item
            for item in pane._tree_items
            if str(item.data(0, pane.PAGE_ROLE) or "") == "Page1"
        )
        pane._toc_item_clicked(page_item, 0)
        if requested != ["Page1"]:
            raise AssertionError(f"Guide menu click did not request its page: {requested!r}")
        pane.resize(980, 560)
        app.processEvents()
        if pane.content_splitter.widget(1).width() <= pane.content_splitter.widget(0).width():
            raise AssertionError("Guide content did not fluidly receive the enlarged window width")
    finally:
        pane.hide()
        pane.deleteLater()


def assert_preview_edits_stay_atomic_and_restore_cursor_anchors() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextCursor
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from ..app import PreviewPlainTextEdit, TranslatorWindow

    class ObservedEditor(PreviewPlainTextEdit):
        def __init__(self) -> None:
            super().__init__()
            self.presentation_updates: list[tuple[str, bool]] = []

        def _set_unformatted_plain_text(self, text: str) -> None:
            self.presentation_updates.append((text, self.updatesEnabled()))
            super()._set_unformatted_plain_text(text)

    app = QApplication.instance() or QApplication([])
    raw = "<header>Title</header><text>1234</text>"
    service = PreviewService(None, "#chinese")
    editor = ObservedEditor()
    editor.set_preview_builder(
        lambda text: service.render(
            text,
            unit_key="atomic-guide-editor",
            file_rel="Guides/Test.txt",
            kind="text",
            target=True,
        ),
        lambda _glyph_id: None,
    )
    editor.setPlainText(raw)
    editor.set_preview_enabled(True)
    editor.presentation_updates.clear()
    cursor_raw = raw.index("1234") + 4
    editor.set_raw_cursor_position(cursor_raw)
    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    QTest.keyClick(editor, Qt.Key.Key_Backspace)
    app.processEvents()
    if editor.toPlainText() != raw.replace("1234", "12"):
        raise AssertionError("atomic preview deletion changed the wrong raw range")
    if not editor.presentation_updates or any(enabled for _text, enabled in editor.presentation_updates):
        raise AssertionError(
            f"preview presentation became paintable before formatting completed: {editor.presentation_updates!r}"
        )
    if any(text == editor.toPlainText() for text, _enabled in editor.presentation_updates):
        raise AssertionError("preview editing exposed an intermediate raw document")
    restored = TranslatorWindow._map_cursor_between_texts("12", "1234", 2)
    if restored != 4:
        raise AssertionError(f"undo cursor did not follow restored suffix length: {restored}")
    editor.setPlainText("1234")
    editor.set_raw_cursor_position(restored)
    if editor.raw_cursor_position() != 4:
        raise AssertionError("restored preview cursor was not mapped back to the raw end")
    editor.deleteLater()
