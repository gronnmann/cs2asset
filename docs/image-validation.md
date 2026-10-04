# Image and material validation

On 2026-10-04, OpenImageIO processing and CS2 build 25687242 were tested with
directional normal, scalar roughness, and straight-alpha fixtures in the dedicated
`cs2asset_model_probe` addon.

`tests/test_images.py` and `tests/test_materials.py` cover floating-point EXR values
above 1, half-float range limits, fractional panorama yaw and seam wrapping,
linear-to-sRGB conversion, linear scalar maps, optional-map constants, normal
green inversion, RGBA and grayscale-alpha cutouts, explicit opacity precedence,
sky exposure, input shape checks, and rejection of tonemapped sky previews.

An actual defect was found and fixed: OpenImageIO's default PNG reader associated
RGB with alpha, changing `[0.8,0.4,0.2,0.25]` into `[0.2,0.1,0.05,0.25]`.
`read_image` now requests `oiio:UnassociatedAlpha=1`; output already requests the
same setting. Template version 2 invalidates conversions made before this fix.

The actual `csgo_complex.vfx` material was compiled using Valve's resource compiler.
Valve's `resourceinfo.exe -all` confirms `F_ALPHA_TEST=1` and that
`LightSim_Opacity_A` uses the compiled color texture's alpha. Its texture extraction
command reports:

| Input | Extracted compiled pixel |
| --- | --- |
| Base color `[0.8,0.4,0.2]`, opacity `0.25` | `[0.800,0.400,0.200,0.25098]` |
| Normal `[0.5,0.8,0.9]`, roughness `0.35` | `[0.7137,0.2902,0.3490,1]` |

The normal compiler uses its `HemiOctIsoRoughness_RG_B` processing path: the
extracted normal is encoded, while roughness is visibly retained in B. The
application supplies separate normal/roughness sources and leaves Valve in charge
of this packing. This inspection verifies compiled dependencies, alpha, and
roughness data; it **does not establish the visible bump direction** for CS2's
shader. An interactive directional-light fixture in Hammer remains required.
The importer defaults to GL source normals and can invert explicitly selected DX
inputs with `--normal-format dx`.

Raw local validation logs and extracted fixtures are under
`.validation/models/material_probe/` and are excluded from source control.

Run the image/material unit checks with:

```powershell
uv run pytest tests/test_images.py tests/test_materials.py -q
```

Source: [OpenImageIO ImageInput configuration](https://github.com/AcademySoftwareFoundation/OpenImageIO/blob/main/src/doc/imageinput.md).
