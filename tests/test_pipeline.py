import hashlib
import json

import pytest

import cs2asset.pipeline as module
from cs2asset.errors import CS2AssetError
from cs2asset.installer import Installer, file_hash
from cs2asset.pipeline import ImportOptions, import_asset, rebuild_asset


def run(workflow, options=None, **kwargs):
    return import_asset(
        workflow.installation,
        workflow.project,
        workflow.provider,
        workflow.cache,
        "test_asset",
        options or ImportOptions(),
        phase=workflow.phases.append,
        **kwargs,
    )


def test_full_offline_pipeline_records_provenance_and_publishes_dependencies(workflow):
    record = run(workflow)
    assert record["asset"] == "test_asset"
    assert record["options"]["resolution"] == "2k"
    assert record["source_url"] == "https://polyhaven.com/a/test_asset"
    assert record["authors"] == {"Artist": "https://example.org"}
    assert record["files"][0]["md5"] == "098f6bcd4621d373cade4e832627b4f6"
    assert record["tools"]["compiler"]["sha256"] == file_hash(workflow.installation.compiler)
    assert record["warnings"] == ["upstream warning", "default roughness"]
    assert record["dimensions_mm"] == [2000, 2000]
    assert record["input_sha256"] == {"base_color": hashlib.sha256(b"test").hexdigest()}
    assert len(record["outputs"]) == 4
    assert (workflow.project.game_dir / "materials/default/generated.vtex_c").is_file()
    assert Installer(workflow.project).imports()[record["id"]] == record


def test_repeated_import_reuses_verified_conversion_but_checks_compiler(workflow):
    first = run(workflow)
    second = run(workflow)
    assert first["fingerprint"] == second["fingerprint"]
    assert workflow.counts == {"download": 1, "convert": 1, "compile": 2}
    assert "Reusing verified converted sources" in workflow.phases


def test_named_material_migration_retains_owned_legacy_resource(workflow, monkeypatch):
    named = module.create_material

    def old_material(content, directory, maps, **kwargs):
        kwargs.pop("name")
        return named(content, directory, maps, **kwargs)

    monkeypatch.setattr(module, "create_material", old_material)
    old = run(workflow)
    assert old["resources"][0].endswith("/material.vmat")
    monkeypatch.setattr(module, "create_material", named)
    migrated = run(workflow)
    assert migrated["resources"][0].endswith("/a_test_material.vmat")
    assert migrated["id"] == old["id"]
    assert migrated["resource_aliases"] == {old["resources"][0]: migrated["resources"][0]}
    assert (workflow.project.content_dir / old["resources"][0]).exists()
    assert (workflow.project.game_dir / (old["resources"][0] + "_c")).exists()
    again = rebuild_asset(
        workflow.installation, workflow.project, workflow.provider, workflow.cache, migrated["id"]
    )[0]
    assert again["fingerprint"] == migrated["fingerprint"]
    assert again["resource_aliases"] == migrated["resource_aliases"]


def test_valve_warning_is_in_record_and_combined_warnings(workflow, monkeypatch):
    compiler = module.compile_resources

    def warned(*args, **kwargs):
        outputs = compiler(*args, **kwargs)
        kwargs["diagnostics"].append(
            {"resource": "materials/test.vmat", "message": "Probe warning", "log": "probe.log"}
        )
        return outputs

    monkeypatch.setattr(module, "compile_resources", warned)
    record = run(workflow)
    assert record["compiler_warnings"][0]["message"] == "Probe warning"
    assert "Valve (materials/test.vmat): Probe warning" in record["warnings"]
    assert (
        Installer(workflow.project).imports()[record["id"]]["compiler_warnings"]
        == record["compiler_warnings"]
    )


def test_modified_cached_source_triggers_reconversion(workflow):
    first = run(workflow)
    stage = (
        workflow.installation.content_dir
        / "csgo_addons"
        / ("cs2asset_stage_" + first["fingerprint"][:24])
    )
    (stage / first["resources"][0]).write_text("broken source")
    run(workflow)
    assert workflow.counts == {"download": 2, "convert": 2, "compile": 2}


def test_compiler_update_reconverts_in_place(workflow):
    first = run(workflow)
    workflow.installation.compiler.write_bytes(b"test compiler v2")
    second = run(workflow)
    assert first["fingerprint"] != second["fingerprint"]
    assert first["id"] == second["id"]
    assert workflow.counts["convert"] == 2
    assert len(Installer(workflow.project).imports()) == 1


def test_settings_create_coexisting_variants(workflow):
    first = run(workflow)
    second = run(workflow, ImportOptions(tiling=2))
    assert first["variant"] != second["variant"]
    assert len(Installer(workflow.project).imports()) == 2


def test_dry_run_has_no_download_conversion_or_installation(workflow):
    record = run(workflow, dry_run=True)
    assert record["dry_run"]
    assert record["project"] == "de_test"
    assert record["download_bytes"] == 4
    assert workflow.counts == {"download": 0, "convert": 0, "compile": 0}
    assert not workflow.cache.exists()
    assert not workflow.project.content_dir.exists()


@pytest.mark.parametrize("error", [CS2AssetError("failed compile"), KeyboardInterrupt()])
def test_failed_or_cancelled_compile_does_not_publish(workflow, monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(module, "compile_resources", fail)
    with pytest.raises(type(error)):
        run(workflow)
    assert not workflow.project.content_dir.exists()
    assert not workflow.project.game_dir.exists()


def test_failed_rebuild_preserves_installed_import(workflow, monkeypatch):
    first = run(workflow)

    def fail(*args, **kwargs):
        raise CS2AssetError("compilation failed")

    monkeypatch.setattr(module, "compile_resources", fail)
    with pytest.raises(CS2AssetError):
        rebuild_asset(
            workflow.installation,
            workflow.project,
            workflow.provider,
            workflow.cache,
            "polyhaven:test_asset",
            force=True,
        )
    assert Installer(workflow.project).imports()[first["id"]] == first


def test_rebuild_reuses_saved_options_and_filters_variants(workflow):
    first = run(workflow, ImportOptions(tiling=3))
    run(workflow, ImportOptions(tiling=4))
    records = rebuild_asset(
        workflow.installation,
        workflow.project,
        workflow.provider,
        workflow.cache,
        "polyhaven:test_asset",
        variant=first["variant"],
        force=True,
    )
    assert len(records) == 1
    assert records[0]["options"]["tiling"] == 3
    assert workflow.counts["convert"] == 2


@pytest.mark.parametrize(
    "options",
    [
        ImportOptions(scale=0),
        ImportOptions(tiling=-1),
        ImportOptions(exposure=float("nan")),
        ImportOptions(yaw=float("inf")),
        ImportOptions(exposure=33),
        ImportOptions(collision="concave"),
        ImportOptions(normal_format="invalid"),
        ImportOptions(surface="x/../y"),
    ],
)
def test_invalid_options_fail_before_io(workflow, options):
    with pytest.raises(CS2AssetError):
        run(workflow, options)
    assert workflow.counts == {"download": 0, "convert": 0, "compile": 0}


@pytest.mark.parametrize(
    "manifest",
    [
        {},
        {"resources": [], "content_hashes": {}},
        {"resources": ["materials/missing.vmat"], "content_hashes": {}},
    ],
)
def test_empty_conversion_cache_is_rebuilt(workflow, manifest):
    record = run(workflow)
    ready = workflow.cache / "builds" / record["fingerprint"] / "converted.json"
    ready.write_text(json.dumps(manifest))
    run(workflow)
    assert workflow.counts["convert"] == 2


def test_reconversion_removes_obsolete_stage_dependencies(workflow):
    record = run(workflow)
    name = "cs2asset_stage_" + record["fingerprint"][:24]
    content = workflow.installation.content_dir / "csgo_addons" / name
    game = workflow.installation.game_dir / "csgo_addons" / name
    (content / record["resources"][0]).write_text("force reconversion")
    (content / "materials/obsolete.vmat").write_text("old variant")
    (game / "materials/obsolete.vmat_c").write_bytes(b"old variant")
    record = run(workflow)
    assert not (content / "materials/obsolete.vmat").exists()
    assert not (game / "materials/obsolete.vmat_c").exists()
    assert not any("obsolete" in output["path"] for output in record["outputs"])


def test_unowned_stage_is_never_cleared(workflow):
    record = run(workflow)
    name = "cs2asset_stage_" + record["fingerprint"][:24]
    stage = workflow.installation.content_dir / "csgo_addons" / name
    (stage / ".cs2asset-stage.json").unlink()
    with pytest.raises(CS2AssetError, match="not owned"):
        run(workflow)
    assert (stage / record["resources"][0]).read_text() == "source material"


def test_conversion_cache_path_traversal_is_rejected(workflow):
    record = run(workflow)
    ready = workflow.cache / "builds" / record["fingerprint"] / "converted.json"
    ready.write_text(
        json.dumps({"resources": ["../outside"], "content_hashes": {"../outside": "a" * 64}})
    )
    with pytest.raises(CS2AssetError, match="Unsafe resource path"):
        run(workflow)
