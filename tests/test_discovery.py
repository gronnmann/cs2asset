import pytest
import vdf

from cs2asset.discovery import (
    check_write_access,
    discover_blender,
    discover_cs2,
    discover_projects,
    resolve_project,
    steam_libraries,
)
from cs2asset.errors import CS2AssetError


def make_install(root):
    (root / "game/csgo").mkdir(parents=True)
    (root / "content").mkdir()
    compiler = root / "game/bin/win64/resourcecompiler.exe"
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(b"test compiler")
    return root


def write_vdf(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(vdf.dumps(data), encoding="utf-8")


def test_multiple_steam_libraries_use_manifest_installdir(tmp_path, monkeypatch):
    monkeypatch.delenv("CS2ASSET_CS2_ROOT", raising=False)
    steam = tmp_path / "Steam"
    library = tmp_path / "Biblioteka gier łódź"
    library.mkdir()
    write_vdf(
        steam / "steamapps/libraryfolders.vdf",
        {"libraryfolders": {"0": {"path": str(steam)}, "1": {"path": str(library)}}},
    )
    root = make_install(library / "steamapps/common/Custom CS2 Folder")
    write_vdf(library / "steamapps/appmanifest_730.acf", {"AppState": {"installdir": root.name}})
    installation = discover_cs2(steam_roots=[steam])
    assert installation.root == root.resolve()
    assert installation.game_context == root / "game/csgo"
    assert len(steam_libraries([steam])) == 2


def test_explicit_root_wins_over_environment(tmp_path, monkeypatch):
    root = make_install(tmp_path / "CS2")
    monkeypatch.setenv("CS2ASSET_CS2_ROOT", str(tmp_path / "missing"))
    assert discover_cs2(root).root == root


def test_legacy_library_format(tmp_path):
    steam, other = tmp_path / "Steam", tmp_path / "Library"
    other.mkdir()
    write_vdf(steam / "steamapps/libraryfolders.vdf", {"LibraryFolders": {"1": str(other)}})
    assert steam_libraries([steam]) == [steam, other]


def test_missing_tools_are_actionable(tmp_path):
    root = tmp_path / "CS2"
    (root / "game/csgo").mkdir(parents=True)
    with pytest.raises(CS2AssetError, match="Properties > DLC"):
        discover_cs2(root)


def test_discover_project_pairs_and_templates(tmp_path):
    installation = discover_cs2(make_install(tmp_path / "CS2"))
    for name in ["de_mine", "addon_template", "workshop_items", "cs2asset_stage_test"]:
        (installation.content_dir / "csgo_addons" / name).mkdir(parents=True)
    (installation.game_dir / "csgo_addons/de_other").mkdir(parents=True)
    projects = discover_projects(installation)
    assert [p.name for p in projects] == ["addon_template", "de_mine", "de_other"]
    assert projects[0].is_template
    mine = resolve_project(installation, "de_mine")
    assert resolve_project(installation, mine.content_dir) == mine
    assert check_write_access(mine) == {"content": True, "game": True}
    assert mine.game_dir.is_dir()
    with pytest.raises(CS2AssetError, match="immediate addon"):
        resolve_project(installation, tmp_path)
    with pytest.raises(CS2AssetError):
        resolve_project(installation, "../de_mine")


def test_blender_override_and_missing(tmp_path):
    blender = tmp_path / "Blender 5.1/blender.exe"
    blender.parent.mkdir()
    blender.write_bytes(b"blender")
    assert discover_blender(blender) == blender
    with pytest.raises(CS2AssetError, match="does not exist"):
        discover_blender(tmp_path / "missing.exe")


def test_malformed_library_file_is_ignored(tmp_path):
    path = tmp_path / "steamapps/libraryfolders.vdf"
    path.parent.mkdir()
    path.write_text('"libraryfolders" { "0" {')
    assert steam_libraries([tmp_path]) == [tmp_path]


def test_manifest_traversal_cannot_redirect_discovery(tmp_path, monkeypatch):
    monkeypatch.delenv("CS2ASSET_CS2_ROOT", raising=False)
    root = make_install(tmp_path / "outside")
    steam = tmp_path / "Steam"
    write_vdf(steam / "steamapps/appmanifest_730.acf", {"AppState": {"installdir": str(root)}})
    with pytest.raises(CS2AssetError, match="not found"):
        discover_cs2(steam_roots=[steam])
