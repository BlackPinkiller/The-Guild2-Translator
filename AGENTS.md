# Repository Guidelines

## Project Purpose

This repository contains The Guild 2 Translator, a Windows desktop editor for The Guild 2 4.6+ localization projects. The application edits `.dbt` files and `Guides/*.txt`, provides format validation and game-style previews, and keeps language-specific history in Git.

## Runtime and Tooling

- Target platform: Windows.
- Target Python: Python 3.12.
- UI framework: PySide6 6.8+ and below 7.
- Development entry point: `py -3.12 -m translator_tool.app`.
- Desktop launcher: `run_translator_tool.bat`.
- Focused syntax check: `py -3.12 -m py_compile translator_tool\<module>.py`.
- Full regression suite: `py -3.12 -m translator_tool.self_test`.
- When `.build-venv` already exists, its Python may be used for reproducible local checks. Do not recreate or delete it unless the task is explicitly about packaging.
- Run Qt UI tests headlessly with `QT_QPA_PLATFORM=offscreen` when no visible window is required.

## Architecture

- `translator_tool/app.py`: PySide6 widgets, models, delegates, window coordination, filtering, editor state, and application startup. Keep business and file-format logic out of this module when it has a natural home elsewhere.
- `translator_tool/project.py`: project loading, translation-unit state, save semantics, insertion ordering, and atomic writes.
- `translator_tool/format_io.py`: byte-preserving DBT/plain-text parsing and serialization, encoding, newline, and file-profile handling.
- `translator_tool/validation.py`: format dialects, token comparison, repair suggestions, and validation issues.
- `translator_tool/preview.py`, `preview_placeholders.py`, `preview_profiles.py`: visual-only preview compilation, placeholder resolution, font/UI atlas access, and window rendering.
- `translator_tool/code_index.py`, `code_window_context.py`, `code_open.py`: script reference indexing and preview context extraction.
- `translator_tool/source_sync.py`: import/update of source projects while preserving language translations and marking affected entries for review.
- `translator_tool/git_history.py`, `history.py`: language-scoped persisted Git history and in-session undoable operations.
- `translator_tool/settings.py`: per-user settings under Local AppData. API keys are protected with Windows DPAPI.
- `translator_tool/cache.py`: project-local workflow metadata such as ignored, review, need-work, and confirmed unit IDs.
- `translator_tool/ai.py`: translation providers, LLM prompts, streaming, and token protection.
- `translator_tool/i18n.py`: all user-facing English and Simplified Chinese strings.
- `translator_tool/self_test.py`: integration-style regression suite. Add a focused assertion here for behavior changes when practical.
- `encoder/`: canonical Guild 2 codec implementation and codec data. Translator code must delegate codec behavior here rather than reimplement mappings.
- `assets/`: application and game-theme assets.
- `sources/<project>/languages/`: editable localization project data. Treat this as user data, not a disposable test fixture.
- `build/`, `.build-venv/`, `__pycache__/`: generated or local runtime artifacts.

## Data and Safety Boundaries

- Preserve raw file bytes, detected encoding, newline style, column layout, row order, and final-newline behavior unless the requested feature explicitly changes one of them.
- Never overwrite, normalize, sync, export, repack, or bulk-edit files under `sources/` merely to validate a code change.
- Do not run source-sync or save workflows against a real project during routine verification. Use the temporary fixtures created by `translator_tool.self_test` or a new isolated temporary fixture.
- Empty translation text means removing the target translation row so source fallback remains intact. Target-only extra DBT rows are removed on save according to existing project semantics.
- Preserve manual workflow metadata in `translator_tool_cache.json`; ignored/review/need-work/confirmed state is user progress.
- Settings and credentials are user-local state. Tests that touch settings must isolate and restore environment/path state.
- Preview rendering is visual-only. Raw placeholders and stored translation text must remain unchanged while preview substitutions are displayed.
- Chinese codec handling is optional and gated by `enable_chinese_codec` plus `language_uses_codec`. When disabled, dependent codec glyph validation must also remain disabled.

## Change Standards

- Diagnose the concrete cause before editing. Prefer the smallest direct fix and avoid speculative compatibility layers.
- Preserve unrelated working-tree changes. Never reset, replace, or format files outside the requested scope.
- Keep UI text in `i18n.py`; do not add user-visible literals to widget logic unless the text is file content or protocol data.
- Keep fixed-choice behavior behind explicit controls rather than free-text configuration.
- Preserve source order in translation tables. Do not enable proxy sorting or add full-project scans to hot paint, filter, typing, or hover paths without measurement.
- Use indexes or caches for repeated lookups, and invalidate them at the same state-change boundary as the underlying data.
- Keep caches bounded. Cached preview content must include every input that changes the rendered result.
- Use `Project`, `format_io`, and the canonical encoder APIs instead of parallel parsing, saving, encoding, or validation implementations.
- Packaging changes must be applied consistently to both Lite build scripts unless the difference is specifically onedir versus onefile behavior.

## Verification Standard

- Always run `git diff --check` after code or documentation edits.
- Run `py_compile` for every changed Python module.
- Run the smallest focused regression assertion that exercises the changed behavior.
- Run `py -3.12 -m translator_tool.self_test` for changes to project loading/saving, parsing, validation, preview semantics, settings, history, source sync, or shared UI state.
- For visible UI/theme changes, also launch or render the relevant UI when possible; passing headless tests alone is not visual verification.
- For packaging changes, distinguish script/static verification from a completed clean build. Do not claim the package works unless the build command finishes and the produced artifact launches.
- Report unrelated failures separately with the exact failing assertion or command.

## Packaging

- `build_translator_lite.bat` builds an onedir release and distributable ZIP.
- `build_translator_lite_onefile.bat` builds a single executable and ZIP.
- Both packages must include `encoder/guild2_codec.py`, `encoder/data`, `assets/app-icon.ico`, and `assets/game_theme`.
- Build scripts delete and recreate local build environments and output directories. Do not run them while another task depends on those artifacts.

