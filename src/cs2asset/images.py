"""Image normalization; source images are left editable in the addon."""

import math
from pathlib import Path

import numpy as np
import OpenImageIO as oiio

from .errors import CS2AssetError


def read_image(path: Path) -> np.ndarray:
    config = oiio.ImageSpec()
    config.attribute("oiio:UnassociatedAlpha", 1)
    reader = oiio.ImageInput.open(str(path), config)
    if reader is None:
        raise CS2AssetError(f"Cannot read image {path}: {oiio.geterror()}")
    try:
        spec = reader.spec()
        if spec.depth > 1:
            raise CS2AssetError(f"Volume images are not supported: {path}")
        if spec.width * spec.height > 150_000_000:
            raise CS2AssetError(f"Image exceeds processing limit (150 megapixels): {path}")
        pixels = reader.read_image(format=oiio.FLOAT)
        if pixels is None:
            raise CS2AssetError(f"Cannot read pixels {path}: {reader.geterror()}")
        if not np.isfinite(pixels).all():
            raise CS2AssetError(f"Image contains non-finite values: {path}")
        return pixels
    finally:
        reader.close()


def write_image(path: Path, pixels: np.ndarray, *, hdr: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pixels = np.ascontiguousarray(pixels, dtype=np.float32)
    if pixels.ndim == 2:
        pixels = pixels[:, :, None]
    if (
        pixels.ndim != 3
        or any(size <= 0 for size in pixels.shape)
        or pixels.shape[2] not in {1, 2, 3, 4}
    ):
        raise CS2AssetError(f"Expected a nonempty image with 1-4 channels: {path}")
    if not np.isfinite(pixels).all():
        raise CS2AssetError(f"Cannot write non-finite pixels: {path}")
    if hdr and (pixels.min() < 0 or pixels.max() > 65504):
        raise CS2AssetError("HDR values must be between 0 and 65504 for half-float sky sources")
    out = oiio.ImageOutput.create(str(path))
    if out is None:
        raise CS2AssetError(f"Cannot create image {path}: {oiio.geterror()}")
    spec = oiio.ImageSpec(
        pixels.shape[1], pixels.shape[0], pixels.shape[2], oiio.HALF if hdr else oiio.UINT16
    )
    # Keep alpha unassociated: normal/data channels must never be premultiplied.
    spec.attribute("oiio:UnassociatedAlpha", 1)
    try:
        if not out.open(str(path), spec) or not out.write_image(pixels):
            raise CS2AssetError(f"Cannot write image {path}: {out.geterror()}")
    finally:
        out.close()
    return path


def linear_to_srgb(pixels: np.ndarray) -> np.ndarray:
    return np.where(
        pixels <= 0.0031308, pixels * 12.92, 1.055 * np.maximum(pixels, 0) ** (1 / 2.4) - 0.055
    )


def rotate_panorama(pixels: np.ndarray, yaw: float) -> np.ndarray:
    """Positive yaw shifts panorama features right, with periodic interpolation."""
    if not math.isfinite(yaw):
        raise CS2AssetError("Sky yaw must be a finite number")
    shift = (yaw % 360) / 360 * pixels.shape[1]
    whole = int(np.floor(shift))
    fraction = shift - whole
    return (
        np.roll(pixels, whole, axis=1) * (1 - fraction)
        + np.roll(pixels, whole + 1, axis=1) * fraction
    )


def normalize_map(source: Path, target: Path, role: str, *, normal_format: str = "gl") -> Path:
    if normal_format not in {"gl", "dx"}:
        raise CS2AssetError("Normal convention must be 'gl' or 'dx'")
    if role not in {"base_color", "normal", "roughness", "metalness", "ao", "opacity", "height"}:
        raise CS2AssetError(f"Unknown texture map role: {role}")
    pixels = read_image(source)
    if role == "normal":
        if pixels.shape[2] < 3:
            raise CS2AssetError(f"Normal map needs RGB channels: {source}")
        pixels = pixels[:, :, :3].copy()
        # Author GL normals by default; convert explicitly declared DX inputs once.
        # A rendered directional fixture is still required for visual acceptance.
        if normal_format == "dx":
            pixels[:, :, 1] = 1 - pixels[:, :, 1]
    elif role == "base_color":
        pixels = pixels[:, :, :1] if pixels.shape[2] == 2 else pixels[:, :, :3]
        if pixels.shape[2] == 1:
            pixels = np.repeat(pixels, 3, axis=2)
        if source.suffix.lower() in (".exr", ".hdr"):
            pixels = linear_to_srgb(pixels)
    else:
        pixels = pixels[:, :, :1]
    return write_image(target, np.clip(pixels, 0, 1))
