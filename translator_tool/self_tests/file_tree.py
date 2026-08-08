from __future__ import annotations

from ..file_tree import build_file_tree


def assert_file_tree_groups_nested_paths() -> None:
    tree = build_file_tree(
        (
            "Text.dbt",
            "Kontor.dbt",
            "Guides/Intro.txt",
            "Guides/Economy/Production.txt",
            "Guides/Economy/Trade.txt",
        )
    )
    if [node.name for node in tree] != ["Guides", "Kontor.dbt", "Text.dbt"]:
        raise AssertionError("file menu root was not grouped with folders first")
    guides = tree[0]
    if [node.name for node in guides.children] != ["Economy", "Intro.txt"]:
        raise AssertionError("first file-menu folder level was not grouped")
    economy = guides.children[0]
    if [node.file_rel for node in economy.children] != [
        "Guides/Economy/Production.txt",
        "Guides/Economy/Trade.txt",
    ]:
        raise AssertionError("nested file-menu paths did not retain their full filter values")

    windows_tree = build_file_tree((r"Guides\Controls.txt",))
    if windows_tree[0].children[0].file_rel != "Guides/Controls.txt":
        raise AssertionError("file-menu paths were not normalized to project separators")

    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from ..app import FILE_FILTER_ALL, HierarchicalFileComboBox

    app = QApplication.instance() or QApplication([])
    combo = HierarchicalFileComboBox()
    combo.addItem("All files", FILE_FILTER_ALL)
    for file_rel in (
        "Text.dbt",
        "Kontor.dbt",
        "WorkOfArt.dbt",
        "Guides/Intro.txt",
        "Guides/Economy/Trade.txt",
    ):
        combo.addItem(file_rel, file_rel)
    combo.set_file_paths(
        (
            "Text.dbt",
            "Kontor.dbt",
            "WorkOfArt.dbt",
            "Guides/Intro.txt",
            "Guides/Economy/Trade.txt",
        )
    )
    combo.setCurrentIndex(combo.findData("Guides/Economy/Trade.txt"))
    combo.resize(320, 32)
    combo.show()
    combo.showPopup()
    popup = combo._file_popup
    try:
        fixed_popup_height = popup.height() if popup is not None else -1
        if popup is None or popup.folder_parts != ("Guides", "Economy"):
            raise AssertionError("reopening the file list did not restore the selected file's folder")
        active = popup.viewport.active
        if not popup.back_list.isVisible() or popup.back_item.data(popup.ITEM_KIND_ROLE) != "back":
            raise AssertionError("nested file list did not expose a persistent back list item")
        if popup.back_item.sizeHint().height() != active.item(0).sizeHint().height():
            raise AssertionError("file-list back item was shorter than ordinary file rows")
        current = active.currentItem()
        if current is None or current.data(popup.ITEM_VALUE_ROLE) != "Guides/Economy/Trade.txt":
            raise AssertionError("nested file list did not focus the currently selected file")

        popup._go_back()
        QTest.qWait(50)
        app.processEvents()
        if (
            popup.viewport.active.height() != popup.viewport.height()
            or popup.viewport.incoming.height() != popup.viewport.height()
        ):
            raise AssertionError("file-list return animation started with a stale one-row-short page")
        QTest.qWait(130)
        app.processEvents()
        if popup.folder_parts != ("Guides",) or not popup.back_list.isVisible():
            raise AssertionError("file-list back navigation did not move up exactly one level")
        if popup.height() != fixed_popup_height:
            raise AssertionError("navigating up changed the fixed file-popup height")

        popup._go_back()
        QTest.qWait(180)
        app.processEvents()
        if popup.folder_parts or popup.height() != fixed_popup_height:
            raise AssertionError(
                f"returning to the root changed the fixed popup height: {popup.height()} != {fixed_popup_height}"
            )
        if popup.viewport.active.geometry() != popup.viewport.rect():
            raise AssertionError(
                "returning to the root left the active file list at the smaller folder-page height"
            )

        popup.open_for(FILE_FILTER_ALL)
        root = popup.viewport.active
        if root.item(0).data(popup.ITEM_VALUE_ROLE) != FILE_FILTER_ALL:
            raise AssertionError("root file list did not keep All Files at the top")
        if popup.back_list.isVisible():
            raise AssertionError("root file list kept an unnecessary back item")
        if root.item(0).sizeHint().height() != root.item(1).sizeHint().height():
            raise AssertionError("All Files item was shorter than ordinary folder/file rows")

        popup.open_for("Guides/Intro.txt")
        current = popup.viewport.active.currentItem()
        if current is None or current.data(popup.ITEM_VALUE_ROLE) != "Guides/Intro.txt":
            raise AssertionError("reopened folder did not restore focus to its selected file")
        popup._activate_item(current)
        app.processEvents()
        if combo.currentData() != "Guides/Intro.txt":
            raise AssertionError("selecting a nested file did not update the flat filter value")
        if combo.findData("Text.dbt") < 0:
            raise AssertionError("hierarchical popup changed the combo's flat lookup API")
    finally:
        if popup is not None:
            popup.hide()
        combo.deleteLater()
