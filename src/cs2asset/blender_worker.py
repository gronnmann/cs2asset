"""Bundled standalone worker; executed by Blender's Python, never regular Python."""

from __future__ import annotations

import json
import math
import re
import sys
import traceback
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector


def image_path(image, output: Path, index: int) -> str:
    path = Path(bpy.path.abspath(image.filepath, library=image.library))
    if image.packed_file or image.source == "GENERATED":
        # save_render respects float precision when using OpenEXR.
        suffix = ".exr" if image.is_float else ".png"
        path = output / "embedded" / f"image_{index:03d}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        old_path, old_format = image.filepath_raw, image.file_format
        image.filepath_raw, image.file_format = str(path), "OPEN_EXR" if image.is_float else "PNG"
        image.save()
        image.filepath_raw, image.file_format = old_path, old_format
    if not path.is_file():
        raise RuntimeError(f"Missing image dependency {image.name!r}: {path}")
    return str(path.resolve())


def source_image(socket, *, normal=False):
    if not socket.is_linked:
        return None
    link = socket.links[0]
    node = link.from_node
    if node.type == "REROUTE":
        return source_image(node.inputs[0], normal=normal)
    if normal and node.type == "NORMAL_MAP":
        if node.space != "TANGENT" or node.inputs["Strength"].is_linked:
            raise RuntimeError(
                "Only tangent-space normal maps with constant strength are supported"
            )
        if abs(node.inputs["Strength"].default_value - 1.0) > 0.0001:
            raise RuntimeError("Normal-map strength other than 1 requires baking")
        return source_image(node.inputs["Color"])
    if node.type == "TEX_IMAGE" and node.image:
        if node.projection != "FLAT" or node.extension not in {"REPEAT", "EXTEND"}:
            raise RuntimeError("Non-planar or clipped image projections require baking")
        validate_uv(node.inputs["Vector"])
        return node.image, link.from_socket.name
    raise RuntimeError(
        f"Unsupported procedural material node {node.name!r} ({node.type}); bake it to image PBR maps first"
    )


def validate_uv(socket):
    """Accept only coordinates represented by the exported mesh's active UVs."""
    if not socket.is_linked:
        return
    link = socket.links[0]
    node = link.from_node
    if node.type == "REROUTE":
        validate_uv(node.inputs[0])
        return
    if node.type == "TEX_COORD" and link.from_socket.name == "UV":
        return
    if node.type == "UVMAP" and not node.uv_map:
        return
    if node.type == "MAPPING":
        for name, expected in (
            ("Location", (0, 0, 0)),
            ("Rotation", (0, 0, 0)),
            ("Scale", (1, 1, 1)),
        ):
            value = node.inputs[name]
            if value.is_linked or any(
                abs(a - b) > 1e-6 for a, b in zip(value.default_value, expected, strict=True)
            ):
                raise RuntimeError("Non-identity texture Mapping requires baking before import")
        validate_uv(node.inputs["Vector"])
        return
    raise RuntimeError(
        f"Texture coordinates from {node.name!r} require baking to the active UV map"
    )


def alpha_path(image, output: Path, index: int) -> str:
    """Extract an image Alpha output without confusing it with its red channel."""
    width, height = image.size
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    alpha = pixels.reshape(-1, 4)[:, 3]
    rgba = np.ones((width * height, 4), dtype=np.float32)
    rgba[:, :3] = alpha[:, None]
    extracted = bpy.data.images.new(f"alpha_{index}", width=width, height=height, alpha=False)
    extracted.colorspace_settings.name = "Non-Color"
    extracted.pixels.foreach_set(rgba.ravel())
    target = output / "embedded" / f"alpha_{index:03d}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    extracted.filepath_raw, extracted.file_format = str(target), "PNG"
    extracted.save()
    bpy.data.images.remove(extracted)
    return str(target.resolve())


def material_info(material, index: int, output: Path) -> dict:
    source_name = material.name
    material.name = f"cs2asset_material_{index:03d}"
    result = {
        "name": material.name,
        "source_name": source_name,
        "maps": {},
        "constants": {},
        "warnings": [],
    }
    if not material.use_nodes:
        result["constants"] = {
            "base_color": list(material.diffuse_color),
            "roughness": material.roughness,
            "metalness": material.metallic,
            "opacity": material.diffuse_color[3],
        }
        return result
    outputs = [
        n for n in material.node_tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output
    ]
    if not outputs or not outputs[0].inputs["Surface"].is_linked:
        raise RuntimeError(f"Material {source_name!r} has no connected surface shader")
    shader = outputs[0].inputs["Surface"].links[0].from_node
    if shader.type != "BSDF_PRINCIPLED":
        raise RuntimeError(
            f"Material {source_name!r} uses {shader.type}; only image-based Principled BSDF is supported"
        )
    for name in (
        "Transmission Weight",
        "Coat Weight",
        "Subsurface Weight",
        "Sheen Weight",
        "Anisotropic IOR Level",
    ):
        socket = shader.inputs.get(name)
        if socket and (socket.is_linked or abs(socket.default_value) > 1e-6):
            result["warnings"].append(
                f"Principled {name} is not represented by the opaque/cutout CS2 material."
            )
    emission = shader.inputs.get("Emission Color")
    strength = shader.inputs.get("Emission Strength")
    if (
        emission
        and strength
        and (strength.is_linked or strength.default_value > 0)
        and (emission.is_linked or any(c > 0 for c in emission.default_value[:3]))
    ):
        result["warnings"].append("Emission is not exported by the standard PBR material pipeline.")
    for semantic, name in [
        ("base_color", "Base Color"),
        ("roughness", "Roughness"),
        ("metalness", "Metallic"),
        ("opacity", "Alpha"),
        ("normal", "Normal"),
    ]:
        socket = shader.inputs[name]
        try:
            source = source_image(socket, normal=semantic == "normal")
        except RuntimeError as exc:
            raise RuntimeError(f"Material {source_name!r}, {name}: {exc}") from exc
        if source:
            image, channel = source
            image_index = index * 10 + len(result["maps"])
            result["maps"][semantic] = (
                alpha_path(image, output, image_index)
                if channel == "Alpha"
                else image_path(image, output, image_index)
            )
        elif semantic != "normal":
            value = socket.default_value
            result["constants"][semantic] = (
                list(value) if semantic == "base_color" else float(value)
            )
    # A height-driven bump chain needs a bake. Ignoring it would alter shading.
    if outputs[0].inputs["Displacement"].is_linked:
        result["warnings"].append(
            "Material displacement is not exported; static render mesh modifiers are evaluated."
        )
    return result


def main(job: dict) -> dict:
    source, output = Path(job["source"]), Path(job["output_dir"])
    suffix = source.suffix.lower()
    if suffix == ".blend":
        bpy.ops.wm.open_mainfile(filepath=str(source), load_ui=False, use_scripts=False)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        if suffix == ".fbx":
            bpy.ops.import_scene.fbx(filepath=str(source), use_anim=False)
        elif suffix in {".gltf", ".glb"}:
            bpy.ops.import_scene.gltf(filepath=str(source))
        elif suffix == ".obj":
            bpy.ops.wm.obj_import(filepath=str(source))
        else:
            raise RuntimeError(f"Unsupported input format: {suffix}")
    scene = bpy.context.scene
    warnings = []
    if any(o.type == "ARMATURE" for o in scene.objects):
        warnings.append(
            "Rigged source evaluated at its current frame as a static model; animation is not exported."
        )
    # Explicit Blender metric units to Source inches. A NONE unit system uses
    # Blender's conventional meter-sized coordinates; report that assumption.
    if scene.unit_settings.system == "NONE":
        warnings.append(
            "Source has no unit metadata; treating one Blender unit as one meter. Use --scale to override."
        )
    factor = scene.unit_settings.scale_length * float(job["scale"]) / 0.0254
    if not math.isfinite(factor) or factor <= 0:
        raise RuntimeError("Invalid model unit scale")
    for obj in scene.objects:
        if not obj.hide_render:
            obj.hide_viewport = False
            obj.hide_set(False)
        for modifier in obj.modifiers:
            modifier.show_viewport = modifier.show_render
            if modifier.type == "SUBSURF":
                modifier.levels = modifier.render_levels
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    meshes = []
    for instance in graph.object_instances:
        obj = instance.object
        if obj.type not in {"MESH", "CURVE", "SURFACE", "FONT", "META"} or obj.hide_render:
            continue
        if not instance.show_self:
            continue
        mesh = bpy.data.meshes.new_from_object(obj, preserve_all_data_layers=True, depsgraph=graph)
        for index, slot in enumerate(obj.material_slots):
            if slot.material and index < len(mesh.materials):
                mesh.materials[index] = slot.material.original
        if not mesh.polygons:
            bpy.data.meshes.remove(mesh)
            continue
        matrix = Matrix.Scale(factor, 4) @ instance.matrix_world
        mesh.transform(matrix)
        if matrix.determinant() < 0:
            mesh.flip_normals()
        mesh.update()
        lod_match = re.search(r"(?:^|[_ .-])LOD[_ .-]?(\d+)(?:$|[_. -])", obj.name, re.IGNORECASE)
        if not lod_match:
            for collection in obj.original.users_collection:
                lod_match = re.search(
                    r"(?:^|[_ .-])LOD[_ .-]?(\d+)(?:$|[_. -])", collection.name, re.IGNORECASE
                )
                if lod_match:
                    break
        meshes.append((obj.name, mesh, int(lod_match[1]) if lod_match else None))
    if not meshes:
        raise RuntimeError("Source contains no visible render meshes")
    export_scene = bpy.data.scenes.new("cs2asset_export")
    bpy.context.window.scene = export_scene
    materials = []
    seen = set()
    coords = []
    triangle_count = 0
    levels = sorted({level for _, _, level in meshes if level is not None}) or [0]
    if levels[0] != 0:
        raise RuntimeError("Source contains named LODs but no LOD0 mesh")
    export_objects = []
    for index, (name, mesh, level) in enumerate(meshes):
        obj = bpy.data.objects.new(f"mesh_{index:03d}_{name}", mesh)
        export_scene.collection.objects.link(obj)
        export_objects.append((obj, level))
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        if not mesh.materials:
            material = bpy.data.materials.new("Default")
            material.use_nodes = True
            mesh.materials.append(material)
        for slot in obj.material_slots:
            if slot.material is None:
                slot.material = bpy.data.materials.new("Default")
                slot.material.use_nodes = True
            material = slot.material
            if material.as_pointer() not in seen:
                seen.add(material.as_pointer())
                materials.append(material_info(material, len(materials), output))
        if not mesh.uv_layers:
            if any(m["maps"] for m in materials):
                raise RuntimeError(f"Textured mesh {name!r} has no UV coordinates")
            warnings.append(f"Mesh {name!r} has no UV coordinates (constant material).")
        mesh.calc_loop_triangles()
        triangle_count += len(mesh.loop_triangles)
        if level in {None, 0}:
            coords.extend(Vector(c) for c in obj.bound_box)
    dimensions = [max(c[i] for c in coords) - min(c[i] for c in coords) for i in range(3)]
    export_scene.unit_settings.system = "NONE"
    export_scene.unit_settings.scale_length = 1.0
    fbx = output / "mesh.fbx"
    lods = {}
    for level in levels:
        path = fbx if level == 0 else output / f"mesh_lod{level}.fbx"
        lods[level] = str(path)
        for obj, obj_level in export_objects:
            obj.select_set(obj_level is None or obj_level == level)
        bpy.ops.export_scene.fbx(
            filepath=str(path),
            use_selection=True,
            object_types={"MESH"},
            global_scale=0.01,
            apply_unit_scale=False,
            apply_scale_options="FBX_SCALE_NONE",
            axis_forward="-X",
            axis_up="Z",
            use_space_transform=False,
            bake_space_transform=False,
            use_mesh_modifiers=False,
            mesh_smooth_type="OFF",
            use_tspace=True,
            add_leaf_bones=False,
            bake_anim=False,
            path_mode="STRIP",
            embed_textures=False,
        )
    if len(levels) > 1:
        warnings.append(
            "Supplied LOD meshes preserved; default ModelDoc switch thresholds are 0, 100, 200, ... units."
        )
    return {
        "fbx": str(fbx),
        "lods": lods,
        "materials": materials,
        "dimensions_units": dimensions,
        "warnings": warnings,
        "blender_version": bpy.app.version_string,
        "mesh_count": len(meshes),
        "triangle_count": triangle_count,
    }


if __name__ == "__main__":
    job = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    try:
        report = main(job)
    except Exception as exc:
        Path(job["result"]).write_text(json.dumps({"error": str(exc)}), encoding="utf-8")
        traceback.print_exc()
        raise
    Path(job["result"]).write_text(json.dumps(report, indent=2), encoding="utf-8")
