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
from .cache import DownloadCache
from .compiler import compile_resources
from .config import atomic_json, read_json
from .discovery import Installation, Project, discover_blender, executable_version
from .errors import CS2AssetError
from .images import read_image, write_image
from .installer import Installer, contained_path, file_hash
from .materials import TEMPLATE_VERSION, create_material, create_sky
from .models import MODEL_CONVERTER_VERSION, export_model, write_modeldoc
from .provider import PolyHavenProvider, ResolvedAsset


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
    return {
        "provider": "Poly Haven",
        "asset": resolved.asset.id,
        "name": resolved.asset.name,
        "type": resolved.asset.type,
        "resolution": resolved.resolution,
        "download_bytes": resolved.total_size,
        "files": [asdict(f) for f in resolved.files],
        "choices": resolved.choices,
        "options": asdict(options),
        "warnings": list(resolved.warnings),
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
            for name in ("images.py", "materials.py", "models.py", "blender_worker.py")
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


def import_asset(
    installation: Installation,
    project: Project,
    provider: PolyHavenProvider,
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
) -> dict:
    options.validate()
    announce = phase or (lambda message: None)
    announce("Resolving Poly Haven asset")
    resolved = provider.resolve_files(asset, options.resolution, refresh=refresh)
    effective = ImportOptions(**{**asdict(options), "resolution": resolved.resolution})
    description = plan_import(resolved, effective)
    if dry_run:
        return {**description, "project": project.name, "dry_run": True}
    announce(
        f"Poly Haven: {resolved.asset.name} ({resolved.resolution}); "
        f"{resolved.total_size / 1048576:.1f} MiB selected"
    )
    if resolved.asset.type == "models":
        blender = discover_blender(blender)
        if blender is None:
            raise CS2AssetError("Blender is required for models. Install Blender or set --blender.")
    tools = _tool_fingerprint(installation, blender if resolved.asset.type == "models" else None)
    identity = {
        "asset": resolved.asset.id,
        "type": resolved.asset.type,
        "files": [asdict(f) for f in resolved.files],
        "choices": resolved.choices,
        "options": asdict(effective),
        "tools": tools,
    }
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    # Variant identity depends on user settings, so a tool update rebuilds in place.
    variant_hash = hashlib.sha256(
        json.dumps(asdict(effective), sort_keys=True).encode()
    ).hexdigest()[:10]
    variant = f"{resolved.resolution}_{variant_hash}"
    import_id = f"polyhaven:{resolved.asset.id}:{variant}"
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
            announce("Downloading selected files and dependencies")
            with DownloadCache(
                cache, progress=download_progress, offline=provider.offline
            ) as downloader:
                inputs = downloader.materialize(resolved.files, build / "inputs")
            input_hashes = {role: file_hash(path) for role, path in inputs.items()}
            announce("Converting source assets")
            warnings = list(resolved.warnings)
            detail = {}
            resources = []
            if resolved.asset.type == "textures":
                maps = dict(inputs)
                if "arm" in maps:
                    packed = read_image(maps["arm"])
                    if packed.shape[2] < 3:
                        raise CS2AssetError(
                            "ARM map requires three channels (AO, roughness, metalness)"
                        )
                    for index, role in enumerate(("ao", "roughness", "metalness")):
                        if role not in maps:
                            maps[role] = write_image(
                                build / "unpacked" / f"{role}.png", packed[:, :, index : index + 1]
                            )
                resource_dir = f"materials/cs2asset/polyhaven/{resolved.asset.id}/{variant}"
                normal = effective.normal_format or (
                    "dx" if resolved.choices.get("normal_convention") == "directx" else "gl"
                )
                material, notes = create_material(
                    content,
                    resource_dir,
                    maps,
                    normal_format=normal,
                    surface=effective.surface,
                    tiling=effective.tiling,
                )
                warnings.extend(notes)
                resources.append(material.relative_to(content).as_posix())
            elif resolved.asset.type == "hdris":
                resource_dir = f"materials/skybox/cs2asset/polyhaven/{resolved.asset.id}/{variant}"
                material, detail = create_sky(
                    content,
                    resource_dir,
                    inputs["hdri"],
                    yaw=effective.yaw,
                    exposure=effective.exposure,
                )
                resources.append(material.relative_to(content).as_posix())
            else:
                exported = export_model(
                    inputs["model"], build / "model", blender, scale=effective.scale
                )
                warnings.extend(exported.warnings)
                remaps = {}
                for index, material in enumerate(exported.materials):
                    directory = (
                        f"materials/cs2asset/polyhaven/{resolved.asset.id}/{variant}/m{index:03d}"
                    )
                    maps = {
                        ("base_color" if k == "color" else k): v for k, v in material.maps.items()
                    }
                    constants = {
                        ("base_color" if k == "color" else k): v
                        for k, v in material.constants.items()
                    }
                    generated, notes = create_material(
                        content,
                        directory,
                        maps,
                        constants=constants,
                        surface=effective.surface,
                        tiling=effective.tiling,
                        normal_format=effective.normal_format or "gl",
                    )
                    remaps[material.name] = generated.relative_to(content).as_posix()
                    warnings.extend(material.warnings + notes)
                directory = f"models/cs2asset/polyhaven/{resolved.asset.id}/{variant}"
                target = content / directory
                target.mkdir(parents=True, exist_ok=True)
                lod_resources = {}
                for level, fbx in exported.lods.items():
                    filename = "mesh.fbx" if level == 0 else f"mesh_lod{level}.fbx"
                    shutil.copy2(fbx, target / filename)
                    lod_resources[level] = f"{directory}/{filename}"
                model = write_modeldoc(
                    target / "model.vmdl",
                    f"{directory}/mesh.fbx",
                    remaps,
                    collision=effective.collision,
                    surface_prop=effective.surface,
                    lod_resources=lod_resources,
                )
                resources.append(model.relative_to(content).as_posix())
                detail = {
                    "dimensions_units": exported.dimensions_units,
                    "mesh_count": exported.mesh_count,
                    "triangle_count": exported.triangle_count,
                    "collision": effective.collision,
                    "blender_version": exported.blender_version,
                    "lod_levels": sorted(exported.lods),
                }
                if resolved.asset.dimensions and len(resolved.asset.dimensions) == 3:
                    expected = [d / 25.4 * effective.scale for d in resolved.asset.dimensions]
                    actual_sorted, expected_sorted = (
                        sorted(exported.dimensions_units),
                        sorted(expected),
                    )
                    if any(
                        abs(a - e) > max(0.1, e * 0.15)
                        for a, e in zip(actual_sorted, expected_sorted)
                    ):
                        warnings.append(
                            f"Model dimensions differ from Poly Haven metadata: "
                            f"exported {exported.dimensions_units}, expected {expected} inches"
                        )
            converted = {
                "resources": resources,
                "warnings": warnings,
                "detail": detail,
                "content_hashes": {p: file_hash(f) for p, f in _file_set(content).items()},
                "input_sha256": input_hashes,
            }
            atomic_json(ready_path, converted)
        else:
            announce("Reusing verified converted sources")
        announce("Compiling with Valve resourcecompiler")
        compiled = compile_resources(
            installation,
            content,
            game,
            [content / p for p in converted["resources"]],
            build / "logs",
            force=force,
            progress=compiler_progress,
        )
        announce(f"Installing into Hammer addon {project.name}")
        metadata = {
            **description,
            "fingerprint": fingerprint,
            "variant": variant,
            "source_url": resolved.asset.source_url,
            "authors": resolved.asset.authors,
            "dimensions_mm": resolved.asset.dimensions,
            "tools": tools,
            "input_sha256": converted.get("input_sha256", {}),
            "resources": converted["resources"],
            "warnings": converted["warnings"],
            "detail": converted["detail"],
            "logs": str(build / "logs"),
            "compiler_help": executable_version(installation.compiler, ("-help",)),
        }
        content_outputs = {
            relative: contained_path(content, relative)
            for relative in converted["content_hashes"]
        }
        return Installer(project).install(
            import_id, content_outputs, compiled, metadata, overwrite=overwrite
        )


def rebuild_asset(installation, project, provider, cache, asset, *, variant=None, **kwargs):
    from .provider import parse_asset_id

    asset_id = parse_asset_id(asset)
    records = [
        r
        for r in Installer(project).imports().values()
        if r.get("asset") == asset_id and (variant is None or r.get("variant") == variant)
    ]
    if not records:
        raise CS2AssetError(
            f"No installed import found for {asset_id}" + (f" variant {variant}" if variant else "")
        )
    return [
        import_asset(
            installation,
            project,
            provider,
            cache,
            asset_id,
            ImportOptions(**record["options"]),
            **kwargs,
        )
        for record in records
    ]
