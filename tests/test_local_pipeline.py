import json
from pathlib import Path

import numpy as np
import pytest

from cs2asset import pipeline
from cs2asset.errors import CS2AssetError
from cs2asset.images import write_image
from cs2asset.installer import Installer
from cs2asset.materials import create_material
from cs2asset.models import ModelExport, ModelMaterial
from cs2asset.pipeline import ImportOptions, import_asset, rebuild_asset
from cs2asset.sources.local import LocalFileProvider


def local_import(workflow, source, options=None, **kwargs):
    return import_asset(
        workflow.installation,
        workflow.project,
        workflow.provider,
        workflow.cache,
        str(source),
        options or ImportOptions(),
        **kwargs,
    )


def material_folder(path):
    write_image(path / "rock_albedo.png", np.full((4, 4, 3), 0.5))
    write_image(path / "rock_roughness.png", np.full((4, 4, 1), 0.7))
    return path


def test_local_material_directory_provenance_and_rebuild(workflow, tmp_path):
    folder = material_folder(tmp_path / "rock")
    first = local_import(workflow, folder, ImportOptions(tiling=2))
    assert first["source"]["provider"] == "local"
    assert first["source"]["uri"] == str(folder.resolve())
    assert first["source"]["license"] is None
    assert first["record_version"] == 2
    assert "/local/" in first["resources"][0]
    assert workflow.counts["download"] == 0
    rebuilt = rebuild_asset(
        workflow.installation, workflow.project, workflow.provider, workflow.cache, str(folder)
    )[0]
    assert first["id"] == rebuilt["id"]
    assert rebuilt["options"]["tiling"] == 2
    assert workflow.counts["convert"] == 1


def test_local_edit_changes_build_but_preserves_identity(workflow, tmp_path):
    folder = material_folder(tmp_path / "rock")
    first = local_import(workflow, folder)
    write_image(folder / "rock_albedo.png", np.full((4, 4, 3), 0.9))
    second = local_import(workflow, folder)
    assert second["fingerprint"] != first["fingerprint"]
    assert second["id"] == first["id"]
    assert len(Installer(workflow.project).imports()) == 1


def test_local_names_coexist_with_provider_and_other_folders(workflow, tmp_path):
    records = [
        local_import(workflow, material_folder(tmp_path / name / "test_asset"))
        for name in ("a", "b")
    ]
    records.append(
        import_asset(
            workflow.installation,
            workflow.project,
            workflow.provider,
            workflow.cache,
            "polyhaven:test_asset",
            ImportOptions(),
        )
    )
    assert len({r["id"] for r in records}) == 3
    assert len({r["resources"][0] for r in records}) == 3
    assert len(Installer(workflow.project).imports()) == 3


def test_identity_collision_preserves_existing_import(workflow, tmp_path, monkeypatch):
    monkeypatch.setattr("cs2asset.sources.local.local_identity", lambda path: "same_id")
    first = local_import(workflow, material_folder(tmp_path / "first"))
    before = Installer(workflow.project).imports()
    target = workflow.project.content_dir / first["resources"][0]
    original = target.read_bytes()
    with pytest.raises(CS2AssetError, match="Import identity collision"):
        local_import(workflow, material_folder(tmp_path / "second"), overwrite=True)
    assert Installer(workflow.project).imports() == before
    assert target.read_bytes() == original


def test_unicode_source_filenames_import_and_rebuild(workflow, tmp_path):
    folder = tmp_path / "terrain łódź"
    write_image(folder / "café albedo.png", np.full((4, 4, 3), 0.5))
    first = local_import(workflow, folder)
    second = rebuild_asset(
        workflow.installation, workflow.project, workflow.provider, workflow.cache, first["id"]
    )[0]
    assert second["id"] == first["id"]
    assert workflow.counts["convert"] == 1


@pytest.mark.parametrize("extension", [".hdr", ".exr"])
def test_local_sky_pipeline_and_rebuild(workflow, tmp_path, extension):
    source = write_image(tmp_path / f"evening{extension}", np.full((8, 16, 3), 4.0), hdr=True)
    first = local_import(workflow, source)
    assert first["type"] == "hdris"
    assert first["detail"]["maximum_radiance"] == pytest.approx(4)
    assert first["resources"][0].endswith("evening.vmat")
    second = rebuild_asset(
        workflow.installation, workflow.project, workflow.provider, workflow.cache, first["id"]
    )[0]
    assert second["id"] == first["id"]


@pytest.mark.parametrize("extension", [".blend", ".glb", ".gltf", ".fbx", ".obj"])
def test_local_model_reaches_existing_converter(workflow, tmp_path, monkeypatch, extension):
    source = tmp_path / f"rock{extension}"
    source.write_text("{}" if extension == ".gltf" else "model")
    blender = tmp_path / "blender.exe"
    blender.write_bytes(b"blender")
    monkeypatch.setattr(LocalFileProvider, "prepare", lambda self, r, **kw: r)

    def export(path, output, executable, **kwargs):
        assert path.read_bytes() == source.read_bytes()
        assert executable == blender
        assert kwargs["dependency_remap"]
        output.mkdir(parents=True)
        fbx = output / "mesh.fbx"
        fbx.write_bytes(b"mesh")
        return ModelExport(
            fbx,
            [ModelMaterial("mat", {}, {"roughness": 0.5})],
            [1, 2, 3],
            [],
            "test",
            1,
            12,
            {0: fbx},
        )

    monkeypatch.setattr(pipeline, "export_model", export)
    monkeypatch.setattr(pipeline, "create_material", create_material)
    first = local_import(workflow, source, blender=blender)
    assert first["type"] == "models"
    assert first["resources"][-1].endswith("rock.vmdl")
    assert first["detail"]["collision"] == "hull"
    assert first["snapshot"]["dependency_remap"]


@pytest.mark.parametrize("kind", ["material", "sky", "model"])
def test_local_dry_run_has_no_side_effects(workflow, tmp_path, monkeypatch, kind):
    if kind == "material":
        source = material_folder(tmp_path / "rock")
    elif kind == "sky":
        source = write_image(tmp_path / "sky.exr", np.ones((4, 8, 3)), hdr=True)
    else:
        source = tmp_path / "rock.blend"
        source.write_bytes(b"model")
    monkeypatch.setattr(LocalFileProvider, "prepare", lambda *a, **k: pytest.fail("Blender"))
    record = local_import(workflow, source, dry_run=True)
    assert record["dry_run"]
    assert record["download_bytes"] == 0
    assert record["source"]["provider"] == "local"
    assert not workflow.cache.exists()
    assert not workflow.project.content_dir.exists()
    assert workflow.counts == {"download": 0, "convert": 0, "compile": 0}


def test_cached_rebuild_after_source_removal_and_corruption(workflow, tmp_path):
    folder = material_folder(tmp_path / "rock")
    first = local_import(workflow, folder)
    (folder / "rock_albedo.png").unlink()
    with pytest.raises(CS2AssetError):
        rebuild_asset(
            workflow.installation, workflow.project, workflow.provider, workflow.cache, first["id"]
        )
    cached = rebuild_asset(
        workflow.installation,
        workflow.project,
        workflow.provider,
        workflow.cache,
        first["id"],
        cached_inputs=True,
    )[0]
    assert cached["id"] == first["id"]
    root = Path(first["snapshot"]["root"])
    (root / "rock_albedo.png").write_bytes(b"corrupted")
    with pytest.raises(CS2AssetError, match="modified cached input"):
        rebuild_asset(
            workflow.installation,
            workflow.project,
            workflow.provider,
            workflow.cache,
            first["id"],
            cached_inputs=True,
        )


def test_legacy_manifest_rebuild_keeps_id_paths_and_ownership(workflow):
    first = import_asset(
        workflow.installation,
        workflow.project,
        workflow.provider,
        workflow.cache,
        "test_asset",
        ImportOptions(),
    )
    manifest_path = workflow.project.content_dir / ".cs2asset/imports.json"
    manifest = json.loads(manifest_path.read_text())
    record = manifest["imports"][first["id"]]
    record.pop("source")
    record.pop("record_version")
    manifest_path.write_text(json.dumps(manifest))
    second = rebuild_asset(
        workflow.installation,
        workflow.project,
        workflow.provider,
        workflow.cache,
        "polyhaven:test_asset",
    )[0]
    assert second["id"] == first["id"]
    assert second["resources"] == first["resources"]
    assert second["outputs"] == first["outputs"]
    assert second["source"]["provider"] == "polyhaven"


def test_conversion_is_independent_of_source(workflow, tmp_path, monkeypatch):
    from dataclasses import replace

    from cs2asset.assets import SourceRef

    monkeypatch.setattr(pipeline, "create_material", create_material)
    folder = material_folder(tmp_path / "rock")
    provider = LocalFileProvider()
    resolved = provider.resolve(str(folder))
    local = provider.materialize(resolved, tmp_path / "snapshot")
    other = replace(local, resolved=replace(resolved, source=SourceRef("future", "future:rock")))
    a, b = tmp_path / "a", tmp_path / "b"
    for asset, content in ((local, a), (other, b)):
        pipeline.convert_asset(
            asset, content, tmp_path / "build", "native_variant", ImportOptions(), None
        )
    files = {p.relative_to(a): p.read_bytes() for p in a.rglob("*") if p.is_file()}
    assert files == {p.relative_to(b): p.read_bytes() for p in b.rglob("*") if p.is_file()}


def test_unsupported_input_has_helpful_error(workflow, tmp_path):
    source = tmp_path / "rock.usd"
    source.write_bytes(b"model")
    with pytest.raises(CS2AssetError, match="Unsupported local input.*PBR.*static model"):
        local_import(workflow, source)
