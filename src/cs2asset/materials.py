"""CS2 material source generation validated with Valve's resourcecompiler."""

import json
import math
from pathlib import Path

import numpy as np

from .errors import CS2AssetError
from .images import linear_to_srgb, normalize_map, read_image, rotate_panorama, write_image
from .resource_names import resource_name

TEMPLATE_VERSION = 2


def write_vmat(destination: Path, fields: dict) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = ['"Layer0"', "{"]
    for key, value in fields.items():
        lines.append(f"    {key} {json.dumps(str(value))}")
    lines.append("}")
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


MATERIAL_PARAMETERS = {
    "base_color": "TextureColor",
    "normal": "TextureNormal",
    "roughness": "TextureRoughness",
    "metalness": "TextureMetalness",
    "ao": "TextureAmbientOcclusion",
    "opacity": "TextureTranslucency",
}


def prepare_material_maps(destination, maps, *, normal_format="gl", constants=None):
    """The shared image/constant normalization used by ordinary and blend materials."""
    constants = constants or {}
    destination.mkdir(parents=True, exist_ok=True)
    warnings, prepared = [], {}
    defaults = {
        "base_color": constants.get("base_color", [0.5, 0.5, 0.5]),
        "normal": [0.5, 0.5, 1.0],
        "roughness": [constants.get("roughness", 0.5)],
        "metalness": [constants.get("metalness", 0.0)],
        "ao": [1.0],
    }
    maps = dict(maps)
    if "opacity" not in maps and "base_color" in maps:
        color = read_image(maps["base_color"])
        if color.shape[2] in {2, 4} and np.any(color[:, :, -1] < 0.999):
            maps["opacity"] = write_image(destination / "source_alpha.png", color[:, :, -1:])
    if constants.get("opacity", 1.0) < 1.0 and "opacity" not in maps:
        maps["opacity"] = write_image(
            destination / "source_alpha.png", np.full((4, 4, 1), constants["opacity"])
        )
    for role in MATERIAL_PARAMETERS:
        target = destination / f"{role}.png"
        if role in maps:
            normalize_map(maps[role], target, role, normal_format=normal_format)
        elif role in defaults:
            values = np.asarray(defaults[role], dtype=np.float32).reshape(-1)[:3]
            if role == "base_color" and "base_color" in constants:
                values = linear_to_srgb(values)
            write_image(target, np.broadcast_to(values, (4, 4, len(values))).copy())
            warnings.append(f"{role}: using constant {values.tolist()}")
        else:
            continue
        prepared[role] = target
    return prepared, warnings


def create_material(
    content: Path,
    resource_dir: str,
    maps: dict[str, Path],
    *,
    normal_format: str = "gl",
    constants: dict | None = None,
    surface: str = "default",
    tiling: float = 1.0,
    name: str = "material",
) -> tuple[Path, list[str]]:
    """Write a named material and normalized maps using the validated CS2 complex contract."""
    if not math.isfinite(tiling) or tiling <= 0:
        raise CS2AssetError("Material tiling must be a finite positive number")
    destination = content / resource_dir
    prepared, warnings = prepare_material_maps(
        destination,
        maps,
        normal_format=normal_format,
        constants=constants,
    )
    fields = {
        "shader": "csgo_complex.vfx",
        "F_SPECULAR": 1,
        "F_METALNESS_TEXTURE": 1,
        "PhysicsSurfaceProperties": surface,
        "g_vTexCoordScale": f"[{tiling} {tiling}]",
        **{
            MATERIAL_PARAMETERS[role]: path.relative_to(content).as_posix()
            for role, path in prepared.items()
        },
    }
    if "opacity" in prepared:
        fields["F_ALPHA_TEST"] = 1
        fields["g_flAlphaTestReference"] = 0.5
        warnings.append("Opacity uses alpha testing (cutout), not blended transparency")
    if "height" in maps:
        warnings.append("Height map is retained in the input cache; displacement is not enabled")
    return write_vmat(destination / f"{resource_name(name)}.vmat", fields), warnings


def create_sky(
    content: Path,
    resource_dir: str,
    source: Path,
    *,
    yaw: float = 0,
    exposure: float = 0,
    auto_exposure: bool = False,
    name: str = "sky",
) -> tuple[Path, dict]:
    if not math.isfinite(exposure) or not -32 <= exposure <= 32:
        raise CS2AssetError("Sky exposure must be finite and between -32 and 32 stops")
    if source.suffix.lower() not in {".hdr", ".exr"}:
        raise CS2AssetError("Sky source must be an HDR/EXR panorama, not a tonemapped preview")
    pixels = read_image(source)[:, :, :3]
    h, w = pixels.shape[:2]
    if w != 2 * h or pixels.shape[2] != 3:
        raise CS2AssetError("Sky input must be an RGB 2:1 equirectangular HDR panorama")
    minimum, maximum = float(pixels.min()), float(pixels.max())
    negative_count = int(np.count_nonzero(pixels < 0))
    # A small absolute tolerance handles numerical noise without hiding invalid radiance.
    if minimum < -0.001:
        raise CS2AssetError(
            f"Sky contains negative radiance: minimum={minimum:.6g}, maximum={maximum:.6g}, "
            f"negative components={negative_count}. Repair the source HDR image."
        )
    pixels = rotate_panorama(np.maximum(pixels, 0), yaw) * (2.0**exposure)
    adjustment = 0.0
    peak = float(pixels.max())
    if peak > 65504:
        adjustment = math.log2(65504 / peak)
        if not auto_exposure:
            raise CS2AssetError(
                f"Sky exceeds half-float range: minimum={float(pixels.min()):.6g}, "
                f"maximum={peak:.6g}, overflowing components={int(np.count_nonzero(pixels > 65504))}. "
                f"Use --exposure {math.floor((exposure + adjustment) * 1e6) / 1e6:.6f} "
                "or --auto-exposure."
            )
        pixels = pixels * (65504 / peak)

    output = content / resource_dir
    write_image(output / "sky.exr", pixels, hdr=True)
    # SkyTexture (not TextureSky) makes Valve generate the HDR cubemap and SH data.
    material = write_vmat(
        output / f"{resource_name(name)}.vmat",
        {
            "shader": "sky.vfx",
            "SkyTexture": f"{resource_dir}/sky.exr",
            "g_flBrightnessExposureBias": 0.0,
            "g_flRenderOnlyExposureBias": 0.0,
        },
    )
    return material, {
        "panorama_width": w,
        "panorama_height": h,
        "expected_face_size": w // 4,
        "maximum_radiance": float(pixels.max()),
        "yaw": yaw,
        "exposure": exposure,
        "exposure_adjustment": adjustment,
        "effective_exposure": exposure + adjustment,
        "source_minimum_radiance": minimum,
        "source_maximum_radiance": maximum,
        "clamped_negative_components": negative_count,
    }
