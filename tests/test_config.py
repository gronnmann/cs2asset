import json

import pytest

from cs2asset.config import (
    cache_dir,
    load_config,
    save_project_config,
    save_user_config,
    user_config_path,
)
from cs2asset.errors import CS2AssetError


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CS2ASSET_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CS2ASSET_CACHE_DIR", str(tmp_path / "cache"))


def test_precedence_and_unset_overrides(tmp_path):
    save_user_config({"resolution": "1k", "project": "de_example", "surface": "default"})
    project = tmp_path / "project"
    save_project_config(project, {"resolution": "2k", "surface": "concrete"})
    assert load_config(project, {"resolution": "4k", "surface": None}) == {
        "resolution": "4k",
        "project": "de_example",
        "surface": "concrete",
    }
    assert load_config()["resolution"] == "1k"


def test_config_paths_are_overridable(tmp_path):
    assert user_config_path() == tmp_path / "config/config.json"
    assert cache_dir() == tmp_path / "cache"


def test_saves_merge_and_preserve_unicode():
    save_user_config({"blender": "C:/Ścieżka/blender.exe"})
    save_user_config({"project": "de_test"})
    assert load_config()["blender"] == "C:/Ścieżka/blender.exe"
    assert json.loads(user_config_path().read_text(encoding="utf-8"))["project"] == "de_test"
    assert not list(user_config_path().parent.glob("*.tmp"))


def test_project_machine_paths_rejected(tmp_path):
    with pytest.raises(CS2AssetError, match="Machine-specific"):
        save_project_config(tmp_path, {"cs2_root": "C:/game"})


def test_corrupt_config_is_not_silently_overwritten():
    user_config_path().parent.mkdir(parents=True)
    user_config_path().write_text("{broken")
    with pytest.raises(CS2AssetError, match="Cannot read"):
        save_user_config({"project": "anything"})
    assert user_config_path().read_text() == "{broken"
