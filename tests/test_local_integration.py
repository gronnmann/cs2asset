"""Real local-source imports; all writes use a dedicated validation addon."""

import os
import re
from pathlib import Path

import numpy as np
import pytest

from cs2asset.blend import create_blend_asset
from cs2asset.compiler import run_process
from cs2asset.discovery import Project, discover_blender, discover_cs2
from cs2asset.images import read_image, write_image
from cs2asset.pipeline import ImportOptions, import_asset, rebuild_asset
from cs2asset.sources import SourceRegistry

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("CS2ASSET_INTEGRATION") != "1",
        reason="Set CS2ASSET_INTEGRATION=1 for real local Blender/Valve imports",
    ),
]


@pytest.fixture(scope="module")
def local_tools(tmp_path_factory):
    root = tmp_path_factory.mktemp("generic-integration")
    installation = discover_cs2()
    blender = discover_blender()
    assert blender
    project = Project(
        "cs2asset_generic_validation",
        installation.content_dir / "csgo_addons/cs2asset_generic_validation",
        installation.game_dir / "csgo_addons/cs2asset_generic_validation",
    )
    write_image(root / "color.png", np.full((8, 8, 3), [0.8, 0.4, 0.2]))
    script = root / "create_models.py"
    script.write_text(
        f"""
import bpy
from pathlib import Path
root = Path({str(root)!r})
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=1)
obj = bpy.context.object
material = bpy.data.materials.new('ImageMaterial')
material.use_nodes = True
image = material.node_tree.nodes.new('ShaderNodeTexImage')
image.image = bpy.data.images.load(str(root / 'color.png'))
material.node_tree.links.new(image.outputs['Color'], material.node_tree.nodes.get('Principled BSDF').inputs['Base Color'])
obj.data.materials.append(material)
bpy.context.scene.unit_settings.system = 'METRIC'
bpy.ops.wm.save_as_mainfile(filepath=str(root / 'cube.blend'))
bpy.ops.export_scene.gltf(filepath=str(root / 'cube.glb'), export_format='GLB')
bpy.ops.export_scene.gltf(filepath=str(root / 'cube.gltf'), export_format='GLTF_SEPARATE')
bpy.ops.export_scene.fbx(filepath=str(root / 'cube.fbx'), use_selection=True, bake_anim=False, path_mode='COPY', embed_textures=True)
bpy.ops.wm.obj_export(filepath=str(root / 'cube.obj'), export_selected_objects=True, export_materials=True)
""",
        encoding="utf-8",
    )
    run_process(
        [
            str(blender),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "1",
            "--python",
            str(script),
        ],
        root / "generation.log",
    )
    return installation, project, blender, root


@pytest.mark.parametrize("extension", [".blend", ".glb", ".gltf", ".fbx", ".obj"])
def test_real_local_model_formats(local_tools, extension):
    installation, project, blender, root = local_tools
    with SourceRegistry(root / "cache", offline=True) as provider:
        first = import_asset(
            installation,
            project,
            provider,
            root / "cache",
            str(root / f"cube{extension}"),
            ImportOptions(),
            blender=blender,
        )
        assert first["source"]["provider"] == "local"
        assert first["detail"]["dimensions_units"] == pytest.approx([1 / 0.0254] * 3, rel=1e-4)
        assert first["detail"]["mesh_count"] == 1
        assert first["snapshot"]
        color_sources = list(project.content_dir.glob("materials/cs2asset/local/**/base_color.png"))
        assert color_sources
        assert any(
            np.allclose(read_image(p)[0, 0, :3], [0.8, 0.4, 0.2], atol=0.002) for p in color_sources
        )
        assert first["resources"][-1].endswith("/cube.vmdl")
        assert first["compiler_warnings"] == []
        compile_log = (
            root / "cache/builds" / first["fingerprint"] / "logs/compile-000.log"
        ).read_text()
        assert "GetFbxMaterialPath Failed" not in compile_log
        assert any(p["path"].endswith(".vmdl_c") for p in first["outputs"])
        second = rebuild_asset(
            installation, project, provider, root / "cache", first["id"], blender=blender
        )[0]
        assert first["id"] == second["id"]
        assert first["fingerprint"] == second["fingerprint"]
        info = run_process(
            [
                str(installation.compiler.with_name("resourceinfo.exe")),
                "-all",
                "-i",
                str(project.game_dir / (first["resources"][-1] + "_c")),
            ],
            root / (extension[1:] + "-model-info.log"),
        )
        assert "PHYS" in info
        assert "materials/cs2asset/local/" in info
        assert "/cube.vmat" in info
        assert "compile_warnings = 0" in info


@pytest.mark.parametrize("extension", [".hdr", ".exr"])
def test_real_local_hdr_and_exr(local_tools, extension):
    installation, project, _blender, root = local_tools
    source = write_image(root / f"sky{extension}", np.full((64, 128, 3), 4.0), hdr=True)
    with SourceRegistry(root / "cache", offline=True) as provider:
        record = import_asset(
            installation, project, provider, root / "cache", str(source), ImportOptions()
        )
        assert record["detail"]["maximum_radiance"] == pytest.approx(4)
        assert any(p["path"].endswith(".vtex_c") for p in record["outputs"])
        assert (
            rebuild_asset(installation, project, provider, root / "cache", record["id"])[0]["id"]
            == record["id"]
        )


def test_real_cached_model_reconversion_without_original_inputs(local_tools):
    installation, project, blender, root = local_tools
    source = root / "cube.blend"
    with SourceRegistry(root / "cache", offline=True) as provider:
        first = import_asset(
            installation,
            project,
            provider,
            root / "cache",
            str(source),
            ImportOptions(),
            blender=blender,
        )
        # These are generated sources in pytest's temporary directory.
        source.unlink()
        (root / "color.png").unlink()
        # Require real conversion from the snapshot instead of reusing its stage.
        ready = root / "cache/builds" / first["fingerprint"] / "converted.json"
        ready.write_text("{}")
        cached = rebuild_asset(
            installation,
            project,
            provider,
            root / "cache",
            first["id"],
            cached_inputs=True,
            blender=blender,
        )[0]
        assert cached["id"] == first["id"]
        assert cached["detail"]["dimensions_units"] == first["detail"]["dimensions_units"]
        assert cached["input_sha256"] == first["input_sha256"]


def test_real_gltf_packed_pbr_channels(local_tools):
    installation, project, blender, root = local_tools
    source = root / "packed.glb"
    script = root / "packed.py"
    script.write_text(
        f"""
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=1)
material = bpy.data.materials.new('PackedPBR')
material.use_nodes = True
bpy.context.object.data.materials.append(material)
shader = material.node_tree.nodes.get('Principled BSDF')
image = bpy.data.images.new('PBR', width=8, height=8, alpha=False, is_data=True)
image.pixels[:] = [1, 0.2, 0.8, 1] * 64
texture = material.node_tree.nodes.new('ShaderNodeTexImage')
texture.image = image
separate = material.node_tree.nodes.new('ShaderNodeSeparateColor')
separate.mode = 'RGB'
material.node_tree.links.new(texture.outputs['Color'], separate.inputs['Color'])
material.node_tree.links.new(separate.outputs['Green'], shader.inputs['Roughness'])
material.node_tree.links.new(separate.outputs['Blue'], shader.inputs['Metallic'])
bpy.ops.export_scene.gltf(filepath={str(source)!r}, export_format='GLB')
""",
        encoding="utf-8",
    )
    run_process(
        [
            str(blender),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "1",
            "--python",
            str(script),
        ],
        root / "packed-generation.log",
    )
    with SourceRegistry(root / "cache", offline=True) as provider:
        record = import_asset(
            installation,
            project,
            provider,
            root / "cache",
            str(source),
            ImportOptions(),
            blender=blender,
        )
    for semantic, value in [("roughness", 0.2), ("metalness", 0.8)]:
        output = next(
            item for item in record["outputs"] if item["path"].endswith(f"/{semantic}.png")
        )
        pixels = read_image(project.content_dir / output["path"])
        assert pixels[:, :, 0].mean() == pytest.approx(value, abs=0.01)


def test_real_local_material_and_environment_blend_packing(local_tools):
    installation, project, _blender, root = local_tools
    folders = []
    values = [(0.9, 0.2, 0.6, 0.1, 0.3), (0.1, 0.8, 0.9, 0.7, 0.6)]
    for index, (color, roughness, ao, metal, height) in enumerate(values, 1):
        folder = root / f"material{index}"
        for role, v in {
            "albedo": [color, 0.4, 0.2],
            "normal": [0.5, 0.5, 1],
            "roughness": [roughness],
            "ao": [ao],
            "metalness": [metal],
            "height": [height],
        }.items():
            write_image(folder / f"material{index}_{role}.png", np.broadcast_to(v, (8, 8, len(v))))
        folders.append(str(folder))
    with SourceRegistry(root / "cache", offline=True) as provider:
        ordinary = import_asset(
            installation, project, provider, root / "cache", folders[0], ImportOptions()
        )
        assert any(p["path"].endswith(".vmat_c") for p in ordinary["outputs"])
        record = create_blend_asset(
            installation,
            project,
            provider,
            root / "cache",
            *folders,
            "forest_rock",
            ImportOptions(),
        )
        compiled = project.game_dir / (record["resources"][0] + "_c")
        info = run_process(
            [str(installation.compiler.with_name("resourceinfo.exe")), "-all", "-i", str(compiled)],
            root / "blend-info.log",
        )
        assert 'm_shaderName = "csgo_environment_blend.vfx"' in info
        assert "VertexPaintUI2Layer = 1" in info
        assert "VertexPaintUIAlphaBlendFactor = 1" in info
        assert 'm_pSemantic = "VertexPaintBlendParams"' in info
        textures = dict(re.findall(r'm_name = "(g_t\w+)"\s+m_pValue = resource:"([^"]+)"', info))
        for index, (color, roughness, ao, metal, height) in enumerate(values, 1):
            for semantic, channel, expected in [
                ("Color", 0, color),
                ("Color", 3, ao),
                ("Normal", 2, roughness),
                ("Height", 0, height),
                ("Height", 3, metal),
            ]:
                name = f"g_t{semantic}{index}"
                source = project.game_dir / (textures[name] + "_c")
                destination = root / (name + ".tga")
                run_process(
                    [
                        str(installation.compiler.with_name("resourceinfo.exe")),
                        "-i",
                        str(source),
                        "-extract",
                        "tga",
                        destination.name,
                    ],
                    root / (name + "-extract.log"),
                    cwd=root,
                )
                pixels = read_image(root / (name + "_mip0.tga"))
                assert pixels[0, 0, channel] == pytest.approx(expected, abs=0.02)
        assert (
            rebuild_asset(installation, project, provider, root / "cache", record["id"])[0]["id"]
            == record["id"]
        )


def test_real_named_model_materials_preserve_distinct_bindings(local_tools):
    installation, project, blender, root = local_tools
    source = root / "lifebuoy_parts.blend"
    script = root / "create_multi_material.py"
    script.write_text(
        f"""
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=1)
for name, color in [('Rubber', (0.8, 0.1, 0.1, 1)), ('Rope', (0.1, 0.8, 0.1, 1))]:
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value = color
    bpy.context.object.data.materials.append(material)
for index, polygon in enumerate(bpy.context.object.data.polygons):
    polygon.material_index = index % 2
bpy.ops.wm.save_as_mainfile(filepath={str(source)!r})
""",
        encoding="utf-8",
    )
    run_process(
        [
            str(blender),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "1",
            "--python",
            str(script),
        ],
        root / "multi-generation.log",
    )
    with SourceRegistry(root / "cache", offline=True) as provider:
        result = import_asset(
            installation,
            project,
            provider,
            root / "cache",
            str(source),
            ImportOptions(),
            blender=blender,
        )
    assert result["compiler_warnings"] == []
    paths = [
        item["path"]
        for item in result["outputs"]
        if item["root"] == "content" and item["path"].endswith(".vmat")
    ]
    assert len(paths) == 2
    assert any(path.endswith("/lifebuoy_parts_rubber.vmat") for path in paths)
    assert any(path.endswith("/lifebuoy_parts_rope.vmat") for path in paths)
    info = run_process(
        [
            str(installation.compiler.with_name("resourceinfo.exe")),
            "-all",
            "-i",
            str(project.game_dir / (result["resources"][0] + "_c")),
        ],
        root / "multi-info.log",
    )
    assert all(path in info for path in paths)
    log = Path(result["logs"]) / "compile-000.log"
    assert "GetFbxMaterialPath Failed" not in log.read_text()
