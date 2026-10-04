# Source-independent imports and Hammer blend materials

Plan based on repository and installed-tool inspection on 2026-10-04.
The refactor and blend importer described here have not been implemented.

## Goal and current baseline

Make `cs2asset` a CLI that converts common textures, HDR skies, and static models
into CS2 Hammer resources. Local assets and online providers resolve into shared
normalized inputs; shared converters generate Source 2 sources; Valve compiles
them; the existing installer publishes editable and compiled outputs.

Preserve the tested Poly Haven implementation and existing cache, staging,
compiler, ownership, recovery, manifests, discovery, and validation infrastructure.

Inspection established:

- `uv run pytest -q`: **198 passed, 3 skipped**. Skipped tests require opt-in real
  Blender/Valve integration; they were not run during this planning pass.
- `provider.py` already separates Poly Haven API/file resolution from conversion.
- `materials.py:create_material` accepts semantic texture maps as local paths.
  `create_sky` accepts a local HDR/EXR path. Reuse these validated converters.
- `models.py:export_model` and `blender_worker.py` already handle `.blend`, `.fbx`,
  `.gltf`, `.glb`, and `.obj`. Local model work primarily concerns exposing this
  pipeline and reliably resolving/snapshotting dependencies.
- Provider coupling is concentrated in `pipeline.py` and `cli.py`: provider
  construction, downloading, resource names, provenance, dimensions in millimetres,
  and rebuild selection using `parse_asset_id`.
- `installer.py` uses a schema-1 envelope, output hashes, ownership checks, locks,
  transaction journals, and rollback. Keep these mechanisms.
- Existing real-tool evidence is in `docs/validation.md`, `docs/model-validation.md`,
  and `docs/image-validation.md`. Their outstanding visual checks remain outstanding.

## Blend support: verified presence, unverified authoring contract

The installed CS2 Steam build is **25687242**. Read-only local inspection found:

- `game/csgo/tools/met/met_shaderconfig.kv3` lists `csgo_environment_blend` in the
  Environment category.
- `game/csgo/shaders_pc_dir.vpk` contains compiled shader entries for
  `csgo_environment_blend`, `csgo_simple_2way_blend`, and
  `csgo_lightmapped_4wayblend`, alongside `csgo_complex`.
- `game/csgo/tools/import_settings.txt` supplies the existing `csgo_complex.vfx`
  texture import mapping, but does not establish a complete blend material contract.

Blend shaders are shipped with these tools. Their presence alone does not establish
correct editable VMAT fields, blend-weight channels, or successful Hammer painting.
The implementation must verify those locally. Attempts to access the relevant Valve
Developer Community documentation returned inaccessible/403 responses.

## 1. Introduce normalized interfaces around existing code

Add small `assets/` and `sources/` packages. Keep compiler, installer, discovery,
image processing, material generation, and model export modules in place initially.
Extract orchestration into `converters/` where it makes the boundary clearer; avoid
a broad directory reshuffle. Preserve existing provider imports through compatibility
exports if useful.

| Interface | Responsibility |
| --- | --- |
| `SourceRef` | Provider key, canonical URI, optional provider ID, name, license/provenance, and provider-specific extensions. |
| `ResolvedAsset` | Kind, available/selected files, dependency-relative paths, choices, warnings, and dimensions with explicit units; usable before materialization. |
| `MaterialInput` | Local maps with canonical semantics, normal convention, known packed-channel layouts, constants, and optional physical dimensions. |
| `ModelInput` | Local primary model, dependency map, format, unit/scale information, and supplied LOD information where known. |
| `SkyInput` | Local floating-point panorama and known projection/color information. |
| `NormalizedAsset` | Typed material/model/sky input, provenance, verified input hashes, and normalization decisions; no provider or HTTP client. |
| `ConversionResult` | Generated source resources, output hashes, warnings, and details for the existing compile/install lifecycle. |

Providers implement `resolve` and `materialize`; search is a separate optional
capability. Resolution describes/selects inputs. Materialization returns verified
local inputs. A Poly Haven adapter delegates to the existing provider and
`DownloadCache`; local providers read and snapshot files without networking.

Choose the provider once in a source router. Conversion dispatches by asset kind,
not source. Providers handle dependencies and source naming. Image normalization,
known packed-map unpacking, Blender geometry/material conversion, and Source 2
generation remain generic. Provider metadata can describe channel layouts but
cannot contain shader generation logic.

## 2. Add local inference and generalize the CLI

Keep `import` as the main command:

| Input | Interpretation |
| --- | --- |
| `polyhaven:ID` or supported Poly Haven asset URL | Type from Poly Haven metadata. |
| Existing legacy bare Poly Haven ID | Preserve the alias when unambiguously an ID. |
| `.blend`, `.glb`, `.gltf`, `.fbx`, `.obj` | Static model. |
| `.hdr`, `.exr` | HDR sky; validate panorama layout during conversion. |
| Directory with one recognizable PBR texture set | Material. |
| Local material `.zip` | Safely inspect/materialize one recognizable texture set. |

Recognize Windows absolute/UNC paths and explicit relative paths before URI schemes.
Existing local paths take precedence over bare-ID aliases. Missing path-like inputs
produce a local missing-path error instead of a Poly Haven API call. Reject unknown
provider schemes explicitly.

Add `--type material|model|sky` for ambiguous intent; errors explain incompatible
inputs. Dedicated import commands are optional aliases, not separate pipelines.
Generalize `info`, progress, JSON plans, and `doctor`. Add `search --provider
polyhaven` while preserving today's default search. Local providers need no search.

Keep local resolution native by default. An explicit `--resolution` caps image size
with aspect-preserving reduction and no upscaling; report actual dimensions. Preserve
Poly Haven resolution selection/defaults and all existing import settings.

Dry-run resolves local type, semantics, selected inputs, settings, and intended paths
without snapshots, build/stage/addon/manifest writes, Blender, or Valve. Report model
checks that need normalization as pending. Preserve existing remote metadata-only
dry-run behavior.

## 3. Resolve conventional PBR sets

Create a source-independent semantic filename matcher:

| Semantic | Aliases |
| --- | --- |
| `base_color` | `basecolor`, `base_color`, `albedo`, `diffuse`, `color`; retain Poly Haven `diff`. |
| `normal` | `normal`, `normalgl`, `normaldx`, `nor_gl`, `nor_dx`, `nrm`. |
| `roughness` | `roughness`, `rough`. |
| `metalness` | `metallic`, `metalness`, `metal`. |
| `ao` | `ao`, `ambient_occlusion`. |
| `height` | `height`, `displacement`, `disp`. |
| `opacity` | `opacity`, `alpha`. |

Match case-insensitive tokens/suffixes, including compound aliases, rather than
arbitrary substrings. Ignore unrelated files. Poly Haven's known role mappings are
authoritative inputs to this same normalized material type.

Require a recognizable set for directory inference. Multiple sets or duplicate map
candidates produce an error listing filenames and how to split the folder or select
maps explicitly. Add per-map overrides where necessary instead of guessing.

Infer GL/DX only from explicit naming/metadata. Plain `normal` uses the existing GL
default with a recorded assumption; honor `--normal-format`. Preserve existing
defaults, alpha extraction/testing, color handling, roughness packing, AO, metalness,
and height retention. Unpack only known or explicitly specified channel layouts.
Displacement and procedural shader conversion remain outside scope.

Limit ZIP support to local texture sets. Validate traversal, links, case-insensitive
duplicate destinations, and extraction limits before copying into the tool cache.
Do not extract into the user's source folder.

## 4. Reuse the model and sky pipelines

Run all normalized models through the existing headless Blender exporter, shared
material converter, and ModelDoc writer. Preserve axes, physical units, scale,
transforms, supplied LODs, and hull/none collision. Report unknown-unit assumptions.

Resolve glTF buffers/textures, OBJ/MTL files, FBX textures, and Blender external
images. When necessary, use a headless source-inspection helper during resolution;
it contains no Source 2 logic. Snapshot the consumed dependency graph and remap
references to those copies during conversion, including discovered absolute paths.
Do not mutate user model files. Hash every consumed dependency so texture changes
invalidate model conversion. Missing-file errors name the source and reference.

Retain actionable unsupported-material-graph errors and validate UV requirements.
The existing worker evaluates rigged inputs as static meshes with a warning; preserve
that behavior without adding skeleton/animation export. Validate each advertised
format using representative static, image-based assets, not just suffix tests.

Route HDR and EXR through the existing `create_sky`: retain floating-point values,
RGB 2:1 equirectangular validation, yaw/exposure, `SkyTexture`, and Valve-generated
cubemap resources. Reject incompatible panorama layouts clearly.

## 5. Generalize provenance, identity, cache, and rebuild

New records contain generic provenance:

```json
{
  "record_version": 2,
  "source": {
    "provider": "polyhaven",
    "id": "dirty_football",
    "uri": "polyhaven:dirty_football",
    "extensions": {}
  }
}
```

Local records use `provider: local` and an absolute canonical source path. Local
license is unknown unless supplied; do not inherit Poly Haven licensing. Preserve
authors, source URL, hashes, choices, dimensions with units, tools, logs, resources,
and ownership. Retain legacy Poly Haven fields as compatibility aliases where useful.

Keep the installer's schema-1 envelope and add record versions. Read legacy records
through an in-memory adapter derived from their Poly Haven ID/provenance. Reads do
not rewrite manifests. Save additions only through successful existing atomic
installation transactions. Failed rebuilds preserve the old record and outputs.

Preserve Poly Haven import IDs, variant calculations, and output namespaces exactly.
Local resource directories use a readable slug plus a short hash of canonical source
identity under a local namespace. Source identity is independent of file contents,
so edits rebuild in place. Distinguish local/provider names and same-named folders;
detect case-insensitive resource/hash collisions before publication.

Separate stable source identity, settings/variant identity, and build fingerprint.
Fingerprints include input content hashes, dependency maps, settings, and relevant
converter/tool versions. Verify local inputs before conversion-cache reuse; size and
mtime alone are insufficient. Preserve existing verified download and converted
output caches, locks, staging ownership, and compiler dependency checks. Converter
changes may invalidate conversion cache without deleting downloads or moving outputs.

Rebuild selects by canonical source or installed import ID, retaining variant
filtering and saved effective settings. Local rebuild rereads current inputs. A
missing source fails clearly and preserves installed resources. Add explicit
`--cached-inputs` for rebuilding retained verified snapshots; never silently rebuild
stale local sources. Preserve offline Poly Haven rebuilds.

## 6. Validate the blend contract before generating materials

Start this feasibility work early; the generic importer does not depend on it.

1. Use the installed Material Editor to create/save a minimal two-layer Environment
   Blend material. Inspect installed examples with Valve tools where helpful.
2. Record the actual shader, VMAT fields, layer/default texture semantics, import
   settings, normal convention, packing, blend-weight channels, and mesh requirements.
   Establish its relationship to `csgo_complex`; do not invent numbered parameters.
3. Compile two distinctive synthetic layer sets with Valve. Inspect the compiled
   material and child resources to prove both layers survived. Success status alone
   can conceal ignored fields or default textures.
4. Apply it to a dedicated Hammer mesh, paint each layer and a transition, then
   compile/render a small test map. Verify weight channels, normals, roughness,
   orientation, transitions, and any required subdivision/attributes.
5. Save a minimal project-owned template and contract/build evidence in
   `docs/blend-validation.md`. Track compiler/data and visual acceptance separately.
   Do not redistribute Valve's shipped material assets as fixtures.

The architecture is:

```text
source A -> resolve/materialize -> MaterialInput A --+
                                                   +-> BlendInput -> blend converter
source B -> resolve/materialize -> MaterialInput B --+                       |
                                                                           v
                                                            existing compile/install
```

Add `BlendInput` with two ordered normalized materials, both provenance records,
layer options, and contract version. Reuse normal material resolution and texture
normalization. Ordinary and blend generators consume the same semantics but map
them according to their respective validated shaders. No standalone layer imports
are required before creating the blend.

Add `cs2asset blend create A B --name forest_rock` with existing project, resolution,
normal-format, dry-run, and output controls where applicable. Support local/local,
Poly Haven/Poly Haven, and mixed inputs. Reject model/sky layers. Start with two
opaque PBR layers; cutout, height transitions, and more layers require separate proof.

Recipe identity includes ordered input identities and settings. Build fingerprints
include both input hash graphs and contract version. Record ordered provenance
under `sources`, recipe details, resources, and ownership in one import record.
Same-named different recipes coexist rather than overwrite. Rebuild restores the
recipe and re-resolves both inputs.

If the contract cannot be established, deliver normalized inputs, recipe planning,
provenance, and capability diagnostics; generation stays disabled with a precise
validation-pending error. If compilation works but interactive painting remains
unchecked, expose only explicitly experimental support. Do not claim Hammer-ready
acceptance or invent VMAT fields to satisfy mocks.

## 7. Milestones and verification

| Milestone | Deliverable and acceptance |
| --- | --- |
| A: compatibility boundary | Normalized types, router, provider interfaces, Poly Haven adapter; existing behavior, IDs, paths, and ownership preserved. Begin blend inspection. |
| B: local materials/skies | Inference, semantic matching, snapshots, hashes, generic plans and dry-run. Real Valve checks for a local material and HDR/EXR skies. |
| C: local models | Dependency snapshot/remapping through the current exporter; representative validation for all advertised formats and useful errors. |
| D: provenance/rebuild | Legacy adapter, generic records, collision-safe namespaces, local/provider and cached-input rebuilds; rollback and ownership regressions. |
| E: blends | Actual contract probe, shared two-input recipes, CLI and mixed-source coverage to the verified extent; explicit outstanding validation. |
| F: delivery | README/package/help/capability updates, routine tests, real-tool regressions, packaging, and Hammer visual acceptance where available. |

Add meaningful tests for:

- Local material directories, all alias families, missing optional maps, duplicate
  candidates, normal conventions, known packing, and safe/invalid archives.
- Local HDR and EXR, invalid projection, and HDR-value preservation.
- Local models, external dependencies, embedded images, LODs, collision, source
  edits, unsupported graphs, missing textures, and UV validation.
- Existing fixture-backed Poly Haven material/model/sky imports, offline caching,
  dependency selection, and unavailable-resolution errors through the adapter.
- Equivalent normalized inputs producing equivalent resource content independent
  of provenance, accounting for expected resource-path differences.
- Generic provenance, legacy reads/rebuilds, saved options/variants, local rebuild,
  explicit snapshot rebuild, and failed rebuild preserving installed resources.
- Local/provider name collisions, same-named local folders, Windows path variants,
  source edits rebuilding in place, and user-edited/shared output protections.
- Useful errors for missing paths, unsupported inputs/providers, ambiguous sets,
  missing model dependencies, and non-material blend inputs.
- Local dry-run with no networking, Blender/Valve, snapshots, staging, addon writes,
  or manifest changes, and truthful reporting of pending normalization checks.
- Blend layer order, both provenance graphs, mixed sources, recipe rebuild,
  same-name recipes, and dry-run; shader-field assertions only after validation.
- Real blend compilation and Hammer painting/rendering as distinct checks.

Run `uv run pytest -q` and `uv run ruff check .` at each milestone. Run opt-in
integration tests with `CS2ASSET_INTEGRATION=1` after pipeline-affecting milestones,
using dedicated validation addons. Extend `scripts/validate_local.py` for local
inputs and verified blends. Finish with `uv build` and entry-point verification.

## README positioning and completion criteria

Describe generic conversion first; introduce Poly Haven as the first online provider.
Update the package description and provider-specific CLI help. Show local examples
before provider examples; document inference, normal/scale assumptions, resolution,
snapshots, provenance, rebuild behavior, and blend validation status.

```powershell
cs2asset import .\assets\mossy_rock.glb
cs2asset import .\assets\forest_ground\
cs2asset import .\assets\sunset.hdr

cs2asset import polyhaven:dirty_football
cs2asset import polyhaven:concrete_floor_01 --resolution 2k
cs2asset import polyhaven:sunset_jhbcentral --resolution 4k
cs2asset search "mossy rock" --provider polyhaven

cs2asset blend create .\materials\forest_ground .\materials\rock --name forest_rock
cs2asset blend create polyhaven:forest_ground_04 polyhaven:rock_face_03 --name forest_rock
```

Publish blend usage as supported only to the validated extent. The refactor is
complete when local/provider assets reach the same converters and preserve caching,
transactional installation, ownership, and rebuild. Blend support is complete when
its actual authoring contract and Hammer painting workflow pass validation;
otherwise document the exact remaining checks and expose that limit in capabilities.
