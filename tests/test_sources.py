import json
import os
import stat
import zipfile

import numpy as np
import pytest

from cs2asset.assets import MaterialInput, ModelInput, SkyInput
from cs2asset.assets.material import ALIASES, discover_maps
from cs2asset.errors import CS2AssetError
from cs2asset.images import read_image, resize_image, write_image
from cs2asset.sources import SourceRegistry, canonical_source, record_source
from cs2asset.sources.base import input_path
from cs2asset.sources.local import LocalFileProvider, SnapshotProvider


@pytest.mark.parametrize("role,alias", [(r, a) for r, aliases in ALIASES.items() for a in aliases])
def test_conventional_aliases(role, alias):
    names = [f"rock_{alias}.png"]
    if role != "base_color":
        names.append("rock_albedo.png")
    maps, choices, _warnings = discover_maps(names)
    assert maps[role] == names[0]
    if alias in {"normaldx", "nor_dx"}:
        assert choices["normal_convention"] == "directx"


@pytest.mark.parametrize(
    "names",
    [
        ["rock_albedo.png", "rock_color.png"],
        ["rock_albedo.png", "tree_normal.png"],
        ["rock_albedo_1k.png", "rock_albedo_2k.png"],
        ["a/rock_albedo.png", "b/rock_rough.png"],
    ],
)
def test_ambiguous_sets_are_actionable(names):
    with pytest.raises(CS2AssetError, match="Ambiguous|Multiple"):
        discover_maps(names)


def test_matching_is_not_arbitrary_substring():
    with pytest.raises(CS2AssetError, match="No recognizable"):
        discover_maps(["colorful_rock.png", "abnormal.png", "metallically.png"])


def test_local_material_snapshot_and_unknown_license(tmp_path):
    folder = tmp_path / "rock"
    write_image(folder / "rock_albedo.png", np.full((4, 4, 3), 0.5))
    write_image(folder / "rock_normal.png", np.ones((4, 4, 3)))
    provider = LocalFileProvider()
    resolved = provider.resolve(str(folder))
    assert resolved.asset.kind == "material"
    assert resolved.source.license is None
    assert "assuming OpenGL" in resolved.warnings[0]
    asset = provider.materialize(resolved, tmp_path / "snapshot")
    assert isinstance(asset.input, MaterialInput)
    assert asset.input.maps["base_color"].is_file()
    assert str(folder) == resolved.source.uri
    assert asset.input_sha256["base_color"] == resolved.files[0].sha256


@pytest.mark.parametrize("extension", [".hdr", ".exr"])
def test_local_sky_normalized_input(tmp_path, extension):
    source = write_image(tmp_path / f"sky{extension}", np.full((8, 16, 3), 4.0), hdr=True)
    provider = LocalFileProvider()
    result = provider.materialize(provider.resolve(str(source)), tmp_path / "snapshot")
    assert isinstance(result.input, SkyInput)
    assert read_image(result.input.path).max() == pytest.approx(4)


def test_gltf_dependencies_are_preserved(tmp_path):
    source = tmp_path / "models" / "rock.gltf"
    source.parent.mkdir()
    buffer = source.parent / "rock.bin"
    buffer.write_bytes(b"buffer")
    texture = write_image(tmp_path / "textures" / "color.png", np.ones((4, 4, 3)))
    source.write_text(
        json.dumps({"buffers": [{"uri": "rock.bin"}], "images": [{"uri": "../textures/color.png"}]})
    )
    provider = LocalFileProvider()
    resolved = provider.resolve(str(source))
    asset = provider.materialize(resolved, tmp_path / "snapshot")
    assert isinstance(asset.input, ModelInput)
    assert asset.input.path.parent.name == "models"
    assert (asset.input.path.parent / "rock.bin").read_bytes() == b"buffer"
    assert (asset.input.path.parent / "../textures/color.png").is_file()
    assert os.path.normcase(str(texture)) in asset.input.dependency_remap


def test_missing_model_dependency_names_source_and_reference(tmp_path):
    source = tmp_path / "rock.gltf"
    source.write_text('{"buffers": [{"uri": "missing.bin"}]}')
    with pytest.raises(CS2AssetError, match="rock.gltf.*missing.bin"):
        LocalFileProvider().resolve(str(source))


def test_kind_mismatch(tmp_path):
    source = write_image(tmp_path / "sky.exr", np.ones((4, 8, 3)), hdr=True)
    with pytest.raises(CS2AssetError, match="Expected material.*sky"):
        LocalFileProvider().resolve(str(source), asset_type="material")


def test_zip_material_and_snapshot(tmp_path):
    image = write_image(tmp_path / "albedo.png", np.ones((4, 4, 3)))
    source = tmp_path / "rock.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.write(image, "rock/rock_albedo.png")
    provider = LocalFileProvider()
    result = provider.materialize(provider.resolve(str(source)), tmp_path / "snapshot")
    assert result.input.maps["base_color"].read_bytes() == image.read_bytes()


@pytest.mark.parametrize("member", ["../rock_albedo.png", "C:/rock_albedo.png", "/rock_albedo.png"])
def test_unsafe_zip_paths(tmp_path, member):
    source = tmp_path / "rock.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(member, b"image")
    with pytest.raises(CS2AssetError):
        LocalFileProvider().resolve(str(source))


def test_zip_links_and_case_collisions(tmp_path):
    source = tmp_path / "rock.zip"
    with zipfile.ZipFile(source, "w") as archive:
        item = zipfile.ZipInfo("rock_albedo.png")
        item.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(item, "target")
    with pytest.raises(CS2AssetError, match="links"):
        LocalFileProvider().resolve(str(source))
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("rock_albedo.png", b"a")
        archive.writestr("ROCK_ALBEDO.png", b"b")
    with pytest.raises(CS2AssetError, match="Colliding"):
        LocalFileProvider().resolve(str(source))


def test_source_changes_during_snapshot_are_rejected(tmp_path):
    source = write_image(tmp_path / "sky.exr", np.ones((4, 8, 3)), hdr=True)
    provider = LocalFileProvider()
    resolved = provider.resolve(str(source))
    source.write_bytes(b"changed")
    with pytest.raises(CS2AssetError, match="Source changed"):
        provider.materialize(resolved, tmp_path / "snapshot")


def test_explicit_snapshot_works_without_original_and_checks_hashes(tmp_path):
    source = write_image(tmp_path / "sky.exr", np.ones((4, 8, 3)), hdr=True)
    provider = LocalFileProvider()
    asset = provider.materialize(provider.resolve(str(source)), tmp_path / "snapshot")
    source.unlink()
    cached = SnapshotProvider(asset.snapshot)
    resolved = cached.prepare(cached.resolve(str(source)))
    result = cached.materialize(resolved, tmp_path / "second")
    assert result.input.path.is_file()
    asset.input.path.write_bytes(b"corrupted")
    with pytest.raises(CS2AssetError, match="modified cached input"):
        cached.prepare(resolved)


def test_routing_local_never_constructs_http_provider(tmp_path, monkeypatch):
    from cs2asset import sources

    source = write_image(tmp_path / "sky.exr", np.ones((4, 8, 3)), hdr=True)
    monkeypatch.setattr(sources, "PolyHavenProvider", lambda *a, **k: pytest.fail("HTTP provider"))
    with SourceRegistry(tmp_path / "cache") as registry:
        assert registry.provider_for(str(source)).resolve(str(source)).asset.kind == "sky"
        with pytest.raises(CS2AssetError, match="does not exist"):
            registry.provider_for(str(tmp_path / "missing.glb")).resolve(
                str(tmp_path / "missing.glb")
            )
    assert not (tmp_path / "cache").exists()


def test_legacy_provenance_adapter_does_not_mutate_record():
    record = {
        "id": "polyhaven:rock:2k_variant",
        "asset": "rock",
        "source_url": "https://polyhaven.com/a/rock",
    }
    before = dict(record)
    assert record_source(record)["uri"] == "polyhaven:rock"
    assert record == before


def test_canonical_polyhaven_url_and_local_path(tmp_path):
    assert canonical_source("https://polyhaven.com/a/rock") == "polyhaven:rock"
    assert canonical_source(str(tmp_path / "missing.glb")) == str(
        (tmp_path / "missing.glb").resolve()
    )


def test_resize_preserves_hdr_aspect_and_does_not_upscale(tmp_path):
    source = write_image(tmp_path / "sky.exr", np.full((16, 32, 3), 4.0), hdr=True)
    resized = resize_image(source, tmp_path / "small.exr", 8)
    pixels = read_image(resized)
    assert pixels.shape == (4, 8, 3)
    assert pixels.max() == pytest.approx(4)
    assert resize_image(source, tmp_path / "big.exr", 64) == source


def test_source_registry_accepts_future_provider_without_converter_changes(tmp_path):
    provider = object()
    with SourceRegistry(tmp_path / "cache") as registry:
        registry.register("future", lambda: provider)
        assert registry.provider_for("future:asset/path.png") is provider
        with pytest.raises(CS2AssetError, match="Unsupported source provider: other"):
            registry.provider_for("other:asset/path.png")
        with pytest.raises(CS2AssetError, match="drive letters"):
            registry.register("c", lambda: provider)
    assert canonical_source("future:asset/path.png") == "future:asset/path.png"


def test_spaces_and_unicode_in_snapshot_dependencies(tmp_path):
    source = tmp_path / "rocher łódź.gltf"
    texture = write_image(tmp_path / "images" / "café color.png", np.ones((4, 4, 3)))
    source.write_text(
        json.dumps({"images": [{"uri": "images/caf%C3%A9%20color.png"}]}), encoding="utf-8"
    )
    provider = LocalFileProvider()
    asset = provider.materialize(provider.resolve(str(source)), tmp_path / "snapshot")
    assert asset.input.path.name == source.name
    assert (asset.input.path.parent / "images" / texture.name).is_file()
    cached = SnapshotProvider(asset.snapshot)
    result = cached.materialize(cached.prepare(cached.resolve(str(source))), tmp_path / "copy")
    assert result.input.path.name == source.name
    assert os.path.normcase(str(texture)) in result.input.dependency_remap


@pytest.mark.parametrize("relative", ["../outside.png", "C:/outside.png", "image.png:stream"])
def test_source_snapshot_paths_retain_containment(tmp_path, relative):
    with pytest.raises(CS2AssetError, match="Unsafe asset dependency path"):
        input_path(tmp_path, relative)
