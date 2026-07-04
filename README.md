# The Guild 2 Translator

[简体中文说明](README_zh.md)

Desktop editor for translating The Guild 2 language files.

## Requirements

- Windows
- Python `3.12`

Install dependencies:

```powershell
py -3.12 -m pip install -r requirements.txt
```

## Start

Recommended:

```powershell
run_translator_tool.bat
```

Manual launch:

```powershell
py -3.12 -m translator_tool.app
```

## Project Model

The app treats each folder under `sources/` as a project.

```text
sources/
|-- Vanilla/
|   `-- languages/
|       |-- *.dbt
|       |-- Guides/
|       `-- #<language-code>/
`-- Reforged/
    `-- languages/
```

Notes:

- Source files live directly under `languages/`
- Translation files live under `languages/#<language-code>/`
- `Guides/*.txt` are handled as plain text files
- A translation folder can start empty

## Game Root

The game root is not a separate project entry.

- It is only a source location for vanilla files
- Choosing a game root feeds the managed `sources/Vanilla` project
- Future update/sync actions can reuse the stored game root without treating it as its own project

## Basic Workflow

1. Launch the app.
2. Open or switch to a project under `sources/`.
3. If needed, pick `Create New Language...` from the language dropdown and enter the folder name without the leading `#`.
4. Pick a file from the file dropdown.
5. Edit translations in the lower editor.
6. Save to write only translation-side changes.

## Editing Notes

- `Ctrl+Z` and `Ctrl+Y` work inside the editor and for completed entry changes
- Right-click a translation entry to mark it for deletion; the source text is never modified
- `Guides/*.txt` use the document editor view instead of entry rows
- Missing target files are created only when you save translated content
- Creating a new language only creates an empty `languages/#<language-code>/` folder
- `Guides/*.txt` are saved with the source file's encoding and newline style

## History

The Update Log shows saved changes with inline diffs.

- History is scoped to the current project
- The commit list is filtered to the currently selected language
- The detail view only shows changes from that language
