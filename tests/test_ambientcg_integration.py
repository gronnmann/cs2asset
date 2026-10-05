"""Real provider imports into a dedicated validation addon, never the user's addon."""

import os

import pytest

from cs2asset.discovery import Project, discover_blender, discover_cs2
from cs2asset.pipeline import ImportOptions, import_asset, rebuild_asset
from cs2asset.sources import SourceRegistry

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("CS2ASSET_INTEGRATION") != "1",
        reason="Requires live downloads, Blender and CS2 tools",
    ),
]


@pytest.mark.parametrize(
    "identifier,kind",
    [("Grass005", "material"), ("3DApple001", "model"), ("DaySkyHDRI071A", "sky")],
)
def test_real_ambientcg_import_and_cached_rebuild(tmp_path, identifier, kind):
    installation = discover_cs2()
    project = Project(
        "cs2asset_ambientcg_validation",
        installation.content_dir / "csgo_addons/cs2asset_ambientcg_validation",
        installation.game_dir / "csgo_addons/cs2asset_ambientcg_validation",
    )
    blender = discover_blender()
    with SourceRegistry(tmp_path / "cache") as registry:
        report = import_asset(
            installation,
            project,
            registry,
            tmp_path / "cache",
            f"ambientcg:{identifier}",
            ImportOptions(resolution="1k"),
            blender=blender,
            asset_type=kind,
        )
    assert report["source"]["provider"] == "ambientcg"
    assert report["outputs"]
    assert all(
        (
            (project.game_dir if output["root"] == "game" else project.content_dir) / output["path"]
        ).is_file()
        for output in report["outputs"]
    )
    with SourceRegistry(tmp_path / "cache", offline=True) as registry:
        rebuilt = rebuild_asset(
            installation, project, registry, tmp_path / "cache", report["id"], blender=blender
        )[0]
    assert rebuilt["id"] == report["id"]
    assert rebuilt["fingerprint"] == report["fingerprint"]
