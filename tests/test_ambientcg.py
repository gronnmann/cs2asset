import copy
import io
import json
import zipfile
from pathlib import Path

import httpx
import numpy as np
import pytest

from cs2asset.assets.material import discover_maps
from cs2asset.cache import DownloadCache
from cs2asset.errors import CS2AssetError
from cs2asset.images import read_image, write_image
from cs2asset.materials import prepare_material_maps
from cs2asset.sources import SourceRegistry, canonical_source
from cs2asset.sources.ambientcg import AmbientCGProvider, AmbientCGSource
from cs2asset.sources.base import material_normal_format
from cs2asset.sources.local import LocalFileProvider

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/ambientcg/grass005.json").read_text(encoding="utf-8-sig")
)


def provider(tmp_path, data=FIXTURE, offline=False):
    return AmbientCGProvider(
        tmp_path,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data))
        ),
        offline=offline,
        retries=1,
    )


@pytest.mark.parametrize(
    "value",
    [
        "ambientcg:Grass005",
        "https://ambientcg.com/a/Grass005",
        "https://ambientcg.com/view?id=Grass005",
    ],
)
def test_routing(tmp_path, value):
    assert canonical_source(value) == "ambientcg:Grass005"
    with SourceRegistry(tmp_path) as registry:
        assert isinstance(registry.provider_for(value), AmbientCGSource)
    assert canonical_source("concrete_floor_01") == "polyhaven:concrete_floor_01"


def test_resolve_search_and_offline(tmp_path):
    online = provider(tmp_path)
    resolved = online.resolve_files("ambientcg:Grass005")
    assert resolved.source.id == "Grass005"
    assert resolved.choices["package"] == "2K-PNG"
    assert resolved.files[0].size == 73828852
    assert online.search("grass", "textures")[0].id == "Grass005"
    assert provider(tmp_path, offline=True).resolve_files("Grass005") == resolved
    with pytest.raises(CS2AssetError, match="No cached ambientCG"):
        provider(tmp_path, offline=True).resolve_files("Wood001")


def test_fallback_and_missing_resolution(tmp_path):
    data = copy.deepcopy(FIXTURE)
    data["assets"][0]["downloads"] = [
        d for d in data["assets"][0]["downloads"] if d["attributes"].endswith("JPG")
    ]
    assert provider(tmp_path, data).resolve_files("Grass005").choices["format"] == "jpg"
    with pytest.raises(CS2AssetError, match="Available"):
        provider(tmp_path, data).resolve_files("Grass005", "16k")


@pytest.mark.parametrize(
    "data,message",
    [
        ({"assets": []}, "not found"),
        ({"assets": {}}, "Malformed"),
        ({"assets": [{"id": "Grass005", "type": "hdri"}]}, "Malformed"),
        ({"assets": [{"id": "Grass005", "type": "substance"}]}, "Unsupported"),
        ({"assets": [{"id": "Grass005", "type": "material", "downloads": None}]}, "Malformed"),
    ],
)
def test_invalid_metadata(tmp_path, data, message):
    with pytest.raises(CS2AssetError, match=message):
        provider(tmp_path, data).resolve_files("Grass005")


@pytest.mark.parametrize("archive", [False, True])
def test_grass_pair_local(tmp_path, archive):
    root = tmp_path / "grass"
    color = write_image(root / "Grass005_2K-JPG_Color.jpg", np.full((4, 4, 3), 0.5))
    gl = write_image(root / "Grass005_2K-JPG_NormalGL.jpg", np.full((4, 4, 3), [0.5, 0.75, 1]))
    dx = write_image(root / "Grass005_2K-JPG_NormalDX.jpg", np.full((4, 4, 3), [0.5, 0.25, 1]))
    source = root
    if archive:
        source = tmp_path / "grass.zip"
        with zipfile.ZipFile(source, "w") as z:
            for p in (color, gl, dx):
                z.write(p, p.name)
    local = LocalFileProvider()
    resolved = local.resolve(str(source))
    material = local.materialize(resolved, tmp_path / "out")
    assert material.input.maps["normal"].name == gl.name
    assert material.input.normal_format == "gl"
    assert material_normal_format(material, "dx") == "gl"
    assert any("Both DX and GL" in w for w in resolved.warnings)


@pytest.mark.parametrize(
    "names",
    [
        ["rock_NormalGL.png", "rock_nor_gl.png"],
        ["rock_NormalDX.png", "rock_normal.png"],
        ["rock_NormalDX.png", "tree_NormalGL.png"],
    ],
)
def test_real_normal_ambiguities(names):
    with pytest.raises(CS2AssetError, match="Ambiguous|Multiple"):
        discover_maps(["rock_Color.png", *names])


def test_dx_only_flips_once(tmp_path):
    root = tmp_path / "dx"
    write_image(root / "rock_Color.png", np.full((4, 4, 3), 0.5))
    write_image(root / "rock_NormalDX.png", np.full((4, 4, 3), [0.5, 0.25, 1]))
    local = LocalFileProvider()
    material = local.materialize(local.resolve(str(root)), tmp_path / "inputs")
    maps, _ = prepare_material_maps(
        tmp_path / "output",
        material.input.maps,
        normal_format=material_normal_format(material, "gl"),
    )
    assert read_image(maps["normal"])[0, 0, 1] == pytest.approx(0.75, abs=0.01)


def test_remote_download_extract_and_offline(tmp_path, workflow):
    root = tmp_path / "images"
    write_image(root / "Grass005_Color.png", np.full((4, 4, 3), 0.5))
    write_image(root / "Grass005_NormalGL.png", np.full((4, 4, 3), [0.5, 0.75, 1]))
    write_image(root / "Grass005_NormalDX.png", np.full((4, 4, 3), [0.5, 0.25, 1]))
    contents = io.BytesIO()
    with zipfile.ZipFile(contents, "w") as z:
        for p in root.iterdir():
            z.write(p, p.name)
    body = contents.getvalue()
    data = copy.deepcopy(FIXTURE)
    data["assets"][0]["downloads"][5]["size"] = len(body)
    calls = []

    def download(request):
        calls.append(request.url)
        return httpx.Response(200, content=body)

    client = httpx.Client(transport=httpx.MockTransport(download))

    def downloader(cache, **kwargs):
        return DownloadCache(cache, client=client, **kwargs)

    remote = AmbientCGSource(provider(tmp_path, data), tmp_path, downloader=downloader)
    resolved = remote.resolve("ambientcg:Grass005")
    material = remote.materialize(resolved, tmp_path / "online")
    assert material.resolved.source.provider == "ambientcg"
    assert material.input.maps["normal"].name.endswith("NormalGL.png")
    assert material.resolved.choices["normal_convention"] == "opengl"
    maps, _ = prepare_material_maps(
        tmp_path / "converted", material.input.maps, normal_format=material.input.normal_format
    )
    assert read_image(maps["normal"])[0, 0, 1] == pytest.approx(0.75, abs=0.01)
    remote.provider.offline = True
    cached = remote.materialize(remote.resolve("Grass005"), tmp_path / "offline")
    assert cached.input_sha256 == material.input_sha256
    assert len(calls) == 1

    from cs2asset.pipeline import ImportOptions, import_asset, rebuild_asset

    first = import_asset(
        workflow.installation,
        workflow.project,
        remote,
        workflow.cache,
        "ambientcg:Grass005",
        ImportOptions(),
    )
    assert first["source"]["provider"] == "ambientcg"
    assert first["choices"]["normal_map"].endswith("NormalGL.png")
    assert "/ambientcg/Grass005/" in first["resources"][0]
    rebuilt = rebuild_asset(
        workflow.installation, workflow.project, remote, workflow.cache, "ambientcg:Grass005"
    )[0]
    assert rebuilt["id"] == first["id"]
    assert len(calls) == 1


@pytest.mark.parametrize(
    "kind,attribute,expected",
    [
        ("hdri", "4K", "sky"),
        ("3d-model", "SQ-2K-PNG", "model"),
    ],
)
def test_model_and_sky_metadata(tmp_path, kind, attribute, expected):
    data = {
        "assets": [
            {
                "id": "Example001",
                "type": kind,
                "downloads": [
                    {
                        "attributes": attribute,
                        "extension": "zip",
                        "size": 100,
                        "url": "https://ambientcg.com/get?file=test.zip",
                    }
                ],
            }
        ]
    }
    remote = provider(tmp_path, data)
    resolved = remote.resolve_files("Example001")
    assert resolved.asset.kind == expected
    assert resolved.resolution == ("4k" if expected == "sky" else "2k")
    assert remote.available_resolutions("Example001") == [resolved.resolution]
    assert remote.search("example", "hdris" if expected == "sky" else "models")[0].type == (
        "hdris" if expected == "sky" else "models"
    )


@pytest.mark.parametrize("kind", ["hdri", "3d-model"])
def test_remote_nonmaterial_packages(tmp_path, monkeypatch, kind):
    contents = io.BytesIO()
    with zipfile.ZipFile(contents, "w") as archive:
        if kind == "hdri":
            image = write_image(tmp_path / "source.exr", np.full((4, 8, 3), 2.0), hdr=True)
            archive.write(image, "Example001_HDR.exr")
            archive.writestr("Example001.blend", b"preview scene")
        else:
            archive.writestr("Example001.obj", "mtllib Example001.mtl\nv 0 0 0\n")
            archive.writestr("Example001.mtl", "newmtl Test\nmap_Kd Color.jpg\n")
            archive.writestr("Color.jpg", b"color dependency")
    body = contents.getvalue()
    data = {
        "assets": [
            {
                "id": "Example001",
                "type": kind,
                "downloads": [
                    {
                        "attributes": "4K" if kind == "hdri" else "SQ-2K-PNG",
                        "extension": "zip",
                        "size": len(body),
                        "url": "https://ambientcg.com/get?file=test.zip",
                    }
                ],
            }
        ]
    }
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))
    )

    def downloader(cache, **kwargs):
        return DownloadCache(cache, client=client, **kwargs)

    if kind == "3d-model":
        from dataclasses import replace

        from cs2asset.sources.local import _file

        def prepare(self, resolved, **kwargs):
            root = Path(resolved.files[0].source_path).parent
            return replace(
                resolved,
                files=(*resolved.files, _file("dependency:Color.jpg", root / "Color.jpg", root)),
            )

        monkeypatch.setattr(LocalFileProvider, "prepare", prepare)
    remote = AmbientCGSource(provider(tmp_path, data), tmp_path, downloader=downloader)
    resolved = remote.prepare(remote.resolve("Example001"), blender=tmp_path / "blender")
    normalized = remote.materialize(resolved, tmp_path / "out")
    assert normalized.resolved.source.provider == "ambientcg"
    assert normalized.input.path.suffix == (".exr" if kind == "hdri" else ".obj")
    if kind == "3d-model":
        assert any(p.name == "Color.jpg" for p in normalized.input.dependencies.values())
    remote.provider.offline = True
    assert remote.resolve("Example001") == resolved


def test_archive_traversal_and_ambiguous_sources(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as out:
        out.writestr("../escape.exr", b"bad")
    with pytest.raises(CS2AssetError):
        AmbientCGSource._extract(archive, tmp_path / "out")
    assert not (tmp_path / "escape.exr").exists()
    (tmp_path / "a.exr").write_bytes(b"a")
    (tmp_path / "b.exr").write_bytes(b"b")
    with pytest.raises(CS2AssetError, match="Ambiguous"):
        AmbientCGSource._select(tmp_path, {".exr", ".hdr"})


@pytest.mark.parametrize("minimum,clamped", [(-0.003, True), (-0.1, False)])
def test_hdr_noise_normalization_preserves_original(tmp_path, minimum, clamped):
    pixels = np.full((4, 8, 3), 2.0, dtype=np.float32)
    pixels[0, 0, 0] = minimum
    original = write_image(tmp_path / "original.exr", pixels, hdr=True, full_float=True)
    package = tmp_path / "sky.zip"
    with zipfile.ZipFile(package, "w") as out:
        out.write(original, "source.exr")
    data = {
        "assets": [
            {
                "id": "Example001",
                "type": "hdri",
                "downloads": [
                    {
                        "attributes": "4K",
                        "extension": "zip",
                        "size": package.stat().st_size,
                        "url": "https://ambientcg.com/get?file=sky.zip",
                    }
                ],
            }
        ]
    }

    class Downloader:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def materialize(self, files, destination):
            return {"package": package}

    source = AmbientCGSource(provider(tmp_path, data), tmp_path, downloader=Downloader)
    normalized = source.materialize(source.resolve("Example001"), tmp_path / "out")
    assert read_image(tmp_path / "out/sky/source.exr")[0, 0, 0] == pytest.approx(minimum)
    assert float(read_image(normalized.input.path).min()) == pytest.approx(
        0 if clamped else minimum
    )
    assert bool(normalized.resolved.warnings) is clamped
