# Local end-to-end validation

Validated on Windows on 2026-10-04 with CS2 Steam build **25687242**, Valve's
`resourcecompiler.exe`, Blender 5.1.2 (also 4.3.2 in model probes), and
OpenImageIO 3.1.18.1 on Python 3.12.9.

Actual Steam discovery found the installation through its registry entry,
`libraryfolders.vdf`, and `appmanifest_730.acf`. Actual addon discovery found
`de_campsite`, `de_frostlodge`, and `forest_1v1`, plus Valve examples. All writes
for validation used dedicated tool addons, primarily `cs2asset_validation`.

| Asset | Actual validation |
| --- | --- |
| `concrete_floor_01`, 1K | API-resolved lossless maps and verified downloads; normalized PBR sources; real material/child-texture compilation; transactional installation. |
| `sunset_jhbcentral`, 1K | Actual floating-point EXR downloaded and verified; real sky material compiled to BC6H HDR cubemap; installed with generated texture. |
| `dirty_football`, 1K | Actual Blender scene/dependencies; headless export; material compilation; all four LODs; real model and convex hull compilation; installed sources/children. |

All three were rebuilt offline from verified downloads. Subsequent unchanged
imports reuse converted sources and receive Valve's explicit skipped/dependency
summary. Structured `list`, `doctor`, dry-run, search, and configuration commands
were exercised. Routine API tests use committed fixtures rather than live servers.
The final opt-in regression run passed **201 tests**, including real Blender and
Valve checks. Ruff, wheel/source-distribution builds, and `uvx --from .` passed.

The installed Valve material import settings establish the shader parameter names.
Minimal templates were checked through actual compilation, not guessed from binary
formats. The sky parameter is **SkyTexture**, not TextureSky. A synthetic 256×128
EXR with radiance 4 produced a 64×64 cubemap flagged `VTEX_FLAG_CUBE_TEXTURE`,
BC6H format, and reflectivity `(4,4,4,0)` in Valve's `resourceinfo` output.

The model probe established exact physical bounds, material references, PHYS
presence/absence, and LOD masks with Valve's reader. Image probes established
straight color/alpha preservation and roughness packing. Detailed evidence is in
the companion model and image documents.

## Real map build

`scripts/validate_local.py --offline --map` used Valve's own `dmxconvert.exe` to
convert a copy of the supplied addon-template map. The copy references the imported
concrete material, sky material, and football model. It is saved as:

```text
content/csgo_addons/cs2asset_validation/maps/cs2asset_validation.vmap
game/csgo_addons/cs2asset_validation/maps/cs2asset_validation.vpk
```

The actual compiler completed visibility, GPU ray-traced lighting on the RTX 3090,
navigation, physics/model dependencies, and VPK packaging in approximately 36 s.
Its summary was **23 compiled, 0 failed, 0 skipped**. The map package was approximately
16.6 MB. Logs show the football's geometry participating in the lightmap query.

Valve also prints missing optional detail-prop data, unsigned-package notices,
template lightmap-size warnings, and a leaked-KeyValues diagnostic. These did not
prevent the successful compilation; they are preserved in the full log, not hidden.

Repeatable evidence lives in `.validation/end-to-end/summary.json` and
`.validation/end-to-end/map-compile.log`. These generated files are excluded from
source control; the repeatable script and tests are committed repository files.

## Remaining acceptance checks

Compilation and data integrity are validated. An interactive Hammer inspection,
runtime render of the test map, visible normal-map bump direction, live asset-browser
refresh, gameplay collision, and LOD transitions have not been verified. No existing
Hammer/CS2 UI session was available during the probes, and no desktop automation
provider was used. The generated map is ready for those checks in Workshop Tools.

There is no missing external dependency blocking the implemented import pipeline
on this machine. Unsupported material graphs and later-scope features are listed
explicitly in README.md. The release should not claim the plan's visual acceptance
criterion is complete until the interactive checks above pass.
