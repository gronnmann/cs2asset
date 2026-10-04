"""Real Blender checks for selection, UV mapping, and baking."""

import pytest

from cs2asset.compiler import run_process
from cs2asset.discovery import discover_blender
from cs2asset.models import export_model, inspect_model


@pytest.fixture(scope="module")
def model_scene(tmp_path_factory):
    blender = discover_blender()
    if blender is None:
        pytest.skip("Blender unavailable")
    root = tmp_path_factory.mktemp("model-features")
    script = root / "generate.py"
    script.write_text(
        """
import bpy
from pathlib import Path
root = Path(ROOT)
bpy.ops.wm.read_factory_settings(use_empty=True)
image = bpy.data.images.new('checker', width=16, height=16)
image.generated_color = (0.3, 0.6, 0.2, 1)
image.pack()
mat = bpy.data.materials.new('Mapped')
mat.use_nodes = True
nodes, links = mat.node_tree.nodes, mat.node_tree.links
tex = nodes.new('ShaderNodeTexImage')
tex.image = image
uv = nodes.new('ShaderNodeTexCoord')
mapping = nodes.new('ShaderNodeMapping')
mapping.inputs['Scale'].default_value = (2, 3, 1)
links.new(uv.outputs['UV'], mapping.inputs['Vector'])
links.new(mapping.outputs['Vector'], tex.inputs['Vector'])
links.new(tex.outputs['Color'], nodes.get('Principled BSDF').inputs['Base Color'])
for name, x in [('TreeA', 0), ('TreeB', 5)]:
    collection = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(collection)
    bpy.ops.mesh.primitive_cube_add(location=(x, 0, 0))
    obj = bpy.context.object
    obj.name = name
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    collection.objects.link(obj)
    obj.data.materials.append(mat)
bpy.ops.wm.save_as_mainfile(filepath=str(root / 'trees.blend'))
bpy.data.objects['TreeA'].name = 'Tree_LOD0'
bpy.data.objects['TreeB'].name = 'Tree_LOD1'
bpy.ops.wm.save_as_mainfile(filepath=str(root / 'lods.blend'))
extra = nodes.new('ShaderNodeTexImage')
extra.image = image
links.new(extra.outputs['Color'], nodes.get('Principled BSDF').inputs['Roughness'])
bpy.ops.wm.save_as_mainfile(filepath=str(root / 'conflicting.blend'))
""".replace("ROOT", repr(str(root)))
    )
    run_process(
        [
            str(blender),
            "--background",
            "--factory-startup",
            "--python-exit-code",
            "1",
            "--python",
            str(script),
        ],
        root / "generate.log",
        timeout=120,
    )
    return blender, root


def test_selection_mapping_and_baking(model_scene):
    blender, root = model_scene
    source = root / "trees.blend"
    listing = inspect_model(source, root / "list", blender)
    assert {o["name"] for o in listing["objects"]} == {"TreeA", "TreeB"}
    combined = export_model(source, root / "combined", blender)
    assert combined.mesh_count == 2
    selected = export_model(source, root / "selected", blender, collection="TreeA", origin="center")
    assert selected.mesh_count == 1
    assert selected.dimensions_units == pytest.approx([2 / 0.0254] * 3)
    baked = export_model(
        source,
        root / "baked",
        blender,
        object_name="TreeA",
        bake_materials=True,
        bake_resolution=32,
    )
    assert baked.mesh_count == 1
    assert baked.dimensions_units == pytest.approx(selected.dimensions_units)
    assert "base_color" in baked.materials[0].maps
    assert any("baked" in w for w in baked.warnings)
    from cs2asset.images import read_image

    pixels = read_image(baked.materials[0].maps["base_color"])
    assert pixels[:, :, :3].max() > 0.1
    # Confirm the exported UVs contain the mapping rather than merely allowing it.
    inspect = root / "inspect_fbx.py"
    report = root / "uv.json"
    inspect.write_text(
        """
import bpy, json
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=FBX)
values = [tuple(d.uv) for o in bpy.context.scene.objects if o.type == 'MESH'
          for d in o.data.uv_layers.active.data]
from pathlib import Path
Path(REPORT).write_text(json.dumps(values))
""".replace("FBX", repr(str(selected.fbx.resolve()))).replace("REPORT", repr(str(report.resolve())))
    )
    run_process(
        [str(blender), "--background", "--factory-startup", "--python", str(inspect)],
        root / "uv.log",
        timeout=120,
    )
    import json

    values = json.loads(report.read_text())
    assert max(v[1] for v in values) > 1.0


def test_split_resources(model_scene, tmp_path):
    from cs2asset.assets import AssetInfo, ModelInput, NormalizedAsset, ResolvedAsset, SourceRef
    from cs2asset.pipeline import ImportOptions, convert_asset

    blender, root = model_scene
    resolved = ResolvedAsset(
        AssetInfo("trees", "Trees", "model"),
        SourceRef("local", str(root / "trees.blend")),
        "native",
        (),
        namespace="local/trees",
    )
    normalized = NormalizedAsset(resolved, ModelInput(root / "trees.blend"), {})
    result = convert_asset(
        normalized,
        tmp_path / "content",
        tmp_path / "build",
        "native",
        ImportOptions(split="collections"),
        blender,
    )
    models = [r for r in result["resources"] if r.endswith(".vmdl")]
    assert len(models) == 2
    assert {m["name"] for m in result["detail"]["models"]} == {"TreeA", "TreeB"}
    assert all((tmp_path / "content" / r).is_file() for r in result["resources"])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"split": "bad"},
        {"origin": "bad"},
        {"object_name": "A", "collection": "B"},
        {"split": "objects", "collection": "A"},
        {"bake_resolution": 0},
    ],
)
def test_invalid_model_options(kwargs):
    from cs2asset.errors import CS2AssetError
    from cs2asset.pipeline import ImportOptions

    with pytest.raises(CS2AssetError):
        ImportOptions(**kwargs).validate()


def test_lod_siblings_and_conflicting_mappings(model_scene):
    from cs2asset.errors import CS2AssetError

    blender, root = model_scene
    exported = export_model(
        root / "lods.blend", root / "lod-export", blender, object_name="Tree_LOD0"
    )
    assert exported.mesh_count == 2
    assert sorted(exported.lods) == [0, 1]
    with pytest.raises(CS2AssetError, match="conflicting texture mappings"):
        export_model(root / "conflicting.blend", root / "conflicting-export", blender)
    baked = export_model(
        root / "conflicting.blend",
        root / "conflicting-baked",
        blender,
        bake_materials=True,
        bake_resolution=32,
    )
    assert set(baked.materials[0].maps) >= {"base_color", "roughness"}
