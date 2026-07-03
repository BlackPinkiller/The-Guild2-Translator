# The Guild 2 Translator

[中文说明](README_zh.md)

Desktop editor for translating The Guild 2 language files.

## Requirements

- Windows
- Python `3.12`
- A project root that contains `languages/` and `encoder/`

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

## Project Layout

Open a game or mod project root with this structure:

```text
<project root>/
|-- encoder/
`-- languages/
    |-- *.dbt
    |-- Guides/
    `-- #<language-code>/
```

Notes:

- Source `.dbt` files live directly under `languages/`
- Target `.dbt` files live under `languages/#<language-code>/`
- `Guides/*.txt` are handled as plain text files
- The target language folder can start empty

## Basic Workflow

1. Launch the app.
2. Open the project root.
3. Enter the target language code, such as `#cn` or `#de`.
4. Pick a file from the file list.
5. Edit translations in the lower editor.
6. Save to write only the current translation-side changes.

## Editing Notes

- `Ctrl+Z` and `Ctrl+Y` work inside the editor and for completed entry changes
- Right-click a translation entry to mark it for deletion; the source text is never modified
- `Guides/*.txt` use the document editor view instead of entry rows
- Missing target files are created only when you save translated content
- `Guides/*.txt` are saved with the source file's encoding and newline style

## History

The Update Log shows saved changes with inline diffs, so you can see exactly what changed inside each translation entry.
