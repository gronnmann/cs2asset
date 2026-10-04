from types import SimpleNamespace

import pytest

import cs2asset.pipeline as module
from cs2asset.discovery import Installation, Project
from cs2asset.provider import Asset, DownloadFile, ResolvedAsset


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    compiler = tmp_path / "CS2/game/bin/win64/resourcecompiler.exe"
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(b"test compiler v1")
    installation = Installation(
        tmp_path / "CS2", compiler, tmp_path / "CS2/game", tmp_path / "CS2/content"
    )
    project = Project(
        "de_test",
        installation.content_dir / "csgo_addons/de_test",
        installation.game_dir / "csgo_addons/de_test",
    )
    resolved = ResolvedAsset(
        Asset(
            "test_asset",
            "A test material",
            "textures",
            {"authors": {"Artist": "https://example.org"}, "dimensions": [2000, 2000]},
        ),
        "2k",
        (
            DownloadFile(
                "base_color",
                "https://dl.polyhaven.org/test.png",
                "test.png",
                4,
                "098f6bcd4621d373cade4e832627b4f6",
            ),
        ),
        {"normal_convention": "opengl"},
        ("upstream warning",),
    )
    counts = {"download": 0, "convert": 0, "compile": 0}
    phases = []

    class Provider:
        offline = True

        def resolve_files(self, asset, resolution, refresh=False):
            assert asset in {"test_asset", "polyhaven:test_asset"}
            return resolved

    class Downloader:
        def __init__(self, cache, **kwargs):
            assert kwargs["offline"]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def materialize(self, files, target):
            counts["download"] += 1
            target.mkdir(parents=True, exist_ok=True)
            source = target / "test.png"
            source.write_bytes(b"test")
            return {"base_color": source}

    def material(content, directory, maps, **kwargs):
        counts["convert"] += 1
        target = content / directory / (kwargs.get("name", "material") + ".vmat")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("source material")
        (target.parent / "color.png").write_bytes(maps["base_color"].read_bytes())
        return target, ["default roughness"]

    def compile(installation, content, game, sources, logs, **kwargs):
        counts["compile"] += 1
        outputs = {}
        for source in sources:
            relative = source.relative_to(content).as_posix() + "_c"
            target = game / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"compiled " + source.read_bytes())
            outputs[relative] = target
        dependency = game / "materials/default/generated.vtex_c"
        dependency.parent.mkdir(parents=True, exist_ok=True)
        dependency.write_bytes(b"compiled dependency")
        outputs[dependency.relative_to(game).as_posix()] = dependency
        return outputs

    monkeypatch.setattr(module, "DownloadCache", Downloader)
    monkeypatch.setattr(module, "create_material", material)
    monkeypatch.setattr(module, "compile_resources", compile)
    monkeypatch.setattr(module, "executable_version", lambda *args: "Valve test build")
    return SimpleNamespace(
        installation=installation,
        project=project,
        provider=Provider(),
        cache=tmp_path / "cache",
        counts=counts,
        phases=phases,
        resolved=resolved,
    )
