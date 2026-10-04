# CS2 environment blend validation

Compiler and data checks were performed locally on 2026-10-04 with CS2 Steam build
**25687242**, Valve's installed `resourcecompiler.exe` and `resourceinfo.exe`, and
OpenImageIO 3.1.18.1. Interactive Hammer painting and runtime visual acceptance
have **not** been verified. The CLI exposes this feature as experimental.

## Installed evidence

`game/csgo/tools/met/met_shaderconfig.kv3` lists `csgo_environment_blend` in the
Environment shader category. The installed shader archive contains this shader
and other blend variants. Valve's reader can inspect shipped resources directly
through the game's VPK search paths:

```powershell
resourceinfo.exe -game '<CS2>/game/csgo' -i materials/cs_italy/ground/herringbone_tile_blend_01.vmat_c -all
```

This stock material uses `csgo_environment_blend.vfx`, two layer texture groups,
and `VertexPaintBlendParams`/`VertexPaintTintColor` input semantics. Its metadata
advertises `VertexPaintUI2Layer`, `VertexPaintUIAlphaBlendFactor`, and
`MaterialLayerRefType = csgo_environment`. Other shipped blend materials use
`csgo_simple_2way_blend.vfx` or legacy `csgo_lightmappedgeneric.vfx`; their fields
must not be treated as interchangeable with environment blend fields.

A shader-only VMAT compiled successfully but used default textures for both layers.
That demonstrates why compile success alone cannot establish correct bindings.

## Verified authoring subset

A second probe supplied unique source images and distinctive scalar values for
both layers. The following authoring parameters were confirmed by compiler input
dependencies, compiled material bindings, and Valve texture extraction:

| Semantic | Layer 1 / layer 2 fields | Verified compiled representation |
| --- | --- | --- |
| Base color | `TextureColor1`, `TextureColor2` | `g_tColor1/2`, RGB |
| AO | `TextureAmbientOcclusion1`, `TextureAmbientOcclusion2` | Color texture A |
| Normal | `TextureNormal1`, `TextureNormal2` | `g_tNormal1/2`, Valve normal encoding |
| Roughness | `TextureRoughness1`, `TextureRoughness2` | Normal texture B |
| Height | `TextureHeight1`, `TextureHeight2` | `g_tHeight1/2`, R |
| Metalness | `TextureMetalness1`, `TextureMetalness2` | Height texture A |

Valve owns the packing, normal encoding, mip generation, and BC7 compilation.
The application supplies separate normalized source maps. Source images remain
editable. The two-layer shader's default feature/painting configuration is retained;
no guessed `F_BLEND` or numbered `csgo_complex` feature flags are emitted.

Observed extracted pixels from the disposable parameter probe:

| Texture | Layer 1 | Layer 2 |
| --- | --- | --- |
| Color RGB + AO | `[0.902, 0.098, 0.098, 0.600]` | `[0.098, 0.098, 0.800, 0.902]` |
| Normal encoding + roughness | `[0.502, 0.502, 0.200, 1]` | `[0.502, 0.502, 0.800, 1]` |
| Height + defaults + metalness | `[0.298, 1, 0, 0.098]` | `[0.600, 1, 0, 0.702]` |

The resulting material advertises `MaterialLayerCount = 2`,
`VertexPaintUI2Layer = 1`, `VertexPaintUI3Layer = 0`, and
`VertexPaintUIAlphaBlendFactor = 1`; its input signature contains
`VertexPaintBlendParams`. This verifies the shader's paint-related metadata, not an
actual painted mesh or render.

Raw probe logs/extractions are retained under `.validation/blend-contract/` and the
dedicated `cs2asset_blend_probe` addon. These are generated evidence, not distributed
Valve source assets. The committed real-tool regression reproduces the generated
converter's two layers and checks their packed values:

```powershell
$env:CS2ASSET_INTEGRATION = '1'
uv run pytest tests/test_local_integration.py -q
```

## Supported extent and remaining checks

The implementation accepts two opaque normalized PBR materials from local sources,
Poly Haven, or a mix, and uses the existing compiler/transactional installer. It
records ordered provenance, input hashes, recipe settings, contract version, editable
outputs, compiled children, and rebuild snapshots. Missing optional maps use the
same normalization/defaults as ordinary materials; absent height uses 0.5.

Remaining acceptance checks are applying the generated material in Hammer,
inspecting the paint tools and weight-channel behavior on a suitable mesh, painting
both layers and a transition, and compiling/rendering a test map to assess normals,
roughness, and height/default transitions. Mesh subdivision requirements and visible
normal convention also need this interactive check. Cutout, custom UV/transition
controls, and more than two layers require separate validation.

The verified contract is versioned as `csgo_environment_blend-25687242-v1`. Tools
updates can require revalidation; existing import fingerprints include compiler and
converter identities so regenerated resources rebuild in place.
