from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable


@dataclass(frozen=True)
class FileTreeNode:
    name: str
    file_rel: str = ""
    children: tuple["FileTreeNode", ...] = ()

    @property
    def is_folder(self) -> bool:
        return bool(self.children)


def build_file_tree(file_paths: Iterable[str]) -> tuple[FileTreeNode, ...]:
    root: dict[str, object] = {}
    for raw_path in file_paths:
        file_rel = raw_path.replace("\\", "/").strip("/")
        if not file_rel:
            continue
        parts = PurePosixPath(file_rel).parts
        branch = root
        for folder in parts[:-1]:
            child = branch.setdefault(folder, {})
            if not isinstance(child, dict):
                break
            branch = child
        else:
            branch[parts[-1]] = file_rel

    def nodes(branch: dict[str, object]) -> tuple[FileTreeNode, ...]:
        result: list[FileTreeNode] = []
        for name, value in branch.items():
            if isinstance(value, dict):
                result.append(FileTreeNode(name=name, children=nodes(value)))
            else:
                result.append(FileTreeNode(name=name, file_rel=str(value)))
        return tuple(sorted(result, key=lambda node: (not node.is_folder, node.name.casefold())))

    return nodes(root)
