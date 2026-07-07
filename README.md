# The Guild 2 Translator

[简体中文](README_zh.md)

Desktop localization editor for The Guild 2 version 4.6+.

## Start

Double-click:

```text
run_translator_tool.bat
```

or run:

```powershell
py -3.12 -m translator_tool.app
```

## Basic Use

1. Use the project button in the top-left corner to choose a game folder or a local project under `sources/`.
2. Choose a language; create a new one from the language dropdown when needed.
3. Select a `.dbt` file or a `Guides/*.txt` file from the file list.
4. Select an entry, then edit its translation in the editor on the right.
5. Use search and status filters to find untranslated, changed, review, or ignored entries.
6. Save to write the current project and language files.

## Preview

<p align="center">
  <img src="docs/images/preview.gif" alt="Preview" width="400">
</p>

- Choose the entry-list preview range in Settings: Off, Source, Translation, or All.
- The source and translation editors each have their own preview toggle.
- Hover the preview button to view the text in a game-style window.
- Placeholders, colors, line breaks, icons, and selected game fonts are rendered from the current resource settings.

The in-game preview makes source and translation formatting problems easier to spot.

<p align="center">
  <img src="docs/images/preview-in-game.jpg" alt="Preview before" width="350">
  <img src="docs/images/preview-in-game-after.jpg" alt="Preview after" width="350">
</p>

## Code References

<p align="center">
  <img src="docs/images/code-references.gif" alt="Code references" width="350">
</p>

- The `Code` button beside the source title shows how many files reference the current label.
- Click it to open a reference; hold it when there are multiple references to choose a file.
- Code reference analysis can be disabled in Settings.

## Project Management

<p align="center">
  <img src="docs/images/project-management.gif" alt="Project management" width="500">
</p>

- Scan a game folder for vanilla and mod localization files.
- Add vanilla or mod files as local projects.
- Update projects while keeping translation progress in the current language.

## Common Actions

- Right-click entries to copy translations, restore loaded text, restore source text, clear translations, mark deletes, or mark entries as ignored.
- Right-click entries to use AI translation or LLM suggestions; configure the service in Settings.
- The update log shows translation changes for the current project and language.

## Update Log

<p align="center">
  <img src="docs/images/history.jpg" alt="Update log" width="300">
</p>

- View translation changes for the current project and language.
- Select one or more commits to inspect changed entries.
- Changes are grouped by file and entry.

## Project Layout

```text
sources/
|-- Vanilla/
|   `-- languages/
|       |-- *.dbt
|       |-- Guides/
|       `-- #<language-code>/
`-- <project name>/
    `-- languages/
```
