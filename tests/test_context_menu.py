from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from cs2asset import context_menu
from cs2asset.cli import app, context_menu_import_file
from cs2asset.errors import CS2AssetError


class Registry:
    HKEY_CURRENT_USER = "user"
    REG_SZ = 1

    def __init__(self):
        self.keys = {}

    def CreateKeyEx(self, root, path):
        assert root == self.HKEY_CURRENT_USER
        self.keys.setdefault(path, {})
        return nullcontext(path)

    def OpenKey(self, root, path):
        if path not in self.keys:
            raise FileNotFoundError(path)
        return nullcontext(path)

    def SetValueEx(self, key, name, reserved, kind, value):
        self.keys[key][name] = value

    def QueryValueEx(self, key, name):
        return self.keys[key][name], self.REG_SZ

    def DeleteKey(self, root, path):
        if path not in self.keys:
            raise FileNotFoundError(path)
        del self.keys[path]


def test_registration_and_removal_are_scoped_and_repeatable(tmp_path, monkeypatch):
    reg = Registry()
    unrelated = r"Software\Classes\SystemFileAssociations\.glb\shell\other"
    reg.keys[unrelated] = {"": "Other program"}
    monkeypatch.setattr(context_menu, "registry", lambda: reg)
    uv = tmp_path / "tools with spaces łódź" / "uv.exe"
    uv.parent.mkdir()
    uv.touch()
    for _ in range(2):
        report = context_menu.add(uv)
        assert all(entry["registered"] for entry in report["entries"])
        for entry in report["entries"]:
            assert entry["command"].startswith(f'"{uv.resolve()}"')
            assert entry["command"].endswith('context-menu import-file "%1"')
            assert reg.keys[context_menu.key_path(entry["extension"])]["MultiSelectModel"] == "Single"
    for _ in range(2):
        assert not any(entry["registered"] for entry in context_menu.remove()["entries"])
    assert reg.keys == {unrelated: {"": "Other program"}}


def test_missing_launcher_does_not_register(tmp_path, monkeypatch):
    reg = Registry()
    monkeypatch.setattr(context_menu, "registry", lambda: reg)
    with pytest.raises(CS2AssetError, match="Cannot find uv"):
        context_menu.add(tmp_path / "missing.exe")
    assert not reg.keys


def test_non_windows_is_a_cli_error(monkeypatch):
    monkeypatch.setattr(context_menu.sys, "platform", "linux")
    result = CliRunner().invoke(app, ["context-menu", "status", "--json"])
    assert result.exit_code == 1
    assert "only supported on Windows" in result.stdout


def test_explorer_import_waits_even_after_failure(monkeypatch):
    calls = []

    def failed_import(*args, **kwargs):
        calls.append(kwargs["asset"])
        raise CS2AssetError("import failed")

    monkeypatch.setattr("cs2asset.cli.import_command", failed_import)
    monkeypatch.setattr("cs2asset.cli.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: calls.append(prompt))
    ctx = SimpleNamespace(invoke=lambda fn, **kwargs: fn(**kwargs))
    with pytest.raises(CS2AssetError, match="import failed"):
        context_menu_import_file(ctx, "C:/rock.glb")
    assert calls == ["C:/rock.glb", "Press Enter to close..."]
