"""Source providers resolve and materialize assets; they do not generate resources."""

from pathlib import Path
from typing import Protocol

from cs2asset.assets import NormalizedAsset, ResolvedAsset
from cs2asset.cache import safe_relative_path
from cs2asset.errors import CS2AssetError


def input_path(root: Path, relative: str) -> Path:
    """Contain source dependencies while retaining spaces and Unicode filenames."""
    root = root.resolve()
    path = root / safe_relative_path(relative)
    if not path.resolve().is_relative_to(root):
        raise CS2AssetError(f"Source dependency escapes {root}: {relative}")
    return path


class AssetProvider(Protocol):
    def resolve(
        self,
        value: str,
        resolution: str | None = None,
        *,
        refresh: bool = False,
        asset_type: str | None = None,
    ) -> ResolvedAsset: ...

    def prepare(self, resolved: ResolvedAsset, *, blender: Path | None = None) -> ResolvedAsset:
        """Complete dependency discovery before computing a build fingerprint."""
        ...

    def materialize(self, resolved: ResolvedAsset, destination: Path) -> NormalizedAsset: ...


def validate_kind(resolved: ResolvedAsset, requested: str | None) -> ResolvedAsset:
    aliases = {"textures": "material", "models": "model", "hdris": "sky"}
    requested = aliases.get(requested, requested)
    if requested not in {None, "material", "model", "sky"}:
        raise CS2AssetError("Asset type must be material, model, or sky")
    if requested and requested != resolved.asset.kind:
        raise CS2AssetError(
            f"Expected {requested}, but {resolved.source.uri} resolves to {resolved.asset.kind}"
        )
    return resolved


def normalized(resolved, paths, hashes, *, snapshot=None, remap=None):
    from cs2asset.assets import MaterialInput, ModelInput, SkyInput

    if resolved.asset.kind == "material":
        payload = MaterialInput(
            paths,
            "dx" if resolved.choices.get("normal_convention") == "directx" else "gl",
            packed_layouts={"arm": ("ao", "roughness", "metalness")} if "arm" in paths else {},
        )
    elif resolved.asset.kind == "model":
        payload = ModelInput(paths["model"], paths, remap or {})
    else:
        payload = SkyInput(paths["hdri"])
    return NormalizedAsset(resolved, payload, hashes, snapshot or {})


def material_normal_format(asset, override=None):
    """An explicit filename/provider convention takes precedence over a generic-map hint."""
    if asset.resolved.choices.get("normal_convention_explicit"):
        return asset.input.normal_format
    return override or asset.input.normal_format
