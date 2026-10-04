# cs2asset

Import Poly Haven textures, HDR skies, and static models into a CS2 Hammer addon.
The CLI downloads dependencies, converts sources, calls Valve's own compiler,
and installs editable and compiled resources directly into your project.

## Quick start

Install [uv](https://docs.astral.sh/uv/), CS2 Workshop Tools, and Blender for model
imports. Run these commands from this repository:

```powershell
uv sync
uv run cs2asset doctor
uv run cs2asset init
uv run cs2asset search "mossy rock" --type models
uv run cs2asset import polyhaven:concrete_floor_01 --resolution 2k
uv run cs2asset import polyhaven:sunset_jhbcentral --resolution 4k
uv run cs2asset import polyhaven:dirty_football --resolution 2k
uv run cs2asset list
```

`init` discovers Steam libraries, CS2's compiler, Blender, and addon directories,
then asks you to select a project. The selection is remembered. In scripts use
`init --project YOUR_ADDON` or add `--project YOUR_ADDON` to `import`; a noninteractive
terminal never waits for project input. Create new addons in Workshop Tools first.

Poly Haven IDs, `polyhaven:ID`, and `https://polyhaven.com/a/ID` URLs are accepted.
Imports infer the asset type. Defaults are 2K textures/models and a 4K HDR panorama.
Unavailable resolutions fail with the available choices rather than silently
downloading another size. `info ID --resolution 1k` shows metadata and download size.

The tool prints the final material/model resource path. Use that resource in
Hammer's asset browser, place models, and assign sky materials to `env_sky`.
No file movement or manual conversion is required. An already-open Hammer session
may need an asset-browser refresh; live refresh has not been verified.

## Commands and options

```powershell
uv run cs2asset projects list
uv run cs2asset projects use de_example
uv run cs2asset import concrete_floor_01 --dry-run --resolution 1k
uv run cs2asset import dirty_football --collision none --scale 1.0
uv run cs2asset import sunset_jhbcentral --yaw 90 --exposure -1
uv run cs2asset import concrete_floor_01 --tiling 2 --surface concrete
uv run cs2asset rebuild dirty_football --offline
uv run cs2asset recover
uv run cs2asset --json list
uv run cs2asset --verbose import dirty_football
uv run cs2asset --help
```

`--dry-run` resolves the import and reports selected files without conversion,
compilation, or installing project outputs. `--refresh` refreshes provider metadata.
`--offline` uses cached metadata and verified files exclusively. `rebuild` rebuilds
every installed variant of the selected asset; `--variant` restricts it to one
printed variant identifier. `--force` forces Valve compilation. `--overwrite`
explicitly replaces user-edited **tool-owned** outputs; unrelated files are always
protected. Choose different conversion options to create a separate variant.

`--json` is available globally and on relevant commands. It suppresses progress
and produces structured results or runtime errors. Argument-parser errors use
normal CLI stderr. Global options such as `--cs2-root` and `--verbose` come before
the command; `import` also accepts its own `--blender` override.

```powershell
uv run cs2asset --cs2-root 'D:\SteamLibrary\steamapps\common\Counter-Strike Global Offensive' doctor
uv run cs2asset import dirty_football --blender 'C:\Program Files\Blender Foundation\Blender 5.1\blender.exe'
```

## Configuration and data

```powershell
uv run cs2asset config show
uv run cs2asset config set resolution 1k
uv run cs2asset config set surface concrete --project de_example
```

Precedence is command-line overrides, project settings, user settings, discovery.
Portable project defaults live in `content/csgo_addons/NAME/.cs2asset/config.json`.
Machine paths and the selected project live in the platform user configuration
directory; `init` prints its location. `doctor` reports the cache location.
Set `CS2ASSET_CONFIG_DIR` and `CS2ASSET_CACHE_DIR` to isolate either directory.
`CS2ASSET_CS2_ROOT` and `CS2ASSET_BLENDER` also supply tool paths.

Editable `.vmat`, images/EXR, FBX, and `.vmdl` sources go into the selected addon's
`content` tree. Compiled materials, textures, models, and all generated children
go into the matching `game` tree, under `materials/cs2asset`,
`materials/skybox/cs2asset`, and `models/cs2asset`. The import manifest records
provenance, source/output hashes, options, dimensions, tool identities, warnings,
and log paths in `.cs2asset/imports.json`.

Conversion and compilation take place in tool-owned staging addons. Fingerprints
include inputs, options, compiler/Blender identities, and converter source hashes.
Unchanged imports reuse conversions and invoke Valve's dependency check. Staging
and downloads have process locks; final publication has a per-project lock, atomic
file replacement, backups, and a recovery journal. Interrupted publication is
recovered automatically on the next import/list/recovery operation. Backups are
retained if recovery finds a later external edit.

Completed downloads and converted stages are retained for rebuilds. You may clear
the tool's cache when no imports are running; offline rebuilds will then need fresh
downloads. Compiler logs are retained under the corresponding cache build directory.

## Conversion behavior and limits

- PBR materials use the locally tested `csgo_complex.vfx` contract. Color stays
  sRGB; normal/roughness/AO/metalness remain linear. Known ARM channels are split
  only for missing separate maps. Missing optional maps use recorded constants.
  Opacity uses alpha testing; blended transparency is not implemented.
- HDR skies use the real EXR/HDR panorama, preserve radiance above 1, and apply
  optional yaw/exposure. Valve's `sky.vfx` compiler creates the cubemap and spherical
  harmonics. Panorama width and generated face size are separate values. Sun/map
  lighting settings are mapping decisions; imports do not edit your map.
- Models use headless Blender with source-file scripts disabled, evaluated static
  meshes, UVs, materials, supplied LODs, and validated inch units and Z-up axes.
  Collision is a simple convex hull by default, or explicitly none. Hulls do not
  preserve cavities. Source LOD switch distances use documented defaults.
- Conventional image-based Principled materials are supported. Procedural shaders,
  nonidentity texture mapping, alternate UV channels, and unsupported normal/bump
  graphs report actionable conversion errors. Bake those materials before importing.
  Skeletal animation, physics props, complex collision, automatic LOD reduction,
  displacement/parallax, and local-file CLI imports are later extensions.
- GL normal sources are selected by default. `--normal-format dx` converts DX source
  green channels. Visible directional-light normal orientation still needs an
  interactive Hammer check; the packing itself has been inspected with Valve tools.

## Validation

```powershell
uv run pytest -q
uv run ruff check src tests scripts
$env:CS2ASSET_INTEGRATION = '1'
uv run pytest -q
uv build
```

Routine tests use recorded API fixtures and cover download integrity, offline
behavior, discovery, configuration, paths, conversions, CLI errors, compiler failure,
rebuild reuse, rollback, recovery, and ownership. Opt-in tests discover actual local
Blender/Valve tools and check compiled model bounds, materials, and collision.

For full imports into the dedicated **cs2asset_validation** addon:

```powershell
uv run python scripts/validate_local.py
uv run python scripts/validate_local.py --offline
uv run python scripts/validate_local.py --offline --map
```

The first command downloads 1K validation assets if absent. `--map` converts a copy
of Valve's installed addon template with `dmxconvert`, assigns the imported material
and sky, adds the imported model, and builds it with the real compiler, GPU lighting,
visibility, navigation, and VPK packaging. It only writes the dedicated validation
addon. Evidence is recorded under `.validation/end-to-end`.

See [local validation](docs/validation.md), [model validation](docs/model-validation.md),
and [image validation](docs/image-validation.md) for evidence and remaining visual checks.

## Running without the repository environment

```powershell
uvx --from . cs2asset --version
uv tool install .
cs2asset doctor
```

The package uses a console entry point and includes the standalone Blender worker.
It has not been published to PyPI, so bare `uvx cs2asset` is not yet a release command.

Assets come from [Poly Haven](https://polyhaven.com/), whose assets are CC0. The live
API is identified in the CLI and all requests use `User-Agent: cs2asset/<version>`.
