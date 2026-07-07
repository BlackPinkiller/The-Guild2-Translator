from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import winreg

from .code_index import CodeReference


def open_code_reference(reference: CodeReference) -> bool:
    path = reference.path
    line = max(1, reference.line)
    if not path.is_file():
        return False
    if _open_with_default_editor(path, line):
        return True
    if _run_editor(("code", "-g", f"{path}:{line}")):
        return True
    if _run_editor(("cursor", "-g", f"{path}:{line}")):
        return True
    if _run_editor(("notepad++", str(path), f"-n{line}")):
        return True
    try:
        os.startfile(path)  # type: ignore[attr-defined]
        return True
    except OSError:
        return False


def _open_with_default_editor(path: Path, line: int) -> bool:
    command = _default_open_command(path.suffix)
    if not command:
        return False
    lowered = command.casefold()
    if "code" in lowered or "cursor" in lowered:
        exe = _command_executable(command)
        return _run_editor((exe, "-g", f"{path}:{line}")) if exe else False
    if "notepad++" in lowered:
        exe = _command_executable(command)
        return _run_editor((exe, str(path), f"-n{line}")) if exe else False
    return False


def _default_open_command(suffix: str) -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, suffix) as key:
            prog_id = winreg.QueryValueEx(key, "")[0]
        if not isinstance(prog_id, str) or not prog_id:
            return ""
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command") as key:
            command = winreg.QueryValueEx(key, "")[0]
        return command if isinstance(command, str) else ""
    except OSError:
        return ""


def _command_executable(command: str) -> str:
    command = command.strip()
    if command.startswith('"'):
        end = command.find('"', 1)
        return command[1:end] if end > 1 else ""
    first = command.split(maxsplit=1)[0] if command else ""
    return shutil.which(first) or first


def _run_editor(args: tuple[str, ...]) -> bool:
    executable = args[0]
    if not executable:
        return False
    if not Path(executable).is_file() and shutil.which(executable) is None:
        return False
    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False
