import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from cs2asset import __version__
from cs2asset.cache import MetadataCache
from cs2asset.cli import app
from cs2asset.config import cache_dir, save_project_config, save_user_config, user_config_path

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures" / "polyhaven"


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CS2ASSET_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CS2ASSET_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("CS2ASSET_CS2_ROOT", raising=False)
    monkeypatch.delenv("CS2ASSET_BLENDER", raising=False)

    def unexpected_network(*args, **kwargs):
        pytest.fail("CLI unit tests must not use the network")

    monkeypatch.setattr(httpx.Client, "send", unexpected_network)


@pytest.fixture
def install(tmp_path, monkeypatch):
    root = tmp_path / "CS2 Workshop tools łódź"
    (root / "game/csgo").mkdir(parents=True)
    compiler = root / "game/bin/win64/resourcecompiler.exe"
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(b"fake compiler; dry-run must never execute this")
    for name in ("de_first", "de_second", "addon_template"):
        (root / "content/csgo_addons" / name).mkdir(parents=True)
        (root / "game/csgo_addons" / name).mkdir(parents=True)
    monkeypatch.setenv("CS2ASSET_CS2_ROOT", str(root))
    return root


@pytest.fixture
def metadata():
    cache = MetadataCache(cache_dir() / "metadata")
    catalog = {}
    for asset in ("dirty_football", "concrete_floor_01", "sunset_jhbcentral"):
        for endpoint in ("info", "files"):
            data = json.loads((FIXTURES / f"{asset}_{endpoint}.json").read_text(encoding="utf-8"))
            cache.put(f"/{endpoint}/{asset}", data)
            if endpoint == "info":
                catalog[asset] = data
    cache.put("/assets", catalog)
    return cache


def invoke_json(arguments, *, exit_code=0):
    result = runner.invoke(app, arguments)
    assert result.exit_code == exit_code, result.output or repr(result.exception)
    return json.loads(result.stdout), result


def test_version_without_command():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == __version__


@pytest.mark.parametrize("arguments", [["--help"], ["import", "--help"], ["projects", "--help"]])
def test_help(arguments):
    result = runner.invoke(app, arguments)
    assert result.exit_code == 0
    assert "Usage" in result.stdout


def test_projects_listing_uses_fake_install(install):
    data, _ = invoke_json(["projects", "list", "--json"])
    assert [p["name"] for p in data] == ["addon_template", "de_first", "de_second"]
    assert data[0]["is_template"]
    assert all(str(install) in p["content_dir"] for p in data)


def test_project_selection_is_persisted(install):
    data, _ = invoke_json(["projects", "use", "de_first", "--json"])
    assert data["name"] == "de_first"
    saved = json.loads(user_config_path().read_text(encoding="utf-8"))
    assert saved["project"] == "de_first"
    assert saved["cs2_root"] == str(install)


@pytest.mark.parametrize("json_args", [["--json", "list"], ["list", "--json"]])
def test_noninteractive_missing_project_is_json_error(install, json_args):
    data, result = invoke_json(json_args, exit_code=1)
    assert "Select a project" in data["error"]
    assert "Project number" not in result.output
    assert not user_config_path().exists()


@pytest.mark.parametrize("json_args", [["--json", "info"], ["info"]])
def test_info_json_offline(metadata, json_args):
    arguments = [*json_args, "polyhaven:concrete_floor_01", "--offline", "--resolution", "1k"]
    if json_args == ["info"]:
        arguments.append("--json")
    data, result = invoke_json(arguments)
    assert data["provider"] == "Poly Haven"
    assert data["type"] == "textures"
    assert data["resolution"] == "1k"
    assert data["metadata"]["dimensions"]
    assert result.stderr == ""


def test_search_accepts_positional_query_and_type(metadata):
    data, _ = invoke_json(["search", "football", "--type", "models", "--offline", "--json"])
    assert [asset["id"] for asset in data] == ["dirty_football"]


def test_json_error_with_verbose_is_still_json(metadata):
    data, _ = invoke_json(["--verbose", "--json", "info", "missing", "--offline"], exit_code=1)
    assert "No cached Poly Haven metadata" in data["error"]


def test_command_local_json_error(metadata):
    data, _ = invoke_json(["info", "bad:asset", "--offline", "--json"], exit_code=1)
    assert "Invalid Poly Haven asset ID" in data["error"]


def test_dry_run_resolves_without_source_or_project_writes(install, metadata):
    before = sorted(str(p) for p in install.rglob("*"))
    data, result = invoke_json(
        [
            "import",
            "concrete_floor_01",
            "--project",
            "de_first",
            "--resolution",
            "1k",
            "--offline",
            "--dry-run",
            "--json",
        ]
    )
    assert data["dry_run"]
    assert data["project"] == "de_first"
    assert data["download_bytes"] > 0
    assert result.stderr == ""
    assert sorted(str(p) for p in install.rglob("*")) == before
    assert not (cache_dir() / "downloads").exists()
    assert not user_config_path().exists()


def test_import_precedence_cli_project_user(install, metadata):
    save_user_config({"project": "de_first", "resolution": "8k", "tiling": 1.0, "yaw": 5.0})
    save_project_config(
        install / "content/csgo_addons/de_first", {"resolution": "4k", "tiling": 2.0}
    )
    data, _ = invoke_json(
        ["import", "concrete_floor_01", "--resolution", "1k", "--offline", "--dry-run", "--json"]
    )
    assert data["options"]["resolution"] == "1k"
    assert data["options"]["tiling"] == 2.0
    assert data["options"]["yaw"] == 5.0
    assert data["project"] == "de_first"


def test_command_local_project_overrides_global_and_saved(install, metadata):
    save_user_config({"project": "addon_template"})
    data, _ = invoke_json(
        [
            "--project",
            "de_first",
            "import",
            "concrete_floor_01",
            "--project",
            "de_second",
            "--offline",
            "--dry-run",
            "--json",
        ]
    )
    assert data["project"] == "de_second"


@pytest.mark.parametrize("global_project", [True, False])
def test_config_show_resolves_selected_project(install, global_project):
    save_user_config({"project": "de_first", "tiling": 1.0})
    save_project_config(install / "content/csgo_addons/de_first", {"tiling": 2.0})
    save_project_config(install / "content/csgo_addons/de_second", {"tiling": 3.0})
    args = ["--project", "de_second"] if global_project else []
    data, _ = invoke_json([*args, "config", "show"])
    assert data["tiling"] == (3.0 if global_project else 2.0)


def test_invalid_import_option_does_not_execute_tools(install, metadata):
    data, _ = invoke_json(
        [
            "--json",
            "import",
            "dirty_football",
            "--project",
            "de_first",
            "--scale",
            "-1",
            "--offline",
            "--dry-run",
        ],
        exit_code=1,
    )
    assert "positive" in data["error"]


def test_invalid_config_value_is_json_error():
    data, _ = invoke_json(["--json", "config", "set", "scale", "not-a-number"], exit_code=1)
    assert "Invalid value for scale" in data["error"]


def test_empty_installed_assets_is_json_array(install):
    data, _ = invoke_json(["list", "--project", "de_first", "--json"])
    assert data == []


def test_config_show_without_project_needs_no_tools(monkeypatch):
    def unexpected_discovery(*args, **kwargs):
        pytest.fail("User-only config inspection must not require CS2")

    monkeypatch.setattr("cs2asset.cli.discover_cs2", unexpected_discovery)
    save_user_config({"tiling": 1.5})
    data, _ = invoke_json(["config", "show"])
    assert data == {"tiling": 1.5}


def test_config_show_local_project_reports_actual_project(install):
    save_user_config({"project": "de_first"})
    save_project_config(install / "content/csgo_addons/de_second", {"tiling": 2.0})
    data, _ = invoke_json(["--project", "de_first", "config", "show", "--project", "de_second"])
    assert data["project"] == "de_second"
    assert data["tiling"] == 2.0


def test_config_set_global_project_writes_portable_defaults(install):
    data, _ = invoke_json(["--project", "de_first", "config", "set", "tiling", "2.5"])
    path = install / "content/csgo_addons/de_first/.cs2asset/config.json"
    assert data["config"] == str(path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"tiling": 2.5}
    assert not user_config_path().exists()


def test_doctor_json_reports_capabilities_without_blender(install, monkeypatch):
    monkeypatch.setattr("cs2asset.cli.discover_blender", lambda _: None)
    monkeypatch.setattr("cs2asset.cli.executable_version", lambda *args: "Usage: resourcecompiler")
    data, _ = invoke_json(["doctor", "--project", "de_first", "--json"])
    assert data["capabilities"] == {"textures": True, "hdris": True, "models": False}
    assert data["write_access"] == {"content": True, "game": True}
    assert data["project"]["name"] == "de_first"


def test_search_without_query_lists_cached_catalog(metadata):
    data, _ = invoke_json(["search", "--offline", "--json"])
    assert len(data) == 3
