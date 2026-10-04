"""Bundled standalone worker; executed by Blender's Python, never regular Python."""

from __future__ import annotations

import json
import math
import os
import re
import sys
import traceback
from pathlib import Path

import bpy
import numpy as np
from mathutils import Euler, Matrix, Vector


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
    if node.type in {"SEPRGB", "SEPARATE_COLOR"}:
        if getattr(node, "mode", "RGB") != "RGB" or normal:
            raise RuntimeError("Only RGB channel separation for scalar PBR maps is supported")
        source = source_image(node.inputs[0])
        if source is None or source[1] != "Color":
            raise RuntimeError("Channel separation must read a conventional image Color output")
        return source[0], {"R": "Red", "G": "Green", "B": "Blue"}.get(
            link.from_socket.name, link.from_socket.name
        )
    if normal and node.type == "NORMAL_MAP":
        # Blender's FBX importer creates a disabled normal node even without a map.
        if (
            not node.inputs["Strength"].is_linked
            and abs(node.inputs["Strength"].default_value) < 1e-6
        ):
            return None
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


UV_NAMES = set()


def validate_uv(socket):
    """Return the affine UV mapping represented by a coordinate chain."""
    if not socket.is_linked:
        return Matrix.Identity(4)
    link = socket.links[0]
    node = link.from_node
    if node.type == "REROUTE":
        return validate_uv(node.inputs[0])
    if node.type == "TEX_COORD" and link.from_socket.name == "UV":
        return Matrix.Identity(4)
    if node.type == "UVMAP":
        if node.uv_map:
            UV_NAMES.add(node.uv_map)
        return Matrix.Identity(4)
    if (
        node.type == "ATTRIBUTE"
        and node.attribute_type == "GEOMETRY"
        and link.from_socket.name == "Vector"
    ):
        UV_NAMES.add(node.attribute_name)
        return Matrix.Identity(4)
    if node.type == "MAPPING":
        if any(node.inputs[n].is_linked for n in ("Location", "Rotation", "Scale")):
            raise RuntimeError("Linked Mapping parameters require --bake-materials")
        location = Vector(node.inputs["Location"].default_value)
        rotation = Euler(node.inputs["Rotation"].default_value).to_matrix().to_4x4()
        scale = Matrix.Diagonal((*node.inputs["Scale"].default_value, 1))
        if node.vector_type == "POINT":
            transform = Matrix.Translation(location) @ rotation @ scale
        elif node.vector_type == "TEXTURE":
            try:
                transform = (Matrix.Translation(location) @ rotation @ scale).inverted()
            except ValueError as exc:
                raise RuntimeError("Singular Mapping requires --bake-materials") from exc
        elif node.vector_type == "VECTOR":
            transform = rotation @ scale
        else:
            raise RuntimeError("Normal coordinate Mapping requires --bake-materials")
        return transform @ validate_uv(node.inputs["Vector"])
    raise RuntimeError(f"Texture coordinates from {node.name!r} require --bake-materials")


def material_mapping(material):
    transforms = []
    if not material.use_nodes:
        return Matrix.Identity(4)
    # Inspect only nodes feeding supported Principled inputs.
    outputs = [
        n for n in material.node_tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output
    ]
    if not outputs or not outputs[0].inputs["Surface"].is_linked:
        return Matrix.Identity(4)
    shader = outputs[0].inputs["Surface"].links[0].from_node
    visited = set()

    def visit(node):
        if node.as_pointer() in visited:
            return
        visited.add(node.as_pointer())
        if node.type == "TEX_IMAGE":
            transforms.append(validate_uv(node.inputs["Vector"]))
        else:
            for socket in node.inputs:
                for link in socket.links:
                    visit(link.from_node)

    for name in ("Base Color", "Roughness", "Metallic", "Alpha", "Normal"):
        socket = shader.inputs.get(name)
        if socket:
            for link in socket.links:
                visit(link.from_node)
    transform = transforms[0] if transforms else Matrix.Identity(4)
    if any(
        any(abs(t[r][c] - transform[r][c]) > 1e-6 for r in range(4) for c in range(4))
        for t in transforms
    ):
        raise RuntimeError(
            f"Material {material.name!r} uses conflicting texture mappings; use --bake-materials"
        )
    # Changing the tangent basis would change a normal map's interpretation.
    normal = shader.inputs.get("Normal")
    if (
        normal
        and normal.is_linked
        and (
            abs(transform[0][1]) > 1e-6
            or abs(transform[1][0]) > 1e-6
            or transform[0][0] <= 0
            or transform[1][1] <= 0
        )
    ):
        raise RuntimeError("Rotated/reflected UVs with normal maps require --bake-materials")
    return transform


def bake_material(material, objects, output, index, resolution):
    """Bake supported Principled channels onto the original active UV layout."""
    if not material.use_nodes:
        return
    nodes, links = material.node_tree.nodes, material.node_tree.links
    outputs = [n for n in nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output]
    if not outputs or not outputs[0].inputs["Surface"].is_linked:
        raise RuntimeError(f"Material {material.name!r} has no surface shader")
    surface = outputs[0].inputs["Surface"]
    shader = surface.links[0].from_node
    if shader.type != "BSDF_PRINCIPLED":
        found, visited = set(), set()

        def visit(node):
            if node.as_pointer() in visited:
                return
            visited.add(node.as_pointer())
            if node.type == "BSDF_PRINCIPLED":
                found.add(node)
            for socket in node.inputs:
                if socket.type == "SHADER":
                    for link in socket.links:
                        visit(link.from_node)

        visit(shader)
        if len(found) != 1:
            raise RuntimeError("Baking requires exactly one connected Principled BSDF")
        shader = next(iter(found))
        # Export the PBR component; Source's cutout material cannot reproduce
        # Blender's translucent/additive shader lobes.
        links.new(shader.outputs[0], surface)
        print(
            f"Material {material.name}: baking Principled component; additional shader lobes omitted"
        )
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 1
    scene.render.bake.margin = 8
    baked = []
    for semantic, name in (
        ("base_color", "Base Color"),
        ("roughness", "Roughness"),
        ("metalness", "Metallic"),
        ("opacity", "Alpha"),
        ("normal", "Normal"),
    ):
        socket = shader.inputs[name]
        if not socket.is_linked:
            continue
        image = bpy.data.images.new(f"bake_{index}_{semantic}", width=resolution, height=resolution)
        image.colorspace_settings.name = "sRGB" if semantic == "base_color" else "Non-Color"
        target = nodes.new("ShaderNodeTexImage")
        target.image = image
        nodes.active = target
        emission = None
        if semantic != "normal":
            emission = nodes.new("ShaderNodeEmission")
            links.new(socket.links[0].from_socket, emission.inputs["Color"])
            links.new(emission.outputs[0], surface)
        # Blender bakes every material slot of a selected object. Give the other
        # materials disposable targets so it never writes into source images.
        temporary_targets = []
        other_materials = {
            m for obj in objects for m in obj.data.materials if m and m != material and m.use_nodes
        }
        for other in other_materials:
            old_active = other.node_tree.nodes.active
            sink = bpy.data.images.new("cs2asset_bake_sink", width=resolution, height=resolution)
            sink_node = other.node_tree.nodes.new("ShaderNodeTexImage")
            sink_node.image = sink
            other.node_tree.nodes.active = sink_node
            temporary_targets.append((other, sink_node, sink, old_active))
        visibility = {o: o.hide_render for o in objects}
        first = True
        try:
            for obj in objects:
                if material not in list(obj.data.materials):
                    continue
                if not obj.data.uv_layers:
                    raise RuntimeError(f"Mesh {obj.name!r} has no UVs for baking")
                for other in objects:
                    other.hide_render = other != obj
                bpy.ops.object.select_all(action="DESELECT")
                obj.select_set(True)
                bpy.context.view_layer.objects.active = obj
                bpy.ops.object.bake(
                    type="NORMAL" if semantic == "normal" else "EMIT",
                    use_clear=first,
                    use_selected_to_active=False,
                )
                first = False
        finally:
            for obj, hidden in visibility.items():
                obj.hide_render = hidden
            for other, sink_node, sink, old_active in temporary_targets:
                other.node_tree.nodes.remove(sink_node)
                other.node_tree.nodes.active = old_active
                bpy.data.images.remove(sink)
            if emission:
                links.new(shader.outputs[0], surface)
                nodes.remove(emission)
        path = output / "baked" / f"m{index:03d}_{semantic}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        image.filepath_raw, image.file_format = str(path.resolve()), "PNG"
        image.save()
        baked.append((socket, target, semantic))
    for socket, target, semantic in baked:
        if semantic == "normal":
            normal = nodes.new("ShaderNodeNormalMap")
            links.new(target.outputs["Color"], normal.inputs["Color"])
            links.new(normal.outputs[0], socket)
        else:
            links.new(target.outputs["Color"], socket)


def list_scene():
    scene = bpy.context.scene

    def renderable(obj):
        return (
            obj.type in {"MESH", "CURVE", "SURFACE", "FONT", "META", "EMPTY"}
            and not obj.hide_render
        )

    return {
        "objects": [
            {
                "name": o.name,
                "type": o.type,
                "renderable": renderable(o),
                "hidden": o.hide_render,
                "materials": [s.material.name for s in o.material_slots if s.material],
                "collections": [c.name for c in o.users_collection],
                "lod": re.search(r"LOD[_ .-]?(\d+)", o.name, re.IGNORECASE)[1]
                if re.search(r"LOD[_ .-]?(\d+)", o.name, re.IGNORECASE)
                else None,
            }
            for o in scene.objects
        ],
        "collections": [
            {
                "name": c.name,
                "objects": [o.name for o in c.all_objects],
                "renderable": any(renderable(o) for o in c.all_objects),
                "split_group": c in list(scene.collection.children),
            }
            for c in bpy.data.collections
            if any(o.name in scene.objects for o in c.all_objects)
        ],
    }


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


def scalar_path(image, output: Path, index: int, channel: str) -> str:
    """Extract explicitly connected channels, such as glTF roughness G/metalness B."""
    if channel == "Alpha":
        return alpha_path(image, output, index)
    width, height = image.size
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    channel_index = {"Red": 0, "Green": 1, "Blue": 2}[channel]
    values = pixels.reshape(-1, 4)[:, channel_index]
    rgba = np.ones((width * height, 4), dtype=np.float32)
    rgba[:, :3] = values[:, None]
    extracted = bpy.data.images.new(f"scalar_{index}", width=width, height=height, alpha=False)
    extracted.colorspace_settings.name = "Non-Color"
    extracted.pixels.foreach_set(rgba.ravel())
    target = output / "embedded" / f"{channel.lower()}_{index:03d}.png"
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
            if channel in {"Red", "Green", "Blue"} and semantic not in {
                "roughness",
                "metalness",
                "opacity",
            }:
                raise RuntimeError(
                    f"Material {source_name!r}: channel extraction for {semantic} requires baking"
                )
            image_index = index * 10 + len(result["maps"])
            result["maps"][semantic] = (
                scalar_path(image, output, image_index, channel)
                if channel in {"Red", "Green", "Blue", "Alpha"}
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


def load_source(job: dict):
    source = Path(job["source"])
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
    if job.get("operation") == "inspect" and bpy.data.libraries:
        raise RuntimeError(
            "Linked Blender libraries are unsupported for local snapshots; make linked data local first"
        )
    remap = job.get("dependency_remap", {})
    for image in bpy.data.images:
        if image.packed_file or image.source != "FILE":
            continue
        original = os.path.normcase(
            str(Path(bpy.path.abspath(image.filepath, library=image.library)).resolve())
        )
        if original in remap:
            image.filepath = remap[original]
            image.reload()


def inspect_dependencies(job: dict) -> dict:
    load_source(job)
    dependencies = set()
    for image in bpy.data.images:
        if image.packed_file or image.source != "FILE" or not image.users:
            continue
        path = Path(bpy.path.abspath(image.filepath, library=image.library)).resolve()
        if not path.is_file():
            raise RuntimeError(
                f"Model {job['source']} references missing image {image.name!r}: {path}"
            )
        dependencies.add(str(path))
    return {"dependencies": sorted(dependencies)}


def main(job: dict) -> dict:
    if job.get("operation") == "inspect":
        return inspect_dependencies(job)
    load_source(job)
    if job.get("operation") == "list":
        return list_scene()
    output = Path(job["output_dir"])
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
    selected = None
    if job.get("object_name"):
        obj = scene.objects.get(job["object_name"])
        if obj is None:
            raise RuntimeError(f"Unknown object: {job['object_name']}")
        selected = {obj, *obj.children_recursive}
        pattern = r"(?:^|[_ .-])LOD[_ .-]?(\d+)(?:$|[_. -])"
        match = re.search(pattern, obj.name, re.IGNORECASE)
        if match:
            stem = re.sub(pattern, "_LOD_", obj.name, flags=re.IGNORECASE)
            selected.update(
                o
                for o in scene.objects
                if re.sub(pattern, "_LOD_", o.name, flags=re.IGNORECASE) == stem
            )
    if job.get("collection"):
        collection = bpy.data.collections.get(job["collection"])
        if collection is None:
            raise RuntimeError(f"Unknown collection: {job['collection']}")
        selected = set(collection.all_objects)
    meshes = []
    source_matrices = []
    for instance in graph.object_instances:
        obj = instance.object
        if (
            selected is not None
            and obj.original not in selected
            and not (instance.parent and instance.parent.original in selected)
        ):
            continue
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
        source_matrices.append(instance.matrix_world.copy())
    if not meshes:
        raise RuntimeError("Source contains no visible render meshes")
    export_scene = bpy.data.scenes.new("cs2asset_export")
    bpy.context.window.scene = export_scene
    materials = []
    material_objects = []
    seen = set()
    coords = []
    triangle_count = 0
    levels = sorted({level for _, _, level in meshes if level is not None}) or [0]
    if levels[0] != 0:
        raise RuntimeError("Source contains named LODs but no LOD0 mesh")
    export_objects = []
    center = Vector((0, 0, 0))
    if job.get("origin") == "center":
        points = [v.co for _, mesh, level in meshes if level in {None, 0} for v in mesh.vertices]
        center = Vector(
            [(min(v[i] for v in points) + max(v[i] for v in points)) / 2 for i in range(3)]
        )
        for _, mesh, _ in meshes:
            mesh.transform(Matrix.Translation(-center))
            mesh.update()
    # Create all objects before baking so shared materials include every mesh.
    for index, (name, mesh, level) in enumerate(meshes):
        obj = bpy.data.objects.new(f"mesh_{index:03d}_{name}", mesh)
        export_scene.collection.objects.link(obj)
        export_objects.append((obj, level))
    all_materials = list(dict.fromkeys(m for _, mesh, _ in meshes for m in mesh.materials if m))
    if job.get("bake_materials"):
        # Restore source object coordinates for generated/object-coordinate graphs.
        # Geometry is returned to the export coordinate system after baking.
        for (obj, _), matrix in zip(export_objects, source_matrices):
            mesh = obj.data
            mesh.transform(
                (Matrix.Scale(factor, 4) @ matrix).inverted() @ Matrix.Translation(center)
            )
            if matrix.determinant() < 0:
                mesh.flip_normals()
            obj.matrix_world = matrix
        for _, mesh, _ in meshes:
            if not mesh.uv_layers:
                attribute = mesh.attributes.get("UVMap")
                if (
                    attribute
                    and attribute.domain == "CORNER"
                    and attribute.data_type == "FLOAT_VECTOR"
                ):
                    values = [d.vector.copy() for d in attribute.data]
                    mesh.attributes.remove(attribute)
                    layer = mesh.uv_layers.new(name="UVMap")
                    for loop, value in zip(layer.data, values):
                        loop.uv = value[:2]
        for index, material in enumerate(all_materials):
            bake_material(
                material,
                [o for o, _ in export_objects],
                output,
                index,
                job.get("bake_resolution", 2048),
            )
        for (obj, _), matrix in zip(export_objects, source_matrices):
            obj.data.transform(Matrix.Translation(-center) @ Matrix.Scale(factor, 4) @ matrix)
            if matrix.determinant() < 0:
                obj.data.flip_normals()
            obj.matrix_world = Matrix.Identity(4)
            obj.data.update()
        bpy.context.view_layer.update()
        warnings.append(
            "Material Principled channels baked to active UVs; additional shader lobes are omitted. Overlapping UVs must have compatible surface values."
        )
    mappings, coordinate_names = {}, {}
    for material in all_materials:
        UV_NAMES.clear()
        mappings[material] = material_mapping(material)
        coordinate_names[material] = set(UV_NAMES)
    for _, mesh, _ in meshes:
        names = set().union(*(coordinate_names.get(m, set()) for m in mesh.materials))
        if len(names) > 1:
            raise RuntimeError("Multiple named UV channels on one mesh require --bake-materials")
        if names:
            name = next(iter(names))
            layer = mesh.uv_layers.get(name)
            if layer is None:
                attribute = mesh.attributes.get(name)
                if (
                    attribute is None
                    or attribute.domain != "CORNER"
                    or attribute.data_type != "FLOAT_VECTOR"
                ):
                    raise RuntimeError(f"Mesh {mesh.name!r} has no UV attribute {name!r}")
                values = [d.vector.copy() for d in attribute.data]
                mesh.attributes.remove(attribute)
                layer = mesh.uv_layers.new(name=name)
                for loop, value in zip(layer.data, values):
                    loop.uv = value[:2]
            mesh.uv_layers.active = layer
            layer.active_render = True
    for _, mesh, _ in meshes:
        if mesh.uv_layers:
            uv = mesh.uv_layers.active.data
            for polygon in mesh.polygons:
                if polygon.material_index < len(mesh.materials):
                    transform = mappings.get(
                        mesh.materials[polygon.material_index], Matrix.Identity(4)
                    )
                    for loop in polygon.loop_indices:
                        coord = transform @ Vector((*uv[loop].uv, 0, 1))
                        uv[loop].uv = coord[:2]
    for index, (name, mesh, level) in enumerate(meshes):
        obj = export_objects[index][0]
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
                material_objects.append(material)
        if not mesh.uv_layers:
            if any(m["maps"] for m in materials):
                raise RuntimeError(f"Textured mesh {name!r} has no UV coordinates")
            warnings.append(f"Mesh {name!r} has no UV coordinates (constant material).")
        mesh.calc_loop_triangles()
        triangle_count += len(mesh.loop_triangles)
        if level in {None, 0}:
            coords.extend(Vector(c) for c in obj.bound_box)
    dimensions = [max(c[i] for c in coords) - min(c[i] for c in coords) for i in range(3)]
    if job.get("material_resource_root"):
        # The converter supplies resource destinations; source/provider resolution
        # is independent of the Blender material export.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from resource_names import model_material_names

        names = model_material_names(job["asset_name"], [m["source_name"] for m in materials])
        for index, (material, info, name) in enumerate(zip(material_objects, materials, names)):
            directory = job["material_resource_root"]
            if len(materials) > 1:
                directory += f"/m{index:03d}"
            requested = f"{directory}/{name}"
            material.name = requested
            info["name"] = material.name
            if material.name != requested:
                warnings.append(
                    "Blender shortened a material resource name; ModelDoc remapping is used."
                )
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
