"""Headless Blender conversion and the locally verified CS2 ModelDoc contract."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from cs2asset.compiler import run_process
from cs2asset.errors import CS2AssetError

MODELDOC_HEADER = (
    "<!-- kv3 encoding:text:version{e21c7f3c-8a33-41c5-9977-a76d3a32aa0d} "
    "format:modeldoc36:version{972dada4-b828-45a4-bb93-7795cf0585da} -->"
)
MODEL_CONVERTER_VERSION = 1


@dataclass
class ModelMaterial:
    name: str
    maps: dict[str, Path]
    constants: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    source_name: str = ""


@dataclass
class ModelExport:
    fbx: Path
    materials: list[ModelMaterial]
    dimensions_units: list[float]
    warnings: list[str]
    blender_version: str
    mesh_count: int
    triangle_count: int
    lods: dict[int, Path] = field(default_factory=dict)


def export_model(
    source: Path,
    output_dir: Path,
    blender: Path,
    *,
    scale: float = 1.0,
    timeout: float = 600,
) -> ModelExport:
    """Evaluate static geometry, export inch-sized Z-up FBX, and inspect materials.

    Blender runs with factory settings and auto-execution disabled. ``scale`` is
    a multiplier of the scene's physical dimensions, not an automatic resize.
    """
    source, output_dir, blender = Path(source), Path(output_dir), Path(blender)
    if not math.isfinite(scale) or scale <= 0:
        raise CS2AssetError("Model scale must be a finite positive number.")
    if not source.is_file():
        raise CS2AssetError(f"Model source does not exist: {source}")
    if source.suffix.lower() not in {".blend", ".fbx", ".gltf", ".glb", ".obj"}:
        raise CS2AssetError(f"Unsupported model format: {source.suffix}")
    if not blender.is_file():
        raise CS2AssetError(f"Blender executable not found: {blender}")
    output_dir.mkdir(parents=True, exist_ok=True)
    job = output_dir / "blender-job.json"
    result = output_dir / "blender-result.json"
    result.unlink(missing_ok=True)
    job.write_text(
        json.dumps(
            {
                "source": str(source.resolve()),
                "output_dir": str(output_dir.resolve()),
                "result": str(result.resolve()),
                "scale": scale,
            }
        ),
        encoding="utf-8",
    )
    command = [
        str(blender),
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python-exit-code",
        "1",
        "--python",
        str(Path(__file__).with_name("blender_worker.py")),
        "--",
        str(job.resolve()),
    ]
    log = output_dir / "blender.log"
    try:
        run_process(command, log, timeout=timeout)
    except CS2AssetError as exc:
        raise CS2AssetError(f"Blender conversion failed. {exc}") from exc
    try:
        data = json.loads(result.read_text(encoding="utf-8")) if result.is_file() else {}
    except (OSError, ValueError) as exc:
        raise CS2AssetError(f"Cannot read Blender's result report. Log: {log}") from exc
    if data.get("error"):
        raise CS2AssetError(
            f"Blender conversion failed: {data.get('error', 'see log')}. Log: {log}"
        )
    if not data or not Path(data.get("fbx", "")).is_file():
        raise CS2AssetError(f"Blender did not produce its model/report. Log: {log}")
    return ModelExport(
        fbx=Path(data["fbx"]),
        materials=[
            ModelMaterial(
                name=m["name"],
                source_name=m.get("source_name", ""),
                maps={k: Path(v) for k, v in m["maps"].items()},
                constants=m.get("constants", {}),
                warnings=m.get("warnings", []),
            )
            for m in data["materials"]
        ],
        dimensions_units=data["dimensions_units"],
        warnings=data["warnings"],
        blender_version=data["blender_version"],
        mesh_count=data["mesh_count"],
        triangle_count=data["triangle_count"],
        lods={int(k): Path(v) for k, v in data.get("lods", {}).items()},
    )


def _resource(value: str, suffix: str) -> str:
    value = value.replace("\\", "/")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or ":" in value or path.suffix.lower() != suffix:
        raise CS2AssetError(f"Expected addon-relative {suffix} resource path, got {value!r}")
    return path.as_posix()


def _kv3(value, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(value, dict):
        return (
            "{\n"
            + "\n".join(f"{pad}  {key} = {_kv3(item, indent + 1)}" for key, item in value.items())
            + f"\n{pad}}}"
        )
    if isinstance(value, list):
        return (
            "[\n" + "\n".join(f"{pad}  {_kv3(item, indent + 1)}," for item in value) + f"\n{pad}]"
        )
    return json.dumps(value, ensure_ascii=True)


def write_modeldoc(
    destination: Path,
    fbx_resource: str,
    material_remaps: dict[str, str],
    *,
    collision: str = "hull",
    surface_prop: str = "default",
    lod_resources: dict[int, str] | None = None,
) -> Path:
    """Write ModelDoc 36. A hull is a simple convex approximation, not concavity."""
    if collision not in {"hull", "none"}:
        raise CS2AssetError("Model collision must be 'hull' or 'none'.")
    fbx_resource = _resource(fbx_resource, ".fbx")
    remaps = []
    for name, material in material_remaps.items():
        material = _resource(material, ".vmat")
        # FBX's material importer appends .vmat. Both spellings cover the two
        # forms emitted by supported Blender/Valve versions.
        for key in dict.fromkeys((name, name if name.endswith(".vmat") else name + ".vmat")):
            remaps.append({"from": key, "to": material})
    children = [
        {
            "_class": "MaterialGroupList",
            "children": [
                {
                    "_class": "DefaultMaterialGroup",
                    "remaps": remaps,
                    "use_global_default": False,
                    "global_default_material": "",
                }
            ],
        },
        {
            "_class": "RenderMeshList",
            "children": [
                {
                    "_class": "RenderMeshFile",
                    "name": "mesh",
                    "filename": fbx_resource,
                    "import_scale": 1.0,
                    "import_filter": {"exclude_by_default": False, "exception_list": []},
                }
            ],
        },
    ]
    if lod_resources:
        resources = {0: fbx_resource, **lod_resources}
        if any(not isinstance(level, int) or level < 0 for level in resources):
            raise CS2AssetError("LOD levels must be non-negative integers.")
        children[1]["children"] = [
            {
                "_class": "RenderMeshFile",
                "name": f"lod{level}",
                "filename": _resource(resource, ".fbx"),
                "import_scale": 1.0,
                "import_filter": {"exclude_by_default": False, "exception_list": []},
            }
            for level, resource in sorted(resources.items())
        ]
        children.append(
            {
                "_class": "LODGroupList",
                "children": [
                    {
                        "_class": "LODGroup",
                        "name": f"lod{level}",
                        "switch_threshold": float(index * 100),
                        "meshes": [f"lod{level}"],
                    }
                    for index, level in enumerate(sorted(resources))
                ],
            }
        )
    if collision == "hull":
        children.append(
            {
                "_class": "PhysicsShapeList",
                "children": [
                    {
                        "_class": "PhysicsHullFromRender",
                        "parent_bone": "",
                        "surface_prop": surface_prop,
                        "collision_prop": "default",
                        "tool_material": "",
                        "faceMergeAngle": 20.0,
                        "maxHullVertices": 32,
                        "optimization_algorithm": "IFR",
                        "renderMeshList": ["lod0"] if lod_resources else [],
                    }
                ],
                "leave_body_collision_unmodified": False,
            }
        )
    root = {
        "rootNode": {
            "_class": "RootNode",
            "children": children,
            "model_archetype": "static_prop",
            "primary_associated_entity": "prop_static",
            "anim_graph_name": "",
            "document_sub_type": "ModelDocSubType_None",
        }
    }
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(MODELDOC_HEADER + "\n" + _kv3(root) + "\n", encoding="utf-8")
    return destination
