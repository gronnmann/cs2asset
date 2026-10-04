# Static model validation

Validated on Windows against the installed CS2 Workshop Tools on 2026-10-04,
Steam app 730 build **25687242**.
Tests use only the dedicated `cs2asset_model_probe` addon. No user map is modified.

## Contract and evidence

The installed Valve source
`content/csgo/workshop/content_examples/breakable_glass/breakable_glass_01.vmdl`
establishes the ModelDoc 36 header, `RenderMeshFile`, `PhysicsShapeList`, and
`PhysicsHullFromRender` fields. The importer generates that text format and calls
Valve's `resourcecompiler.exe`; it does not implement compiled resource formats.
Material remaps and LOD groups were then tested against the actual compiler and
inspected using Valve's own `resourceinfo.exe -all`.

The compiler context is:

```text
resourcecompiler.exe -nop4 -game <CS2>/game/csgo -i <absolute addon content .vmdl>
```

The compiled file appears at the same relative path in the addon's game tree.
Both addon directories must exist; this does not require a custom `gameinfo.gi`.

Blender 4.3.2 and 5.1.2 were exercised headlessly. The worker uses factory settings,
`--disable-autoexec`, and `open_mainfile(use_scripts=False)`. It evaluates static
meshes and modifiers, expands dependency-graph instances, preserves mesh material
slots and UVs, applies world transforms, and exports only mesh objects.

## Units, axes, materials, and collision

A synthetic two-material box occupied positive X/Y/Z coordinates with physical
dimensions 1 × 2 × 3 meters. Its compiled render bounds, read by Valve's utility,
were `[0,0,0]` to `[39.37008,78.74016,118.11024]`, within floating-point rounding.
The initial exporter configuration was **rejected** by this check because Valve
interpreted it at 100 times the intended size and rotated the XY axes.

The validated configuration converts evaluated coordinates from scene meters to
Source inches, then exports FBX using `global_scale=0.01`, `apply_unit_scale=False`,
`apply_scale_options='FBX_SCALE_NONE'`, `axis_forward='-X'`, `axis_up='Z'`, and
`use_space_transform=False`. The ModelDoc `import_scale` remains 1.0. A source
without declared units generates an explicit meter-assumption warning; `--scale`
is a user override, not an automatic resize to a guessed bounding box.

The synthetic box's compiled resource contains both intended material references.
The hull version contains a `PHYS` block. A separately compiled no-collision
version has no `PHYS` block. Convex hull collision is a simple approximation;
it does not preserve cavities or concave architecture.

Packed image extraction and an image's Alpha output were also exercised with
Blender: a 0.25 alpha input becomes a grayscale opacity map within PNG quantization
tolerance, independent of the source red channel. Unsupported procedural color
nodes fail with an actionable bake-first error. Transformed/procedural texture
coordinates likewise fail instead of silently changing the material.

## Real Poly Haven model

Downloaded the actual `dirty_football` 1K Blender variant and all three declared
dependencies from API-provided URLs. Blender 5.1.2 exported its four supplied LODs.
The shared material converter generated the base color, OpenGL normal, roughness,
and constant optional maps. Both convex-hull and no-collision models compiled.

Valve `resourceinfo` reported:

| LOD | Triangles | Group mask | Switch distance |
| --- | ---: | ---: | ---: |
| 0 | 19,998 | 1 | 0 |
| 1 | 9,996 | 2 | 100 |
| 2 | 4,996 | 4 | 200 |
| 3 | 2,496 | 8 | 300 |

The model is approximately 8.716 × 8.662 × 8.663 Source inches, consistent with a
football. LOD sources are retained as separate FBX files. Source LOD numbers are
recognized in object/collection names; untagged meshes are shared between levels.
Switch distances are documented defaults, not asserted to be source-authored.
Hull generation uses only LOD0.

The compiler prints `GetFbxMaterialPath Failed` while trying automatic material
lookup before applying ModelDoc remaps. Compilation completes successfully, and
the compiled resource references the explicit intended materials. Do not treat
that intermediate diagnostic as evidence that remapping failed.

## Repeatable checks and remaining limits

```powershell
uv run pytest tests/test_models.py
$env:CS2ASSET_INTEGRATION = '1'
uv run pytest tests/test_models.py -q
```

The opt-in test creates its own synthetic Blender scene, discovers the local
tools, compiles both collision policies, and checks compiled bounds, material
references, and physics with Valve's reader. It also tests packed alpha extraction
and procedural-node rejection. This validates data and compilation, not appearance.

An interactive Hammer/test-map render, lighting/normal appearance, runtime LOD
transitions, collision gameplay, and live asset-browser refresh remain unverified.
Complex shader graphs, nonidentity texture-coordinate transforms, named alternate
UV channels, skeletal animation, concave collision, and automatic LOD reduction
are not supported by this first static-model pipeline. Unsupported material paths
report failures or explicit warnings rather than silently claiming equivalence.

References: [Blender FBX API](https://docs.blender.org/api/current/bpy.ops.export_scene.html),
[Blender dependency graph API](https://docs.blender.org/api/current/bpy.types.Depsgraph.html),
and [Facepunch's public ModelDoc LOD example](https://github.com/Facepunch/sbox-public/blob/master/game/addons/citizen/Assets/models/citizen_props/foamhand.vmdl).
