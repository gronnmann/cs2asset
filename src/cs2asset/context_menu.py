"""Per-user classic Explorer verbs, without changing file associations."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .errors import CS2AssetError
from .sources.local import MODEL_EXTENSIONS

EXTENSIONS = tuple(sorted(MODEL_EXTENSIONS | {".hdr", ".exr", ".zip"}))
VERB = "cs2asset.import"
LABEL = "Import into CS2 Hammer"


def registry():
    if sys.platform != "win32":
        raise CS2AssetError("Explorer context-menu integration is only supported on Windows.")
    import winreg

    return winreg


def key_path(extension):
    return rf"Software\Classes\SystemFileAssociations\{extension}\shell\{VERB}"


def add(uv: Path | None = None):
    reg = registry()
    executable = uv or shutil.which("uv")
    if not executable or not Path(executable).is_file():
        raise CS2AssetError("Cannot find uv.exe. Install uv or pass --uv PATH_TO_UV_EXE.")
    executable = Path(executable).resolve()
    command = subprocess.list2cmdline(
        [str(executable), "tool", "run", "--from", "cs2asset", "cs2asset",
         "context-menu", "import-file"]
    ) + ' "%1"'
    for extension in EXTENSIONS:
        with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, key_path(extension)) as key:
            reg.SetValueEx(key, "", 0, reg.REG_SZ, LABEL)
            reg.SetValueEx(key, "MultiSelectModel", 0, reg.REG_SZ, "Single")
        with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, key_path(extension) + r"\command") as key:
            reg.SetValueEx(key, "", 0, reg.REG_SZ, command)
    return status()


def remove():
    reg = registry()
    for extension in EXTENSIONS:
        for path in (key_path(extension) + r"\command", key_path(extension)):
            try:
                reg.DeleteKey(reg.HKEY_CURRENT_USER, path)
            except FileNotFoundError:
                pass
    return status()


def status():
    reg = registry()
    entries = []
    for extension in EXTENSIONS:
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, key_path(extension) + r"\command") as key:
                command = reg.QueryValueEx(key, "")[0]
        except FileNotFoundError:
            command = None
        entries.append({"extension": extension, "registered": command is not None,
                        "command": command})
    return {"label": LABEL, "scope": "current-user", "entries": entries}
