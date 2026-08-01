from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from PySide6.QtWidgets import QApplication


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from translator_tool.preview import PreviewService  # noqa: E402


def _manifest_entries(manifest: dict[str, object]) -> tuple[tuple[str, str], ...]:
    entries: list[tuple[str, str]] = []
    assets = manifest.get("assets", ())
    if isinstance(assets, list):
        for entry in assets:
            if isinstance(entry, dict):
                entries.append((str(entry["source"]), str(entry["file"])))
    families = manifest.get("nine_slices", ())
    if isinstance(families, list):
        for family in families:
            if not isinstance(family, dict):
                continue
            prefix = str(family["source_prefix"])
            directory = str(family["directory"])
            pieces = family.get("pieces", range(9))
            entries.extend(
                (f"{prefix}{piece}", f"{directory}/{piece}.png")
                for piece in pieces
                if isinstance(piece, int) and 0 <= piece <= 8
            )
    return tuple(entries)


def extract_preview_assets(game_root: Path, output_root: Path) -> tuple[int, tuple[str, ...]]:
    manifest = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))
    service = PreviewService(
        game_root,
        ui_assets_dir=str(game_root / "Textures" / "Hud"),
    )
    written = 0
    missing: list[str] = []
    for source, relative in _manifest_entries(manifest):
        image = service.ui_image(source)
        if image is None or image.isNull():
            missing.append(source)
            continue
        destination = output_root.joinpath(*relative.replace("\\", "/").split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not image.save(str(destination), "PNG"):
            missing.append(source)
            continue
        written += 1
    return written, tuple(missing)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract the minimal Guild 2 UI texture set used by previews."
    )
    parser.add_argument("game_root", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT / "assets" / "preview_ui",
    )
    args = parser.parse_args()
    QApplication.instance() or QApplication([])
    written, missing = extract_preview_assets(args.game_root.resolve(), args.output.resolve())
    print(f"wrote {written} preview assets")
    if missing:
        print("missing:")
        for source in missing:
            print(f"  {source}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
