# cs2asset

Import textures, HDR skies, and 3D models into **Counter-Strike 2 Hammer**.

`cs2asset` converts common asset formats into Source 2 resources, runs Valve's compiler, and installs the result directly into a selected CS2 addon.

Assets can come from local files or supported online providers. [Poly Haven](https://polyhaven.com/) is supported out of the box.

## Features

- Import PBR texture sets as CS2 materials
- Import `.blend`, `.glb`, `.gltf`, `.fbx`, and `.obj` models
- Import `.hdr` and `.exr` panoramas as HDR skies
- Search and import assets directly from Poly Haven
- Generate editable Source 2 source files and compiled resources
- Automatically discover CS2 Workshop Tools, Blender, and addon projects
- Rebuild previously imported assets without changing their Hammer paths
- Create experimental two-layer environment blend materials
- Keep generated assets organized in dedicated `cs2asset` directories

## Setup

You'll need Counter-Strike 2 with **Workshop Tools** installed and [Blender](https://www.blender.org/).

Blender is used internally for conversion and inspection, including the model pipeline and other asset-processing tasks.

The examples in this README use [uv](https://docs.astral.sh/uv/), but `cs2asset` is a normal Python package and does not depend on `uv` specifically.

From the repository:

```powershell
uv sync
```

Check that `cs2asset` can find CS2, Valve's tools, Blender, and your addons:

```powershell
uv run cs2asset doctor
```

Then select the addon you want to import assets into:

```powershell
uv run cs2asset init
```

`init` remembers the selected addon for future commands.

Create new addons through CS2 Workshop Tools before selecting them in `cs2asset`.

## Quick start

Import local assets:

```powershell
uv run cs2asset import .\assets\mossy_rock.glb
uv run cs2asset import .\assets\forest_ground\
uv run cs2asset import .\assets\sunset.hdr
```

Or import directly from Poly Haven:

```powershell
uv run cs2asset import polyhaven:concrete_floor_01 --resolution 2k
uv run cs2asset import polyhaven:sunset_jhbcentral --resolution 4k
uv run cs2asset import polyhaven:dirty_football --resolution 2k
```

After an import, `cs2asset` prints the final Source 2 resource path. Use that path directly in Hammer's Asset Browser.

An already-open Hammer session may need an Asset Browser refresh before newly imported resources appear.

## Using Poly Haven

Poly Haven is the easiest way to get usable materials, HDRIs, and models into Hammer without manually downloading and converting files.

Search for assets from the CLI:

```powershell
uv run cs2asset search "forest ground" --provider polyhaven
uv run cs2asset search "mossy rock" --provider polyhaven --type models
uv run cs2asset search "sunset" --provider polyhaven --type hdris
```

Then import an asset by ID:

```powershell
uv run cs2asset import polyhaven:forest_ground_04
uv run cs2asset import polyhaven:dirty_football
uv run cs2asset import polyhaven:sunset_jhbcentral
```

You can also paste a Poly Haven asset URL directly:

```powershell
uv run cs2asset import https://polyhaven.com/a/concrete_floor_01
```

Inspect what will be imported before doing anything:

```powershell
uv run cs2asset info polyhaven:concrete_floor_01
```

Choose a resolution:

```powershell
uv run cs2asset import polyhaven:concrete_floor_01 --resolution 1k
uv run cs2asset import polyhaven:concrete_floor_01 --resolution 2k
uv run cs2asset import polyhaven:sunset_jhbcentral --resolution 4k
```

Default Poly Haven resolutions are:

- 2K for textures
- 2K for models
- 4K for HDR skies

If a requested resolution does not exist, `cs2asset` reports the available choices.

Once imported, the asset behaves like any other local `cs2asset` import and can be listed or rebuilt:

```powershell
uv run cs2asset list
uv run cs2asset rebuild IMPORT_ID
```

Cached Poly Haven assets can also be rebuilt offline:

```powershell
uv run cs2asset rebuild IMPORT_ID --offline
```

Poly Haven assets are CC0.

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

You can require a specific asset type with:

```powershell
uv run cs2asset import SOURCE --type material
uv run cs2asset import SOURCE --type model
uv run cs2asset import SOURCE --type sky
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
uv run cs2asset import .\rock\
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

You can override the format:

```powershell
uv run cs2asset import .\rock\ --normal-format dx
```

Materials use CS2's `csgo_complex.vfx` shader.

Missing optional maps use constant values rather than preventing the material from being imported.

Packed ARM/ORM maps are supported when their layout is known.

## Models

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
uv run cs2asset import .\assets\mossy_rock.glb
```

`cs2asset` uses Blender to inspect and convert the source before generating the Source 2 model resources required by Hammer.

Static meshes, UVs, materials, supplied LODs, and basic collision are supported.

By default, a simple convex collision hull is generated. Collision can be disabled:

```powershell
uv run cs2asset import .\assets\rock.glb --collision none
```

A different scale can also be supplied:

```powershell
uv run cs2asset import .\assets\rock.glb --scale 1.0
```

Rigged models are currently imported as static snapshots of their evaluated pose.

## HDR skies

HDR and EXR panoramas can be imported directly:

```powershell
uv run cs2asset import .\assets\sunset.hdr
```

You can adjust yaw and exposure during import:

```powershell
uv run cs2asset import .\assets\sunset.hdr --yaw 90 --exposure -1
```

The generated sky material can be assigned to `env_sky` in Hammer.

The panorama provides the visual sky and environment data. Map-specific lighting such as sun direction and brightness is still configured in Hammer.

## Projects

List discovered addons:

```powershell
uv run cs2asset projects list
```

Select one:

```powershell
uv run cs2asset projects use de_example
```

You can also specify the project for an individual import:

```powershell
uv run cs2asset import .\assets\rock.glb --project de_example
```

For scripts or other non-interactive environments:

```powershell
uv run cs2asset init --project de_example
```

Non-interactive commands do not stop to ask for project selection.

## Common commands

```powershell
# Check the local setup
uv run cs2asset doctor

# Select an addon
uv run cs2asset init

# Import an asset
uv run cs2asset import SOURCE

# Inspect an asset
uv run cs2asset info SOURCE

# Search Poly Haven
uv run cs2asset search "QUERY" --provider polyhaven

# List installed imports
uv run cs2asset list

# Rebuild an existing import
uv run cs2asset rebuild IMPORT_ID

# Show configuration
uv run cs2asset config show

# Show help
uv run cs2asset --help
```

Some useful import options:

```powershell
uv run cs2asset import concrete_floor_01 --resolution 1k
uv run cs2asset import concrete_floor_01 --tiling 2
uv run cs2asset import concrete_floor_01 --surface concrete
uv run cs2asset import dirty_football --collision none
uv run cs2asset import sunset_jhbcentral --yaw 90 --exposure -1
```

## Dry runs

Resolve an import without converting, compiling, or installing anything:

```powershell
uv run cs2asset import concrete_floor_01 --dry-run
```

This is useful for checking the selected source, detected asset type, options, project, and expected output paths.

## Rebuilding assets

Previously imported assets can be rebuilt while keeping their existing Hammer resource paths:

```powershell
uv run cs2asset rebuild IMPORT_ID
```

Local imports normally reread the original source files.

To rebuild from the retained input snapshot instead:

```powershell
uv run cs2asset rebuild IMPORT_ID --cached-inputs
```

Cached provider assets can be rebuilt offline:

```powershell
uv run cs2asset rebuild IMPORT_ID --offline
```

Use `cs2asset list` to find import IDs.

## Environment blends

> **Experimental**

`cs2asset` can create two-layer Hammer materials using `csgo_environment_blend.vfx`.

Local materials:

```powershell
uv run cs2asset blend create `
    .\materials\forest_ground `
    .\materials\rock `
    --name forest_rock
```

Poly Haven materials:

```powershell
uv run cs2asset blend create `
    polyhaven:forest_ground_04 `
    polyhaven:rock_face_03 `
    --name forest_rock
```

Local and online sources can also be mixed:

```powershell
uv run cs2asset blend create `
    .\materials\rock `
    polyhaven:forest_ground_04 `
    --name mixed_ground
```

The resulting material can be applied to suitable Hammer geometry and painted using Hammer's blend tools.

The current implementation supports two layers. Extra layers, custom transition controls, and cutout/blended transparency are not yet supported.

## Configuration

Show the current configuration:

```powershell
uv run cs2asset config show
```

Set a user default:

```powershell
uv run cs2asset config set resolution 1k
```

Set a project-specific value:

```powershell
uv run cs2asset config set surface concrete --project de_example
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
uv run cs2asset `
    --cs2-root 'D:\SteamLibrary\steamapps\common\Counter-Strike Global Offensive' `
    doctor
```

```powershell
uv run cs2asset import dirty_football `
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

`cs2asset` also retains the information needed to rebuild previously imported assets.

## Limitations

Current limitations include:

- Models are imported as static geometry
- Skeletal animation is not exported
- Rigged models are imported as static evaluated poses
- Physics-prop setup is not generated
- Collision is limited to a simple convex hull
- Automatic LOD generation is not implemented
- Procedural Blender materials must be baked before importing
- Unsupported or non-standard material graphs may require manual preparation
- Alternate UV channels are not currently supported
- Non-identity texture mapping may require baking
- Material opacity uses alpha testing rather than blended transparency
- Displacement/parallax is not currently generated
- Environment blends are experimental
- HDR sky imports do not configure map-specific sunlight

Where possible, unsupported inputs result in an explicit conversion error rather than silently generating an incorrect asset.

## Development

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

Install the current checkout with `uv`:

```powershell
uv tool install .
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
uvx --python 3.12 --isolated --from . cs2asset --version
```

The package is not currently published to PyPI.

## License

See [`LICENSE`](LICENSE) for the license of `cs2asset`.

Assets imported through `cs2asset` retain their original licenses. Poly Haven assets are CC0.