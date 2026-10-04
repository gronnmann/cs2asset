"""Two-layer environment blend contract verified with CS2 build 25687242.

Compiler and texture data are verified; interactive Hammer painting remains pending.
See docs/blend-validation.md. No ordinary-material feature flags are invented here.
"""

from pathlib import Path

import numpy as np

from cs2asset.assets import BlendInput, MaterialInput
from cs2asset.errors import CS2AssetError
from cs2asset.images import normalize_map, read_image, write_image
from cs2asset.materials import MATERIAL_PARAMETERS, prepare_material_maps, write_vmat
from cs2asset.resource_names import resource_name

BLEND_CONTRACT_VERSION = "csgo_environment_blend-25687242-v1"
BLEND_WARNING = (
    "Experimental environment blend: Valve compilation and layer texture packing are verified; "
    "interactive Hammer painting, visible normal direction, and runtime transitions remain unverified."
)


def create_blend(
    content: Path,
    resource_dir: str,
    blend: BlendInput,
    *,
    maps,
    normal_format=None,
    surface="default",
):
    fields = {
        "shader": "csgo_environment_blend.vfx",
        "PhysicsSurfaceProperties": surface,
        "PhysicsSurfaceProperties2": surface,
    }
    warnings = [BLEND_WARNING]
    for index, (layer, source_maps) in enumerate(zip(blend.layers, maps, strict=True), 1):
        if not isinstance(layer.input, MaterialInput):
            raise CS2AssetError("Environment blend inputs must both be normalized materials")
        destination = content / resource_dir / f"layer{index}"
        prepared, notes = prepare_material_maps(
            destination,
            source_maps,
            normal_format=normal_format or layer.input.normal_format,
            constants=layer.input.constants,
        )
        if "opacity" in prepared and np.any(read_image(prepared["opacity"]) < 0.999):
            raise CS2AssetError(
                f"Blend layer {index} contains opacity/cutout. The validated blend contract supports opaque layers."
            )
        for role, path in prepared.items():
            if role != "opacity":
                fields[MATERIAL_PARAMETERS[role] + str(index)] = path.relative_to(
                    content
                ).as_posix()
        height = destination / "height.png"
        if "height" in source_maps:
            normalize_map(source_maps["height"], height, "height")
            warnings.append(
                f"Layer {index}: height is supplied to Valve's environment blend texture; transition controls use shader defaults."
            )
        else:
            write_image(height, np.full((4, 4, 1), 0.5))
        fields[f"TextureHeight{index}"] = height.relative_to(content).as_posix()
        warnings.extend(f"Layer {index}: {note}" for note in layer.resolved.warnings)
        warnings.extend(f"Layer {index}: {note}" for note in notes)
    return write_vmat(content / resource_dir / f"{resource_name(blend.name)}.vmat", fields), warnings
