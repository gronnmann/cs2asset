import os
import re
import subprocess
from uuid import uuid4

import pytest

from cs2asset.errors import CS2AssetError
from cs2asset.models import export_model, write_modeldoc


@pytest.mark.parametrize(
    "resource", ["../outside.fbx", "C:/outside.fbx", "/outside.fbx", "mesh.obj"]
)
def test_model_references_cannot_escape_addon(tmp_path, resource):
    with pytest.raises(CS2AssetError, match="addon-relative"):
        write_modeldoc(tmp_path / "model.vmdl", resource, {})


def test_material_references_cannot_escape_addon(tmp_path):
    with pytest.raises(CS2AssetError, match="addon-relative"):
        write_modeldoc(tmp_path / "model.vmdl", "models/mesh.fbx", {"mat": "../secret.vmat"})


@pytest.mark.parametrize("scale", [0, -1, float("nan"), float("inf")])
def test_scale_rejected_before_starting_blender(tmp_path, scale):
    with pytest.raises(CS2AssetError, match="finite positive"):
        export_model(tmp_path / "source.blend", tmp_path, tmp_path / "blender", scale=scale)


def test_unsupported_collision_is_not_silently_ignored(tmp_path):
    with pytest.raises(CS2AssetError, match="collision"):
        write_modeldoc(tmp_path / "model.vmdl", "models/mesh.fbx", {}, collision="concave")


@pytest.mark.parametrize("lods", [None, {}, {0: "models/mesh.fbx"}])
def test_single_mesh_omits_lod_groups_and_uses_it_for_collision(tmp_path, lods):
    model = write_modeldoc(tmp_path / "rock.vmdl", "models/mesh.fbx", {}, lod_resources=lods)
    text = model.read_text()
    assert "LODGroupList" not in text
    assert 'name = "mesh"' in text
    assert "PhysicsHullFromRender" in text
    assert '"lod0"' not in text


def test_implicit_base_lod_and_one_lower_lod_keep_groups(tmp_path):
    model = write_modeldoc(
        tmp_path / "rock.vmdl", "models/mesh.fbx", {}, lod_resources={1: "models/lod1.fbx"}
    )
    text = model.read_text()
    assert "LODGroupList" in text
    assert '"lod0"' in text and '"lod1"' in text


def test_single_lod_resource_is_validated_and_used(tmp_path):
    with pytest.raises(CS2AssetError, match="addon-relative"):
        write_modeldoc(
            tmp_path / "rock.vmdl", "models/base.fbx", {}, lod_resources={0: "../bad.fbx"}
        )
    result = write_modeldoc(
        tmp_path / "rock.vmdl", "models/base.fbx", {}, lod_resources={0: "models/actual.fbx"}
    )
    assert 'filename = "models/actual.fbx"' in result.read_text()


def test_modeldoc_escapes_source_names_and_preserves_lods(tmp_path):
    model = write_modeldoc(
        tmp_path / "model.vmdl",
        "models/mesh.fbx",
        {'material"name': "materials/a.vmat"},
        lod_resources={0: "models/mesh.fbx", 1: "models/lod1.fbx"},
    )
    text = model.read_text()
    assert 'from = "material\\"name.vmat"' in text
    assert text.count('_class = "RenderMeshFile"') == 2
    assert re.search(r'renderMeshList = \[\s+"lod0",', text)


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("CS2ASSET_INTEGRATION") != "1",
    reason="Set CS2ASSET_INTEGRATION=1 for real Blender/Valve tests",
)
def test_real_blender_valve_units_materials_and_collision(tmp_path):
    """Valve's own reader checks compiled dimensions and collision, not a mock."""
    from cs2asset.discovery import discover_blender, discover_cs2
    from cs2asset.materials import create_material

    installation = discover_cs2()
    blender = discover_blender()
    assert blender, "Blender is required for the opted-in model integration test"
    source = tmp_path / "two_material.blend"
    script = tmp_path / "create.py"
    script.write_text(
        f"""
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=1, location=(0.5, 1, 1.5))
obj = bpy.context.object
obj.scale = (1, 2, 3)
for name, color in [('Red', (1, 0, 0, 1)), ('Blue', (0, 0, 1, 1))]:
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value = color
    obj.data.materials.append(material)
for face in obj.data.polygons:
    face.material_index = face.index % 2
bpy.context.scene.unit_settings.system = 'METRIC'
bpy.ops.wm.save_as_mainfile(filepath={str(source)!r})
""",
        encoding="utf-8",
    )
    subprocess.run(
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
        check=True,
        capture_output=True,
        timeout=120,
    )
    # Only the explicitly tool-owned test addon is touched.
    addon = "cs2asset_model_probe"
    content = installation.content_dir / "csgo_addons" / addon
    game = installation.game_dir / "csgo_addons" / addon
    game.mkdir(parents=True, exist_ok=True)
    relative = f"models/cs2asset/tests/{uuid4().hex}"
    exported = export_model(source, content / relative, blender)
    assert exported.dimensions_units == pytest.approx(
        [1 / 0.0254, 2 / 0.0254, 3 / 0.0254], rel=1e-6
    )
    assert len(exported.materials) == 2
    remaps = {}
    for material in exported.materials:
        path, _ = create_material(
            content,
            f"materials/cs2asset/tests/{material.name}",
            material.maps,
            constants=material.constants,
        )
        remaps[material.name] = path.relative_to(content).as_posix()
    for collision in ("hull", "none"):
        model = write_modeldoc(
            content / relative / f"{collision}.vmdl",
            f"{relative}/mesh.fbx",
            remaps,
            collision=collision,
        )
        compiler = subprocess.run(
            [
                str(installation.compiler),
                "-nop4",
                "-game",
                str(installation.game_context),
                "-i",
                str(model),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=120,
        )
        assert compiler.returncode == 0, compiler.stdout + compiler.stderr
        compiled = game / relative / f"{collision}.vmdl_c"
        assert compiled.is_file()
        info = subprocess.run(
            [str(installation.compiler.with_name("resourceinfo.exe")), "-i", str(compiled), "-all"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout
        bounds = re.search(r"m_vMaxBounds = \[ ([^\]]+) \]", info)
        assert bounds, info
        assert [float(v) for v in bounds[1].split(",")] == pytest.approx(
            exported.dimensions_units, rel=1e-5
        )
        assert ("PHYS" in info) == (collision == "hull")
        assert all(path in info for path in remaps.values())


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("CS2ASSET_INTEGRATION") != "1",
    reason="Set CS2ASSET_INTEGRATION=1 for real Blender tests",
)
def test_real_packed_alpha_and_rejected_procedural_material(tmp_path):
    from cs2asset.discovery import discover_blender
    from cs2asset.images import read_image

    blender = discover_blender()
    assert blender
    source, bad_source = tmp_path / "alpha.blend", tmp_path / "procedural.blend"
    script = tmp_path / "create_alpha.py"
    script.write_text(
        f"""
import bpy
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add()
material = bpy.data.materials.new('Cutout')
material.use_nodes = True
bpy.context.object.data.materials.append(material)
shader = material.node_tree.nodes.get('Principled BSDF')
image = bpy.data.images.new('Packed', width=4, height=4, alpha=True)
image.pixels[:] = [0.8, 0.2, 0.1, 0.25] * 16
image.pack()
texture = material.node_tree.nodes.new('ShaderNodeTexImage')
texture.image = image
material.node_tree.links.new(texture.outputs['Color'], shader.inputs['Base Color'])
material.node_tree.links.new(texture.outputs['Alpha'], shader.inputs['Alpha'])
packed = bpy.data.images.new('PackedPBR', width=4, height=4, alpha=False, is_data=True)
packed.pixels[:] = [0.9, 0.35, 0.7, 1] * 16
packed.pack()
pbr = material.node_tree.nodes.new('ShaderNodeTexImage')
pbr.image = packed
separate = material.node_tree.nodes.new('ShaderNodeSeparateColor')
separate.mode = 'RGB'
material.node_tree.links.new(pbr.outputs['Color'], separate.inputs['Color'])
material.node_tree.links.new(separate.outputs['Green'], shader.inputs['Roughness'])
material.node_tree.links.new(separate.outputs['Blue'], shader.inputs['Metallic'])
bpy.ops.wm.save_as_mainfile(filepath={str(source)!r})
noise = material.node_tree.nodes.new('ShaderNodeTexNoise')
material.node_tree.links.new(noise.outputs['Color'], shader.inputs['Base Color'])
bpy.ops.wm.save_as_mainfile(filepath={str(bad_source)!r})
""",
        encoding="utf-8",
    )
    subprocess.run(
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
        capture_output=True,
        check=True,
        timeout=120,
    )
    exported = export_model(source, tmp_path / "export", blender)
    assert read_image(exported.materials[0].maps["opacity"])[:, :, 0].mean() == pytest.approx(
        0.25, abs=0.005
    )
    assert exported.materials[0].maps["base_color"].is_file()
    assert read_image(exported.materials[0].maps["roughness"])[:, :, 0].mean() == pytest.approx(
        0.35, abs=0.005
    )
    assert read_image(exported.materials[0].maps["metalness"])[:, :, 0].mean() == pytest.approx(
        0.7, abs=0.005
    )
    with pytest.raises(CS2AssetError, match="Unsupported procedural material node"):
        export_model(bad_source, tmp_path / "bad_export", blender)
