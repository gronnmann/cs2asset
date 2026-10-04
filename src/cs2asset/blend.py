"""Source-independent blend recipes using the existing staging/compile/install guarantees."""

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

from filelock import FileLock

from .assets import BlendInput
from .cache import DownloadCache
from .compiler import compile_resources
from .config import atomic_json, read_json
from .converters.blend import BLEND_CONTRACT_VERSION, BLEND_WARNING, create_blend
from .errors import CS2AssetError
from .installer import Installer, contained_path, file_hash
from .pipeline import (
    ImportOptions,
    _clear_owned_stage,
    _converted_valid,
    _file_set,
    _stage,
    _tool_fingerprint,
    legacy_resource_paths,
    material_maps,
    plan_import,
    previous_import,
    retain_resource_aliases,
)
from .resource_names import resource_name
from .sources import source_provider
from .sources.local import SnapshotProvider


def blend_capability(installation):
    config = installation.game_context / "tools/met/met_shaderconfig.kv3"
    present = config.is_file() and "csgo_environment_blend" in config.read_text(encoding="utf-8")
    return {
        "shader_present": present,
        "status": "experimental",
        "contract": BLEND_CONTRACT_VERSION,
        "compiler_verified": True,
        "verified_cs2_build": "25687242",
        "hammer_paint_verified": False,
        "note": BLEND_WARNING,
    }


def create_blend_asset(
    installation,
    project,
    provider,
    cache: Path,
    a: str,
    b: str,
    name: str,
    options: ImportOptions,
    *,
    dry_run=False,
    refresh=False,
    overwrite=False,
    force=False,
    phase=None,
    download_progress=None,
    compiler_progress=None,
    layer_providers=None,
    layer_options=None,
    blender=None,
):
    options.validate()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", name):
        raise CS2AssetError("Blend name must be 1-64 letters, digits, underscores, or hyphens")
    if options.tiling != 1:
        raise CS2AssetError("Custom blend tiling is not validated yet; use the default tiling of 1")
    announce = phase or (lambda message: None)
    announce("Resolving both blend material sources")
    providers = layer_providers or [
        source_provider(
            provider, value, cache, downloader=DownloadCache, progress=download_progress
        )
        for value in (a, b)
    ]
    selection = layer_options or [asdict(options), asdict(options)]
    resolved = [
        source.resolve(value, chosen["resolution"], refresh=refresh, asset_type="material")
        for source, value, chosen in zip(providers, (a, b), selection, strict=True)
    ]
    effective = [ImportOptions(**{**asdict(options), "resolution": r.resolution}) for r in resolved]
    recipe_key = hashlib.sha256(json.dumps([r.identity for r in resolved]).encode()).hexdigest()[
        :16
    ]
    settings_key = hashlib.sha256(
        json.dumps([asdict(o) for o in effective], sort_keys=True).encode()
    ).hexdigest()[:10]
    identifier, variant = f"{name.lower()}_{recipe_key}", f"blend_{settings_key}"
    namespace = f"materials/cs2asset/blends/{identifier}/{variant}"
    description = {
        "provider": "blend",
        "asset": identifier,
        "name": name,
        "type": "blend",
        "kind": "blend",
        "resolution": "+".join(r.resolution for r in resolved),
        "variant": variant,
        "sources": [asdict(r.source) for r in resolved],
        "options": asdict(options),
        "layers": [plan_import(r, o) for r, o in zip(resolved, effective, strict=True)],
        "recipe": {
            "inputs": [r.source.uri for r in resolved],
            "name": name,
            "layer_options": [asdict(o) for o in effective],
            "contract": BLEND_CONTRACT_VERSION,
        },
        "experimental": True,
        "validation": blend_capability(installation),
        "warnings": [BLEND_WARNING],
        "download_bytes": sum(r.download_size for r in resolved),
    }
    if dry_run:
        return {
            **description,
            "dry_run": True,
            "project": project.name,
            "planned_resources": [f"{namespace}/{resource_name(name)}.vmat"],
        }
    # Verify cached snapshots even if converted resources will be reused.
    resolved = [source.prepare(r) for source, r in zip(providers, resolved, strict=True)]
    import_id = f"blend:{identifier}:{variant}"
    previous = previous_import(project, import_id)
    legacy_paths = legacy_resource_paths(previous)
    tools = _tool_fingerprint(installation, None)
    tools["blend_contract"] = BLEND_CONTRACT_VERSION
    tools["blend_converter"] = file_hash(Path(__file__).parent / "converters/blend.py")
    identity = {
        "recipe": description["recipe"],
        "inputs": [asdict(r) for r in resolved],
        "tools": tools,
        "legacy_paths": legacy_paths,
    }
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    build = cache / "builds" / fingerprint
    build.mkdir(parents=True, exist_ok=True)
    stage_parent = installation.content_dir / "csgo_addons"
    stage_parent.mkdir(parents=True, exist_ok=True)
    with (
        FileLock(str(build / "build.lock"), timeout=600),
        FileLock(str(stage_parent / f".cs2asset_stage_{fingerprint[:24]}.lock"), timeout=600),
    ):
        content, game = _stage(installation, fingerprint)
        ready = build / "converted.json"
        converted = read_json(ready)
        if not _converted_valid(converted, content):
            _clear_owned_stage(content, game)
            announce("Reading and normalizing both material layers")
            layers = tuple(
                source.materialize(r, build / "inputs" / f"layer{index}")
                for index, (source, r) in enumerate(zip(providers, resolved, strict=True), 1)
            )
            blend = BlendInput(layers, name, BLEND_CONTRACT_VERSION)
            maps = [
                material_maps(layer, build / f"layer{index}")
                for index, layer in enumerate(layers, 1)
            ]
            material, warnings = create_blend(
                content,
                namespace,
                blend,
                maps=maps,
                normal_format=options.normal_format,
                surface=options.surface,
            )
            converted = {
                "resources": [material.relative_to(content).as_posix()],
                "warnings": warnings,
                "content_hashes": {p: file_hash(f) for p, f in _file_set(content).items()},
                "input_sha256": [layer.input_sha256 for layer in layers],
                "snapshots": [layer.snapshot for layer in layers],
                "alias_candidates": {
                    f"{namespace}/blend.vmat": material.relative_to(content).as_posix()
                },
            }
            retain_resource_aliases(converted, content, legacy_paths)
            atomic_json(ready, converted)
        else:
            announce("Reusing verified converted blend sources")
        announce("Compiling environment blend with Valve resourcecompiler")
        compiler_warnings = []
        compiled = compile_resources(
            installation,
            content,
            game,
            [
                content / p
                for p in [*converted["resources"], *converted.get("resource_aliases", {})]
            ],
            build / "logs",
            force=force,
            progress=compiler_progress,
            diagnostics=compiler_warnings,
        )
        metadata = {
            **description,
            "record_version": 2,
            "fingerprint": fingerprint,
            "tools": tools,
            "resources": converted["resources"],
            "warnings": [
                *converted["warnings"],
                *[f"Valve ({w['resource']}): {w['message']}" for w in compiler_warnings],
            ],
            "compiler_warnings": compiler_warnings,
            "resource_aliases": converted.get("resource_aliases", {}),
            "snapshots": converted["snapshots"],
            "input_sha256": converted["input_sha256"],
            "logs": str(build / "logs"),
        }
        announce(f"Installing environment blend into {project.name}")
        return Installer(project).install(
            import_id,
            {p: contained_path(content, p) for p in converted["content_hashes"]},
            compiled,
            metadata,
            overwrite=overwrite,
        )


def rebuild_blend(installation, project, provider, cache, record, *, cached_inputs=False, **kwargs):
    providers = None
    if cached_inputs:
        providers = []
        for source, snapshot in zip(record["sources"], record["snapshots"], strict=True):
            if source["provider"] == "local":
                if not snapshot:
                    raise CS2AssetError("Blend layer has no retained input snapshot")
                providers.append(SnapshotProvider(snapshot))
            else:
                providers.append(source_provider(provider, source["uri"], cache))
    recipe = record["recipe"]
    return create_blend_asset(
        installation,
        project,
        provider,
        cache,
        *recipe["inputs"],
        recipe["name"],
        ImportOptions(**record["options"]),
        layer_providers=providers,
        layer_options=recipe["layer_options"],
        **kwargs,
    )
