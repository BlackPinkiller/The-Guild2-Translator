# The Guild 2 Translator

[简体中文](README_zh.md)

Desktop editor for The Guild 2 version 4.6+ translation projects.

## Requirements

- Windows
- Python `3.12`

## Install

```powershell
py -3.12 -m pip install -r requirements.txt
```

## Start

```powershell
run_translator_tool.bat
```

or

```powershell
py -3.12 -m translator_tool.app
```

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

## Use

1. Open a project from `sources/`.
2. If needed, choose `Create New Language...` from the language dropdown.
3. Enter the language folder name without `#`.
4. Choose a file.
5. Edit the translation.
6. Save.

## Notes

- Source files stay under `languages/`
- Translation files stay under `languages/#<language-code>/`
- Creating a new language only creates an empty folder
- `Guides/*.txt` are edited as plain text
- The update log is scoped to the current project and current language
