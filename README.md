# cs2asset

Import textures, HDR skies, and 3D models into **Counter-Strike 2 Hammer**.

`cs2asset` converts common asset formats into Source 2 resources, runs Valve's compiler, and installs the result directly into a selected CS2 addon.

Assets can come from local files, [Poly Haven](https://polyhaven.com/), or [ambientCG](https://ambientcg.com/). Both providers support PBR materials, HDR skies, and static models.

## Features

- Import PBR texture sets as CS2 materials
- Import `.blend`, `.glb`, `.gltf`, `.fbx`, and `.obj` models
- Import `.hdr` and `.exr` panoramas as HDR skies
- Search and import assets directly from Poly Haven and ambientCG
- Generate editable Source 2 source files and compiled resources
- Automatically discover CS2 Workshop Tools, Blender, and addon projects
- Rebuild previously imported assets without changing their Hammer paths
- Create experimental two-layer environment blend materials
- Keep generated assets organized in dedicated `cs2asset` directories

## Setup

You'll need Counter-Strike 2 with **Workshop Tools** installed and [Blender](https://www.blender.org/).

Blender is used internally for conversion and inspection, including the model pipeline and other asset-processing tasks.

The examples in this README use [uv](https://docs.astral.sh/uv/), but `cs2asset` is a normal Python package and does not depend on `uv` specifically.

Run the published package directly without cloning this repository:

```powershell
uvx cs2asset --help
```

Check that `cs2asset` can find CS2, Valve's tools, Blender, and your addons:

```powershell
uvx cs2asset doctor
```

Then select the addon you want to import assets into:

```powershell
uvx cs2asset init
```

`init` remembers the selected addon for future commands.

Create new addons through CS2 Workshop Tools before selecting them in `cs2asset`.

## Quick start

### Windows right-click import

Register **Import into CS2 Hammer** for supported model files, HDR/EXR skies,
and PBR texture ZIP archives:

```powershell
uvx cs2asset context-menu add
uvx cs2asset context-menu status
uvx cs2asset context-menu remove
```

Registration applies only to your Windows user and does not require administrator
access or change default file associations. On Windows 11, use **Show more options**.
Select one file at a time. The importer uses your saved addon, or prompts you to
choose one, and keeps the console open until you press Enter after the import.

The entry uses an absolute path to `uv.exe` to run the published `cs2asset` package,
not a temporary Python environment. If uv is not on PATH, use
`context-menu add --uv "C:\path\to\uv.exe"`. Run `add` again if you move uv.
When testing an unreleased checkout, Explorer still runs the published package;
the published version must include these commands before the entry can be used.

Import local assets:

```powershell
uvx cs2asset import .\assets\mossy_rock.glb
uvx cs2asset import .\assets\forest_ground\
uvx cs2asset import .\assets\sunset.hdr
```

Or import directly from Poly Haven:

```powershell
uvx cs2asset import polyhaven:concrete_floor_01 --resolution 2k
uvx cs2asset import polyhaven:sunset_jhbcentral --resolution 4k
uvx cs2asset import polyhaven:dirty_football --resolution 2k
```

Or import a material directly from ambientCG:

```powershell
uvx cs2asset import ambientcg:Grass005 --resolution 2k
```

After an import, `cs2asset` prints the final Source 2 resource path. Use that path directly in Hammer's Asset Browser.

An already-open Hammer session may need an Asset Browser refresh before newly imported resources appear.

## Using Poly Haven

Poly Haven is the easiest way to get usable materials, HDRIs, and models into Hammer without manually downloading and converting files.

Search for assets from the CLI:

```powershell
uvx cs2asset search "forest ground" --provider polyhaven
uvx cs2asset search "mossy rock" --provider polyhaven --type models
uvx cs2asset search "sunset" --provider polyhaven --type hdris
```

Then import an asset by ID:

```powershell
uvx cs2asset import polyhaven:forest_ground_04
uvx cs2asset import polyhaven:dirty_football
uvx cs2asset import polyhaven:sunset_jhbcentral
```

You can also paste a Poly Haven asset URL directly:

```powershell
uvx cs2asset import https://polyhaven.com/a/concrete_floor_01
```

Inspect what will be imported before doing anything:

```powershell
uvx cs2asset info polyhaven:concrete_floor_01
```

Choose a resolution:

```powershell
uvx cs2asset import polyhaven:concrete_floor_01 --resolution 1k
uvx cs2asset import polyhaven:concrete_floor_01 --resolution 2k
uvx cs2asset import polyhaven:sunset_jhbcentral --resolution 4k
```

Default Poly Haven resolutions are:

- 2K for textures
- 2K for models
- 4K for HDR skies

If a requested resolution does not exist, `cs2asset` reports the available choices.

Once imported, the asset behaves like any other local `cs2asset` import and can be listed or rebuilt:

```powershell
uvx cs2asset list
uvx cs2asset rebuild IMPORT_ID
```

Cached Poly Haven assets can also be rebuilt offline:

```powershell
uvx cs2asset rebuild IMPORT_ID --offline
```

Poly Haven assets are CC0.

## Using ambientCG

ambientCG provides PBR materials, HDRI panoramas, and 3D models that can be imported directly into Hammer.

Search for materials from the CLI:

```powershell
uvx cs2asset search "grass" --provider ambientcg
```

Then import a material by ID:

```powershell
uvx cs2asset import ambientcg:Grass005
```

You can also paste an ambientCG asset URL directly:

```powershell
uvx cs2asset import https://ambientcg.com/a/Grass005
```

Inspect the selected download before importing:

```powershell
uvx cs2asset info ambientcg:Grass005
```

Choose a resolution:

```powershell
uvx cs2asset import ambientcg:Grass005 --resolution 1k
uvx cs2asset import ambientcg:Grass005 --resolution 2k
```

The default resolution is 2K. The importer prefers PNG packages and falls back to
JPG at the requested resolution. If that resolution is unavailable, it reports
the available packages.

Import models and skies by ID or URL:

```powershell
cs2asset import ambientcg:3DApple001 --resolution 2k
cs2asset import https://ambientcg.com/view?id=DaySkyHDRI071A --resolution 4k --auto-exposure
cs2asset search "apple" --provider ambientcg --type models
cs2asset search "day sky" --provider ambientcg --type hdris
```

Models default to 2K textures and prefer SQ geometry, falling back to HQ or LQ when needed. Model ZIP dependencies are inspected with Blender before conversion. HDRIs default to 4K and use the EXR/HDR panorama from the package, not its tonemapped preview. Small negative ambientCG EXR values down to −0.01 are clamped in a separate working copy, with the original hash and adjustment recorded in the import report; larger negative radiance remains an error.

Once imported, assets can be listed and rebuilt like other imports:

```powershell
uvx cs2asset list
uvx cs2asset rebuild IMPORT_ID
```

Cached ambientCG assets can also be rebuilt offline:

```powershell
uvx cs2asset rebuild IMPORT_ID --offline
```

Use the `ambientcg:` prefix or an ambientCG asset URL; bare asset IDs default to
Poly Haven. ambientCG supports materials, models, and HDRIs.

ambientCG assets are CC0.

## Supported inputs

`cs2asset` normally detects the asset type automatically.

| Input | Imported as |
| --- | --- |
| `.blend` | Model |
| `.glb` | Model |
| `.gltf` | Model |
| `.fbx` | Model |
| `.obj` | Model |
| `.hdr` | HDR sky |
| `.exr` | HDR sky |
| Texture directory | Material |
| Texture ZIP | Material |
| `polyhaven:ID` | Poly Haven asset |
| Poly Haven asset URL | Poly Haven asset |
| `ambientcg:ID` | ambientCG asset |
| ambientCG asset URL | ambientCG asset |

You can require a specific asset type with:

```powershell
uvx cs2asset import SOURCE --type material
uvx cs2asset import SOURCE --type model
uvx cs2asset import SOURCE --type sky
```

## Materials

A directory containing a recognizable PBR texture set can be imported directly.

For example:

```text
rock/
├── rock_albedo.png
├── rock_normal.png
├── rock_roughness.png
└── rock_ao.png
```

Import it with:

```powershell
uvx cs2asset import .\rock\
```

Supported texture names include:

| Map | Common names |
| --- | --- |
| Base color | `basecolor`, `base_color`, `albedo`, `diffuse`, `color`, `diff` |
| Normal | `normal`, `normalgl`, `normaldx`, `nor_gl`, `nor_dx`, `nrm` |
| Roughness | `roughness`, `rough` |
| Metalness | `metallic`, `metalness`, `metal` |
| Ambient occlusion | `ao`, `ambient_occlusion` |
| Height | `height`, `displacement`, `disp` |
| Opacity | `opacity`, `alpha` |
| ARM / ORM | AO, roughness, and metalness in R/G/B |

A base-color texture is required.

Generic names such as `normal` and `nrm` are treated as OpenGL normal maps by default. Explicit GL and DirectX names are detected automatically.

You can specify the convention for generic normal filenames:

```powershell
uvx cs2asset import .\rock\ --normal-format dx
```

Local folders and ZIP packages can contain both DirectX and OpenGL normal maps
for the same material. The importer selects OpenGL when both exist and detects
DirectX when it is the only variant. Duplicate maps of one convention and mixed
texture sets still produce an ambiguity error. Explicit `NormalGL`/`NormalDX`
filenames determine conversion; `--normal-format dx` is a hint for generic
normal filenames with no declared convention. Selected normals are reported in
choices and warnings, and DirectX inputs are converted to OpenGL once.

Materials use CS2's `csgo_complex.vfx` shader.

Missing optional maps use constant values rather than preventing the material from being imported.

Packed ARM/ORM maps are supported when their layout is known.

## Models

FBX embedded images are extracted into the build workspace. Missing external images
are resolved against the model directory and its `.fbm` folder; ambiguous filenames
produce an error. Textures must be connected to supported material shader inputs.

Supported formats:

```text
.blend
.glb
.gltf
.fbx
.obj
```

Example:

```powershell
uvx cs2asset import .\assets\mossy_rock.glb
```

`cs2asset` uses Blender to inspect and convert the source before generating the Source 2 model resources required by Hammer.

Static meshes, UVs, materials, supplied LODs, and basic collision are supported.

By default, a simple convex collision hull is generated. Collision can be disabled:

```powershell
uvx cs2asset import .\assets\rock.glb --collision none
```

A different scale can also be supplied:

```powershell
uvx cs2asset import .\assets\rock.glb --scale 1.0
```

Rigged models are currently imported as static snapshots of their evaluated pose.

## HDR skies

HDR and EXR panoramas can be imported directly:

```powershell
uvx cs2asset import .\assets\sunset.hdr
```

You can adjust yaw and exposure during import:

```powershell
uvx cs2asset import .\assets\sunset.hdr --yaw 90 --exposure -1
```

The generated sky material can be assigned to `env_sky` in Hammer.

The panorama provides the visual sky and environment data. Map-specific lighting such as sun direction and brightness is still configured in Hammer.

## Projects

List discovered addons:

```powershell
uvx cs2asset projects list
```

Select one:

```powershell
uvx cs2asset projects use de_example
```

You can also specify the project for an individual import:

```powershell
uvx cs2asset import .\assets\rock.glb --project de_example
```

For scripts or other non-interactive environments:

```powershell
uvx cs2asset init --project de_example
```

Non-interactive commands do not stop to ask for project selection.

For the complete command and argument list, see the [CLI reference](docs/cli-reference.md).

## Common commands

```powershell
# Check the local setup
uvx cs2asset doctor

# Select an addon
uvx cs2asset init

# Import an asset
uvx cs2asset import SOURCE

# Inspect an asset
uvx cs2asset info SOURCE

# Search online providers
uvx cs2asset search "QUERY" --provider polyhaven
uvx cs2asset search "QUERY" --provider ambientcg

# List installed imports
uvx cs2asset list

# Rebuild an existing import
uvx cs2asset rebuild IMPORT_ID

# Show configuration
uvx cs2asset config show

# Show help
uvx cs2asset --help
```

Some useful import options:

```powershell
uvx cs2asset import concrete_floor_01 --resolution 1k
uvx cs2asset import concrete_floor_01 --tiling 2
uvx cs2asset import concrete_floor_01 --surface concrete
uvx cs2asset import dirty_football --collision none
uvx cs2asset import sunset_jhbcentral --yaw 90 --exposure -1
```

## Dry runs

Resolve an import without converting, compiling, or installing anything:

```powershell
uvx cs2asset import concrete_floor_01 --dry-run
```

This is useful for checking the selected source, detected asset type, options, project, and expected output paths.

## Rebuilding assets

Previously imported assets can be rebuilt while keeping their existing Hammer resource paths:

```powershell
uvx cs2asset rebuild IMPORT_ID
```

Local imports normally reread the original source files.

To rebuild from the retained input snapshot instead:

```powershell
uvx cs2asset rebuild IMPORT_ID --cached-inputs
```

Cached provider assets can be rebuilt offline:

```powershell
uvx cs2asset rebuild IMPORT_ID --offline
```

Use `cs2asset list` to find import IDs.

## Environment blends

> **Experimental**

`cs2asset` can create two-layer Hammer materials using `csgo_environment_blend.vfx`.

Local materials:

```powershell
uvx cs2asset blend create `
    .\materials\forest_ground `
    .\materials\rock `
    --name forest_rock
```

Poly Haven materials:

```powershell
uvx cs2asset blend create `
    polyhaven:forest_ground_04 `
    polyhaven:rock_face_03 `
    --name forest_rock
```

Local and online sources can also be mixed:

```powershell
uvx cs2asset blend create `
    .\materials\rock `
    polyhaven:forest_ground_04 `
    --name mixed_ground
```

The resulting material can be applied to suitable Hammer geometry and painted using Hammer's blend tools.

The current implementation supports two layers. Extra layers, custom transition controls, and cutout/blended transparency are not yet supported.

## Configuration

Show the current configuration:

```powershell
uvx cs2asset config show
```

Set a user default:

```powershell
uvx cs2asset config set resolution 1k
```

Set a project-specific value:

```powershell
uvx cs2asset config set surface concrete --project de_example
```

Configuration precedence is:

```text
command line
↓
project configuration
↓
user configuration
↓
automatic discovery
```

Useful environment variables:

```text
CS2ASSET_CONFIG_DIR
CS2ASSET_CACHE_DIR
CS2ASSET_CS2_ROOT
CS2ASSET_BLENDER
```

Custom tool locations can also be provided directly:

```powershell
uvx cs2asset `
    --cs2-root 'D:\SteamLibrary\steamapps\common\Counter-Strike Global Offensive' `
    doctor
```

```powershell
uvx cs2asset import dirty_football `
    --blender 'C:\Program Files\Blender Foundation\Blender 5.1\blender.exe'
```

## Generated resources

Editable Source 2 resources are installed into the selected addon's `content` tree.

Compiled resources are installed into the corresponding `game` tree.

Generated assets are organized under:

```text
materials/cs2asset/
materials/skybox/cs2asset/
models/cs2asset/
```

This keeps imported resources separate from manually created addon assets.

Resource filenames use the asset name, so Hammer displays names such as
`lifebuoy.vmdl`, `lifebuoy.vmat`, and `snow_road.vmat` instead of `model.vmdl`
or `blend.vmat`. Provider/asset/variant folders still separate sources and settings.
A model with one material puts its named VMAT directly in its material variant
folder; several materials use names such as `lifebuoy_rope.vmat` in separate folders.

Rebuild older imports to get these names. Previously owned generic resource paths
remain as editable and compiled compatibility copies, preserving existing map
references. New imports do not create these copies. The manifest records them in
`resource_aliases`.

Valve compiler warnings appear in terminal/JSON output and the manifest's combined
`warnings` list. `compiler_warnings` records each warning's resource, message, and
log path. Warnings remain visible after an unchanged dependency check and clear
after a clean rebuild.

`cs2asset` also retains the information needed to rebuild previously imported assets.

## Limitations

Current limitations include:

- Models are imported as static geometry
- Skeletal animation is not exported
- Rigged models are imported as static evaluated poses
- Physics-prop setup is not generated
- Collision is limited to a simple convex hull
- Automatic LOD generation is not implemented
- Procedural Principled material inputs require `--bake-materials`
- Unsupported or non-standard material graphs may require manual preparation
- Multiple UV channels on the same mesh require baking
- Compatible affine UV mappings are applied to mesh UVs; conflicting mappings require baking
- Material opacity uses alpha testing rather than blended transparency
- Displacement/parallax is not currently generated
- Environment blends are experimental
- HDR sky imports do not configure map-specific sunlight

Where possible, unsupported inputs result in an explicit conversion error rather than silently generating an incorrect asset.

## Development

Clone the repository and install its development dependencies:

```powershell
uv sync --locked
uv run cs2asset --help
```

Run the test suite:

```powershell
uv run pytest -q
uv run ruff check src tests scripts
```

Run integration tests against locally installed CS2 and Blender tools:

```powershell
$env:CS2ASSET_INTEGRATION = '1'
uv run pytest -q
```

Build the package:

```powershell
uv build
```

## Installation

Install from PyPI with `uv`:

```powershell
uv tool install cs2asset
```

Then use `cs2asset` directly:

```powershell
cs2asset doctor
cs2asset init
cs2asset import .\assets\rock.glb
```

You can also install the package using another Python package manager if preferred.

To run directly from the repository without installing:

```powershell
uvx --isolated --from . cs2asset --version
```

To run the published package without installing a persistent command:

```powershell
uvx cs2asset doctor
```

To upgrade a persistent installation, run `uv tool upgrade cs2asset`.

## Releases

The `.github/workflows/publish.yml` workflow tests, builds, and publishes to PyPI
when a `v*` tag is pushed. The tag must match both versions in `pyproject.toml`
and `src/cs2asset/__init__.py`.

Before the first release, configure a pending Trusted Publisher in your PyPI
account with these values:

- Project name: `cs2asset`
- GitHub owner: `gronnmann`
- Repository: `cs2asset`
- Workflow filename: `publish.yml`
- Environment: `pypi`

For each release, update both package versions, run `uv lock`, commit the changes,
and push the branch and matching tag:

```powershell
git push origin master
git tag -a v0.2.1 -m "Release 0.2.1"
git push origin v0.2.1
```

Use the new version number for subsequent releases. PyPI versions cannot be reused.

## License

See [`LICENSE`](LICENSE) for the license of `cs2asset`.

Assets imported through `cs2asset` retain their original licenses. Poly Haven and ambientCG assets are CC0.


## Models with multiple objects and mapped materials

One source file produces one model by default. Inspect the source before selecting
or splitting it (inspection downloads dependencies but does not compile or install):

```powershell
cs2asset model-list https://polyhaven.com/a/fir_sapling_medium
cs2asset import trees.blend --object TreeA
cs2asset import trees.blend --collection TreeA
cs2asset import trees.blend --split collections --origin center
cs2asset import props.blend --split objects
cs2asset import trees.blend --bake-materials --bake-resolution 2048
```

Selection uses exact names. Object selection includes its descendants and matching
named LOD siblings. Object splitting starts with visible objects without an LOD
suffix or with LOD0. Collection splitting uses top-level scene collections, keeping
nested collections and LODs together. Hidden render objects are excluded. Each
split group has its own model and material resources, recorded in one import so
rebuilding repeats the same selection. Materials are currently generated separately
for each split group. `--origin source` preserves source coordinates; `center`
centers each model's LOD0 bounding box and applies that offset to all its LODs.

Compatible constant Mapping nodes are applied directly to exported UV coordinates.
Named UV maps and corner vector UV attributes are supported. Conflicting texture
mappings, procedural coordinates, and rotation/reflection with tangent normal maps
require explicit baking. `--bake-materials` bakes linked base color, roughness,
metalness, opacity, and tangent normal inputs from a Principled BSDF onto the active
UV layout. Resolution accepts 16–8192 pixels. Baking does not unwrap meshes:
overlapping UVs must represent compatible surface values, and UV islands must fit
the texture tile. A mixed surface with one connected Principled shader can bake that PBR component;
additional translucent/additive lobes are omitted and reported. Surfaces without a
unique connected Principled shader remain unsupported.
