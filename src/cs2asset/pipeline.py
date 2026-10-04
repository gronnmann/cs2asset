"""Download, convert, compile, and publish complete imports."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from filelock import FileLock

from . import __version__
from .assets import MaterialInput, ModelInput, NormalizedAsset, ResolvedAsset, SkyInput
from .cache import DownloadCache
from .compiler import compile_resources
from .config import atomic_json, read_json
from .discovery import Installation, Project, discover_blender, executable_version
from .errors import CS2AssetError
from .images import read_image, write_image
from .installer import Installer, contained_path, file_hash
from .materials import TEMPLATE_VERSION, create_material, create_sky
from .models import MODEL_CONVERTER_VERSION, export_model, write_modeldoc
from .resource_names import model_material_names, resource_name
from .sources import canonical_source, record_source, source_provider
from .sources.base import input_path
from .sources.local import SnapshotProvider
from .sources.polyhaven import adapt_resolved


@dataclass(frozen=True)
class ImportOptions:
    resolution: str | None = None
    scale: float = 1.0
    collision: str = "hull"
    surface: str = "default"
    tiling: float = 1.0
    yaw: float = 0.0
    exposure: float = 0.0
    normal_format: str | None = None

    def validate(self):
        import re

        for name in ("scale", "tiling", "yaw", "exposure"):
            if not math.isfinite(getattr(self, name)):
                raise CS2AssetError(f"{name} must be finite")
        if self.scale <= 0 or self.tiling <= 0:
            raise CS2AssetError("Scale and tiling must be positive")
        if self.collision not in {"hull", "none"}:
            raise CS2AssetError("Collision must be hull or none")
        if self.normal_format not in {None, "gl", "dx"}:
            raise CS2AssetError("Normal format must be gl or dx")
        if not re.fullmatch(r"[a-zA-Z0-9_]+", self.surface):
            raise CS2AssetError(
                "Surface property must contain only letters, digits, or underscores"
            )
        if not -32 <= self.exposure <= 32:
            raise CS2AssetError("Exposure must be between -32 and 32 stops")


def plan_import(resolved: ResolvedAsset, options: ImportOptions) -> dict:
    resolved = adapt_resolved(resolved)
    return {
        "provider": "Poly Haven"
        if resolved.source.provider == "polyhaven"
        else resolved.source.provider,
        "source": asdict(resolved.source),
        "kind": resolved.asset.kind,
        "asset": resolved.asset.id,
        "name": resolved.asset.name,
        "type": resolved.asset.type,
        "resolution": resolved.resolution,
        "download_bytes": resolved.download_size,
        "input_bytes": resolved.total_size,
        "files": [asdict(f) for f in resolved.files],
        "choices": resolved.choices,
        "options": asdict(options),
        "warnings": list(resolved.warnings),
    }


def _variant(resolved, options):
    digest = hashlib.sha256(json.dumps(asdict(options), sort_keys=True).encode()).hexdigest()[:10]
    return f"{resolved.resolution}_{digest}"


def convert_asset(
    normalized: NormalizedAsset,
    content: Path,
    build: Path,
    variant: str,
    options: ImportOptions,
    blender: Path | None,
) -> dict:
    """Source-independent conversion: only normalized local inputs cross this boundary."""
    resolved = normalized.resolved
    namespace = resolved.namespace
    warnings, detail, resources, aliases = list(resolved.warnings), {}, [], {}
    name = resource_name(resolved.asset.name)
    payload = normalized.input
    if isinstance(payload, MaterialInput):
        maps = material_maps(normalized, build)
        resource_dir = f"materials/cs2asset/{namespace}/{variant}"
        material, notes = create_material(
            content,
            resource_dir,
            maps,
            normal_format=options.normal_format or payload.normal_format,
            constants=payload.constants,
            surface=options.surface,
            tiling=options.tiling,
            name=name,
        )
        warnings.extend(notes)
        resources.append(material.relative_to(content).as_posix())
        aliases[f"{resource_dir}/material.vmat"] = resources[-1]
    elif isinstance(payload, SkyInput):
        inputs = _limit_maps({"hdri": payload.path}, resolved, build / "resized")
        material, detail = create_sky(
            content,
            f"materials/skybox/cs2asset/{namespace}/{variant}",
            inputs["hdri"],
            yaw=options.yaw,
            exposure=options.exposure,
            name=name,
        )
        resources.append(material.relative_to(content).as_posix())
        aliases[f"materials/skybox/cs2asset/{namespace}/{variant}/sky.vmat"] = resources[-1]
    elif isinstance(payload, ModelInput):
        material_root = f"materials/cs2asset/{namespace}/{variant}"
        kwargs = {
            "scale": options.scale,
            "material_resource_root": material_root,
            "asset_name": name,
        }
        if payload.dependency_remap:
            kwargs["dependency_remap"] = payload.dependency_remap
        exported = export_model(payload.path, build / "model", blender, **kwargs)
        warnings.extend(exported.warnings)
        remaps = {}
        material_names = model_material_names(
            name, [material.source_name or material.name for material in exported.materials]
        )
        for index, material in enumerate(exported.materials):
            directory = material_root
            if len(exported.materials) > 1:
                directory += f"/m{index:03d}"
            maps = {("base_color" if k == "color" else k): v for k, v in material.maps.items()}
            maps = _limit_maps(maps, resolved, build / "resized" / f"m{index:03d}")
            constants = {
                ("base_color" if k == "color" else k): v for k, v in material.constants.items()
            }
            generated, notes = create_material(
                content,
                directory,
                maps,
                constants=constants,
                surface=options.surface,
                tiling=options.tiling,
                normal_format=options.normal_format or "gl",
                name=material_names[index],
            )
            remaps[material.name] = generated.relative_to(content).as_posix()
            aliases[f"{material_root}/m{index:03d}/material.vmat"] = remaps[material.name]
            warnings.extend(material.warnings + notes)
        directory = f"models/cs2asset/{namespace}/{variant}"
        target = content / directory
        target.mkdir(parents=True, exist_ok=True)
        lod_resources = {}
        for level, fbx in exported.lods.items():
            filename = "mesh.fbx" if level == 0 else f"mesh_lod{level}.fbx"
            shutil.copy2(fbx, target / filename)
            lod_resources[level] = f"{directory}/{filename}"
        model = write_modeldoc(
            target / f"{name}.vmdl",
            f"{directory}/mesh.fbx",
            remaps,
            collision=options.collision,
            surface_prop=options.surface,
            lod_resources=lod_resources,
        )
        resources.append(model.relative_to(content).as_posix())
        aliases[f"{directory}/model.vmdl"] = resources[-1]
        detail = {
            "dimensions_units": exported.dimensions_units,
            "mesh_count": exported.mesh_count,
            "triangle_count": exported.triangle_count,
            "collision": options.collision,
            "blender_version": exported.blender_version,
            "lod_levels": sorted(exported.lods),
        }
        dimensions = resolved.asset.metadata.get("dimensions")
        if dimensions and dimensions["unit"] == "mm" and len(dimensions["values"]) == 3:
            expected = [d / 25.4 * options.scale for d in dimensions["values"]]
            if any(
                abs(a - e) > max(0.1, e * 0.15)
                for a, e in zip(sorted(exported.dimensions_units), sorted(expected))
            ):
                warnings.append(
                    f"Model dimensions differ from source metadata: exported {exported.dimensions_units}, "
                    f"expected {expected} inches"
                )
    else:
        raise CS2AssetError("Unsupported normalized asset input")
    return {
        "resources": resources,
        "warnings": warnings,
        "detail": detail,
        "content_hashes": {p: file_hash(f) for p, f in _file_set(content).items()},
        "input_sha256": normalized.input_sha256,
        "snapshot": normalized.snapshot,
        "alias_candidates": aliases,
    }


def material_maps(normalized, build):
    """Resolve known channel layouts and resolution consistently for both material paths."""
    payload = normalized.input
    maps = dict(payload.maps)
    for packed_role, layout in payload.packed_layouts.items():
        packed = read_image(maps[packed_role])
        if packed.shape[2] < len(layout):
            raise CS2AssetError("ARM map requires three channels (AO, roughness, metalness)")
        for index, role in enumerate(layout):
            if role not in maps:
                maps[role] = write_image(
                    build / "unpacked" / f"{role}.png", packed[:, :, index : index + 1]
                )
    return _limit_maps(maps, normalized.resolved, build / "resized")


def _limit_maps(maps, resolved, destination):
    from .images import resize_image

    limit = resolved.choices.get("max_dimension")
    if not limit:
        return maps
    return {
        key: resize_image(
            path,
            destination / (key + (".exr" if path.suffix.lower() in {".hdr", ".exr"} else ".png")),
            limit,
        )
        for key, path in maps.items()
    }


def _file_set(root: Path) -> dict[str, Path]:
    return {
        p.relative_to(root).as_posix(): p
        for p in root.rglob("*")
        if p.is_file() and p.parts[len(root.parts)] in {"materials", "models"}
    }


def _tool_fingerprint(installation: Installation, blender: Path | None) -> dict:
    def identity(path):
        return {"path": str(path), "sha256": file_hash(path)} if path else None

    return {
        "cs2asset": __version__,
        "template": TEMPLATE_VERSION,
        "model_converter": MODEL_CONVERTER_VERSION,
        "compiler": identity(installation.compiler),
        "blender": identity(blender),
        "converter_sources": {
            name: file_hash(Path(__file__).with_name(name))
            for name in (
                "images.py",
                "materials.py",
                "models.py",
                "blender_worker.py",
                "pipeline.py",
                "resource_names.py",
            )
        },
    }


def _converted_valid(converted: dict, content: Path) -> bool:
    resources = converted.get("resources")
    hashes = converted.get("content_hashes")
    if (
        not isinstance(resources, list)
        or not resources
        or not isinstance(hashes, dict)
        or not hashes
    ):
        return False
    if any(resource not in hashes for resource in resources):
        return False
    for relative, digest in hashes.items():
        path = contained_path(content, relative)
        if Path(relative).parts[0] not in {"materials", "models"}:
            raise CS2AssetError("Invalid converted asset cache path")
        if not isinstance(digest, str) or len(digest) != 64:
            return False
        if not path.is_file() or file_hash(path) != digest:
            return False
    snapshots = converted.get("snapshots", [converted.get("snapshot", {})])
    for snapshot in snapshots:
        if not snapshot:
            continue
        root = Path(snapshot["root"])
        for item in snapshot["resolved"]["files"]:
            path = input_path(root, item["relative_path"])
            if not path.is_file() or file_hash(path) != item["sha256"]:
                return False
    return True


def _clear_owned_stage(content: Path, game: Path):
    for root in (content, game):
        if not (root / ".cs2asset-stage.json").is_file():
            raise CS2AssetError(f"Refusing to clear unowned stage: {root}")
        for name in ("materials", "models"):
            target = contained_path(root, name)
            if target.exists():
                # Containment was checked before deleting this tool-owned directory.
                shutil.rmtree(target)


def _stage(installation: Installation, fingerprint: str):
    name = "cs2asset_stage_" + fingerprint[:24]
    content = installation.content_dir / "csgo_addons" / name
    game = installation.game_dir / "csgo_addons" / name
    for folder, parent in (
        (content, installation.content_dir / "csgo_addons"),
        (game, installation.game_dir / "csgo_addons"),
    ):
        if folder.resolve().parent != parent.resolve():
            raise CS2AssetError("Staging addon redirects outside the CS2 installation")
        marker = folder / ".cs2asset-stage.json"
        if folder.exists() and not marker.is_file():
            raise CS2AssetError(f"Staging path is not owned by cs2asset: {folder}")
        if marker.is_file() and read_json(marker).get("fingerprint") != fingerprint:
            raise CS2AssetError(f"Staging ownership fingerprint does not match: {folder}")
        folder.mkdir(parents=True, exist_ok=True)
        atomic_json(marker, {"fingerprint": fingerprint})
    return content, game


def legacy_resource_paths(previous: dict) -> list[str]:
    """Retain previously installed generic resource names as compatibility copies."""
    return sorted(
        {
            output["path"]
            for output in previous.get("outputs", [])
            if output["root"] == "content"
            and Path(output["path"]).name
            in {"model.vmdl", "material.vmat", "sky.vmat", "blend.vmat"}
        }
        | set(previous.get("resource_aliases", {}))
    )


def previous_import(project: Project, import_id: str) -> dict:
    installer = Installer(project)
    if not installer.manifest_path.exists() and not installer.journal_path.exists():
        return {}
    return installer.imports().get(import_id, {})


def retain_resource_aliases(converted: dict, content: Path, legacy_paths: list[str]) -> dict:
    aliases = {
        old: new
        for old, new in converted.get("alias_candidates", {}).items()
        if old in legacy_paths and old != new
    }
    for old, new in aliases.items():
        target = contained_path(content, old)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(contained_path(content, new), target)
    converted["resource_aliases"] = aliases
    converted["content_hashes"] = {p: file_hash(f) for p, f in _file_set(content).items()}
    return converted


def import_asset(
    installation: Installation,
    project: Project,
    provider,
    cache: Path,
    asset: str,
    options: ImportOptions,
    *,
    blender: Path | None = None,
    dry_run: bool = False,
    refresh: bool = False,
    overwrite: bool = False,
    force: bool = False,
    phase=None,
    download_progress=None,
    compiler_progress=None,
    asset_type: str | None = None,
) -> dict:
    options.validate()
    announce = phase or (lambda message: None)
    announce("Resolving asset source")
    source = source_provider(
        provider, asset, cache, downloader=DownloadCache, progress=download_progress
    )
    resolved = source.resolve(asset, options.resolution, refresh=refresh, asset_type=asset_type)
    effective = ImportOptions(**{**asdict(options), "resolution": resolved.resolution})
    description = plan_import(resolved, effective)
    variant = _variant(resolved, effective)
    if dry_run:
        return {
            **description,
            "project": project.name,
            "dry_run": True,
            "variant": variant,
            "namespace": resolved.namespace,
        }
    announce(
        f"{description['provider']}: {resolved.asset.name} ({resolved.resolution}); "
        f"{resolved.total_size / 1048576:.1f} MiB selected"
    )
    if resolved.asset.type == "models":
        blender = discover_blender(blender)
        if blender is None:
            raise CS2AssetError("Blender is required for models. Install Blender or set --blender.")
    resolved = source.prepare(resolved, blender=blender)
    import_id = f"{resolved.identity}:{variant}"
    previous = previous_import(project, import_id)
    legacy_paths = legacy_resource_paths(previous)
    description = plan_import(resolved, effective)
    tools = _tool_fingerprint(installation, blender if resolved.asset.type == "models" else None)
    identity = {
        "source": asdict(resolved.source),
        "namespace": resolved.namespace,
        "resource_name": resource_name(resolved.asset.name),
        "asset": resolved.asset.id,
        "type": resolved.asset.type,
        "files": [asdict(f) for f in resolved.files],
        "choices": resolved.choices,
        "options": asdict(effective),
        "tools": tools,
        "legacy_paths": legacy_paths,
    }
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    # Variant identity depends on user settings, so a tool update rebuilds in place.
    build = cache / "builds" / fingerprint
    build.mkdir(parents=True, exist_ok=True)
    stage_parent = installation.content_dir / "csgo_addons"
    stage_parent.mkdir(parents=True, exist_ok=True)
    stage_lock = stage_parent / f".cs2asset_stage_{fingerprint[:24]}.lock"
    with FileLock(str(build / "build.lock"), timeout=600), FileLock(str(stage_lock), timeout=600):
        content, game = _stage(installation, fingerprint)
        ready_path = build / "converted.json"
        converted = read_json(ready_path)
        reusable = _converted_valid(converted, content)
        if not reusable:
            _clear_owned_stage(content, game)
            announce("Reading selected files and dependencies")
            normalized = source.materialize(resolved, build / "inputs")
            announce("Converting source assets")
            converted = convert_asset(normalized, content, build, variant, effective, blender)
            retain_resource_aliases(converted, content, legacy_paths)
            atomic_json(ready_path, converted)
        else:
            announce("Reusing verified converted sources")
        announce("Compiling with Valve resourcecompiler")
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
        announce(f"Installing into Hammer addon {project.name}")
        metadata = {
            **description,
            "fingerprint": fingerprint,
            "variant": variant,
            "record_version": 2,
            "source_url": resolved.asset.metadata.get("source_url"),
            "authors": resolved.asset.metadata.get("authors", {}),
            "dimensions": resolved.asset.metadata.get("dimensions"),
            "dimensions_mm": (resolved.asset.metadata.get("dimensions") or {}).get("values"),
            "snapshot": converted.get("snapshot", {}),
            "tools": tools,
            "input_sha256": converted.get("input_sha256", {}),
            "resources": converted["resources"],
            "warnings": [
                *converted["warnings"],
                *[f"Valve ({w['resource']}): {w['message']}" for w in compiler_warnings],
            ],
            "compiler_warnings": compiler_warnings,
            "resource_aliases": converted.get("resource_aliases", {}),
            "detail": converted["detail"],
            "logs": str(build / "logs"),
            "compiler_help": executable_version(installation.compiler, ("-help",)),
        }
        content_outputs = {
            relative: contained_path(content, relative) for relative in converted["content_hashes"]
        }
        return Installer(project).install(
            import_id, content_outputs, compiled, metadata, overwrite=overwrite
        )


def rebuild_asset(
    installation, project, provider, cache, asset, *, variant=None, cached_inputs=False, **kwargs
):
    import os

    records = Installer(project).imports()
    if asset in records:
        selected = [records[asset]]
    else:
        uri = canonical_source(asset)
        selected = [
            record
            for record in records.values()
            if record.get("type") != "blend"
            and os.path.normcase(record_source(record)["uri"]) == os.path.normcase(uri)
        ]
    selected = [r for r in selected if variant is None or r.get("variant") == variant]
    if not selected:
        raise CS2AssetError(
            f"No installed import found for {asset}" + (f" variant {variant}" if variant else "")
        )
    results = []
    for record in selected:
        if record.get("type") == "blend":
            from .blend import rebuild_blend

            results.append(
                rebuild_blend(
                    installation,
                    project,
                    provider,
                    cache,
                    record,
                    cached_inputs=cached_inputs,
                    **kwargs,
                )
            )
            continue
        source = record_source(record)
        chosen_provider = provider
        if cached_inputs and source["provider"] == "local":
            if not record.get("snapshot"):
                raise CS2AssetError("This import has no retained input snapshot")
            chosen_provider = SnapshotProvider(record["snapshot"])
        results.append(
            import_asset(
                installation,
                project,
                chosen_provider,
                cache,
                source["uri"],
                ImportOptions(**record["options"]),
                **kwargs,
            )
        )
    return results
