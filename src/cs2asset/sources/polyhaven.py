"""Adapter around the tested Poly Haven implementation and verified download cache."""

from pathlib import Path

from cs2asset.assets import AssetInfo, ResolvedAsset, SourceRef
from cs2asset.cache import DownloadCache
from cs2asset.installer import file_hash

from .base import normalized, validate_kind


def adapt_resolved(value) -> ResolvedAsset:
    if isinstance(value, ResolvedAsset):
        return value
    asset = value.asset
    metadata = dict(asset.metadata)
    metadata.update(
        source_url=asset.source_url,
        authors=asset.authors,
        dimensions={"values": asset.dimensions, "unit": "mm"} if asset.dimensions else None,
    )
    return ResolvedAsset(
        AssetInfo(
            asset.id,
            asset.name,
            {"textures": "material", "models": "model", "hdris": "sky"}[asset.type],
            metadata,
        ),
        SourceRef(
            "polyhaven", f"polyhaven:{asset.id}", asset.id, "CC0-1.0", {"url": asset.source_url}
        ),
        value.resolution,
        value.files,
        value.choices,
        value.warnings,
        f"polyhaven/{asset.id}",
    )


class PolyHavenSource:
    def __init__(self, provider, cache: Path, *, downloader=DownloadCache, progress=None):
        self.provider, self.cache = provider, cache
        self.downloader, self.progress = downloader, progress

    def resolve(self, value, resolution=None, *, refresh=False, asset_type=None):
        return validate_kind(
            adapt_resolved(self.provider.resolve_files(value, resolution, refresh=refresh)),
            asset_type,
        )

    def prepare(self, resolved, *, blender=None):
        return resolved

    def materialize(self, resolved, destination):
        with self.downloader(
            self.cache, progress=self.progress, offline=self.provider.offline
        ) as downloader:
            paths = downloader.materialize(resolved.files, destination)
        return normalized(resolved, paths, {k: file_hash(p) for k, p in paths.items()})
