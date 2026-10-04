import numpy as np
import pytest

from cs2asset import blend, pipeline
from cs2asset.blend import create_blend_asset
from cs2asset.errors import CS2AssetError
from cs2asset.images import write_image
from cs2asset.installer import Installer
from cs2asset.pipeline import ImportOptions, rebuild_asset
from cs2asset.sources import SourceRegistry


@pytest.fixture
def layers(tmp_path):
    paths = []
    for name, color, roughness in (
        ("forest", [0.1, 0.7, 0.1], 0.8),
        ("rock", [0.6, 0.5, 0.4], 0.2),
    ):
        path = tmp_path / name
        write_image(path / f"{name}_albedo.png", np.broadcast_to(color, (8, 8, 3)))
        write_image(path / f"{name}_roughness.png", np.full((8, 8, 1), roughness))
        paths.append(str(path))
    return paths


@pytest.fixture
def compile_blend(workflow, monkeypatch):
    monkeypatch.setattr(blend, "compile_resources", pipeline.compile_resources)
    return workflow


def test_two_local_materials_compile_and_record_ordered_provenance(compile_blend, layers):
    w = compile_blend
    record = create_blend_asset(
        w.installation, w.project, w.provider, w.cache, *layers, "forest_rock", ImportOptions()
    )
    assert record["experimental"]
    assert record["validation"]["hammer_paint_verified"] is False
    assert [s["uri"] for s in record["sources"]] == layers
    assert [s["provider"] for s in record["sources"]] == ["local", "local"]
    assert record["resources"][0].endswith("forest_rock.vmat")
    text = (w.project.content_dir / record["resources"][0]).read_text()
    assert 'shader "csgo_environment_blend.vfx"' in text
    assert "TextureColor1" in text and "TextureColor2" in text
    assert "TextureRoughness1" in text and "TextureRoughness2" in text
    assert w.counts["download"] == 0
    assert w.counts["compile"] == 1
    assert len(record["snapshots"]) == 2


def test_blend_rebuild_and_edited_layer_rebuild_in_place(compile_blend, layers):
    w = compile_blend
    first = create_blend_asset(
        w.installation, w.project, w.provider, w.cache, *layers, "forest_rock", ImportOptions()
    )
    second = rebuild_asset(w.installation, w.project, w.provider, w.cache, first["id"])[0]
    assert second["id"] == first["id"]
    assert second["fingerprint"] == first["fingerprint"]
    from pathlib import Path

    write_image(Path(layers[1]) / "rock_roughness.png", np.full((8, 8, 1), 0.9))
    third = rebuild_asset(w.installation, w.project, w.provider, w.cache, first["id"])[0]
    assert third["id"] == first["id"]
    assert third["fingerprint"] != first["fingerprint"]
    assert len(Installer(w.project).imports()) == 1


def test_same_name_different_recipes_and_layer_order_coexist(compile_blend, layers):
    w = compile_blend
    first = create_blend_asset(
        w.installation, w.project, w.provider, w.cache, *layers, "forest_rock", ImportOptions()
    )
    second = create_blend_asset(
        w.installation,
        w.project,
        w.provider,
        w.cache,
        *reversed(layers),
        "forest_rock",
        ImportOptions(),
    )
    assert first["id"] != second["id"]
    assert first["resources"] != second["resources"]
    assert len(Installer(w.project).imports()) == 2


def test_blend_dry_run_has_no_snapshots_or_installation(workflow, layers):
    w = workflow
    result = create_blend_asset(
        w.installation,
        w.project,
        w.provider,
        w.cache,
        *layers,
        "forest_rock",
        ImportOptions(),
        dry_run=True,
    )
    assert result["dry_run"]
    assert result["planned_resources"][0].endswith("forest_rock.vmat")
    assert not w.cache.exists()
    assert not w.project.content_dir.exists()
    assert w.counts == {"download": 0, "convert": 0, "compile": 0}


def test_blend_rejects_sky_layer(workflow, layers, tmp_path):
    sky = write_image(tmp_path / "sky.exr", np.ones((8, 16, 3)), hdr=True)
    w = workflow
    with pytest.raises(CS2AssetError, match="Expected material.*sky"):
        create_blend_asset(
            w.installation,
            w.project,
            w.provider,
            w.cache,
            layers[0],
            str(sky),
            "bad",
            ImportOptions(),
        )
    assert not w.cache.exists()


def test_blend_rejects_cutout_before_compilation(compile_blend, layers):
    from pathlib import Path

    w = compile_blend
    write_image(Path(layers[0]) / "forest_alpha.png", np.full((8, 8, 1), 0.2))
    with pytest.raises(CS2AssetError, match="opacity/cutout"):
        create_blend_asset(
            w.installation, w.project, w.provider, w.cache, *layers, "forest_rock", ImportOptions()
        )
    assert w.counts["compile"] == 0
    assert not w.project.content_dir.exists()


@pytest.mark.parametrize(
    "inputs", [("polyhaven:test_asset", "polyhaven:test_asset"), ("local", "polyhaven:test_asset")]
)
def test_polyhaven_and_mixed_layers_share_resolution_and_converter(compile_blend, layers, inputs):
    from pathlib import Path

    w = compile_blend
    image = Path(layers[0]) / "forest_albedo.png"

    class Downloader:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def materialize(self, files, destination):
            destination.mkdir(parents=True)
            target = destination / "test.png"
            target.write_bytes(image.read_bytes())
            return {"base_color": target}

    with SourceRegistry(w.cache, legacy_provider=w.provider, downloader=Downloader) as registry:
        sources = [layers[0] if v == "local" else v for v in inputs]
        record = create_blend_asset(
            w.installation, w.project, registry, w.cache, *sources, "mixed", ImportOptions()
        )
    assert [s["provider"] for s in record["sources"]] == [
        "local" if v == "local" else "polyhaven" for v in inputs
    ]
    assert record["resources"][0].endswith("mixed.vmat")
    assert record["recipe"]["layer_options"][-1]["resolution"] == "2k"


def test_blend_cached_inputs_rebuild_and_failed_compile_preserves_record(
    compile_blend, layers, monkeypatch
):
    from pathlib import Path

    w = compile_blend
    first = create_blend_asset(
        w.installation, w.project, w.provider, w.cache, *layers, "forest_rock", ImportOptions()
    )
    (Path(layers[0]) / "forest_albedo.png").unlink()
    cached = rebuild_asset(
        w.installation, w.project, w.provider, w.cache, first["id"], cached_inputs=True
    )[0]
    assert cached["id"] == first["id"]
    monkeypatch.setattr(
        blend, "compile_resources", lambda *a, **k: (_ for _ in ()).throw(CS2AssetError("fail"))
    )
    with pytest.raises(CS2AssetError, match="fail"):
        rebuild_asset(
            w.installation, w.project, w.provider, w.cache, first["id"], cached_inputs=True
        )
    assert Installer(w.project).imports()[first["id"]] == cached


def test_blend_legacy_name_migrates_with_compiled_compatibility_copy(
    compile_blend, layers, monkeypatch
):
    named = blend.create_blend

    def legacy(*args, **kwargs):
        material, warnings = named(*args, **kwargs)
        old = material.with_name("blend.vmat")
        material.rename(old)
        return old, warnings

    w = compile_blend
    monkeypatch.setattr(blend, "create_blend", legacy)
    first = create_blend_asset(
        w.installation, w.project, w.provider, w.cache, *layers, "forest_rock", ImportOptions()
    )
    monkeypatch.setattr(blend, "create_blend", named)
    migrated = rebuild_asset(w.installation, w.project, w.provider, w.cache, first["id"])[0]
    assert migrated["resources"][0].endswith("/forest_rock.vmat")
    assert migrated["resource_aliases"] == {first["resources"][0]: migrated["resources"][0]}
    assert (w.project.game_dir / (first["resources"][0] + "_c")).is_file()
    repeated = rebuild_asset(w.installation, w.project, w.provider, w.cache, first["id"])[0]
    assert repeated["fingerprint"] == migrated["fingerprint"]


def test_blend_valve_warning_is_in_manifest(compile_blend, layers, monkeypatch):
    original = blend.compile_resources

    def warned(*args, **kwargs):
        result = original(*args, **kwargs)
        kwargs["diagnostics"].append(
            {"resource": "materials/probe.vmat", "message": "Probe warning", "log": "probe.log"}
        )
        return result

    monkeypatch.setattr(blend, "compile_resources", warned)
    w = compile_blend
    record = create_blend_asset(
        w.installation, w.project, w.provider, w.cache, *layers, "forest_rock", ImportOptions()
    )
    assert "Valve (materials/probe.vmat): Probe warning" in record["warnings"]
    assert (
        Installer(w.project).imports()[record["id"]]["compiler_warnings"]
        == record["compiler_warnings"]
    )
