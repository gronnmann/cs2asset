"""ambientCG API v3 materials and verified texture packages."""

import re
import zipfile
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from cs2asset.assets import AssetInfo, ResolvedAsset, SourceRef
from cs2asset.cache import DownloadCache, MetadataCache, safe_relative_path
from cs2asset.errors import CS2AssetError
from cs2asset.provider import Asset, DownloadFile, PolyHavenProvider

from .base import normalized, validate_kind
from .local import LocalFileProvider


def parse_asset_id(value):
    if not isinstance(value, str):
        raise CS2AssetError("Invalid ambientCG asset ID")
    value = value.strip()
    if value.startswith(("http://", "https://")):
        parsed = urlsplit(value)
        if parsed.hostname not in {"ambientcg.com", "www.ambientcg.com"}:
            raise CS2AssetError("Expected an ambientCG asset URL")
        match = re.fullmatch(r"/a/([A-Za-z0-9_-]+)/?", parsed.path)
        if match:
            value = match[1]
        elif parsed.path == "/view":
            value = parse_qs(parsed.query).get("id", [""])[0]
        else:
            raise CS2AssetError(
                "Expected an ambientCG URL ending in /a/asset_id or /view?id=asset_id"
            )
    else:
        value = value.removeprefix("ambientcg:")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", value):
        raise CS2AssetError(f"Invalid ambientCG asset ID: {value!r}")
    return value


class AmbientCGProvider(PolyHavenProvider):
    api_url = "https://ambientcg.com"
    provider_label = "ambientCG"

    def __init__(self, cache_dir, **kwargs):
        super().__init__(cache_dir, **kwargs)
        self.cache = MetadataCache(
            Path(cache_dir) / "metadata" / "ambientcg", kwargs.get("ttl", 86400)
        )

    def _assets(self, params, refresh=False):
        data = self._get("/api/v3/assets", params=params, refresh=refresh)
        assets = data.get("assets")
        if not isinstance(assets, list) or any(not isinstance(a, dict) for a in assets):
            raise CS2AssetError("Malformed ambientCG assets response")
        return assets

    def search(self, query, asset_type=None, *, limit=20, refresh=False):
        if asset_type not in {None, "all", "textures", "material"}:
            raise CS2AssetError("ambientCG currently supports materials only")
        if not 1 <= limit <= 500:
            raise CS2AssetError("Search limit must be between 1 and 500")
        data = self._assets(
            {"q": query, "type": "material", "limit": limit, "include": "title,type,url"}, refresh
        )
        try:
            return [
                Asset(parse_asset_id(a["id"]), a.get("title", a["id"]), "textures", a)
                for a in data[:limit]
            ]
        except (KeyError, TypeError) as exc:
            raise CS2AssetError("Malformed ambientCG search result") from exc

    def resolve_files(self, value, resolution=None, *, refresh=False):
        identifier = parse_asset_id(value)
        data = self._assets({"id": identifier, "include": "downloads,title,type,url,maps"}, refresh)
        matches = [a for a in data if str(a.get("id", "")).lower() == identifier.lower()]
        if len(matches) != 1:
            raise CS2AssetError(f"ambientCG asset not found: {identifier}")
        asset = matches[0]
        identifier = parse_asset_id(asset["id"])
        if asset.get("type") != "material":
            raise CS2AssetError("ambientCG currently supports materials only")
        resolution = (resolution or "2k").lower()
        downloads = asset.get("downloads")
        if not isinstance(downloads, list) or any(not isinstance(d, dict) for d in downloads):
            raise CS2AssetError("Malformed ambientCG downloads metadata")
        if any(not isinstance(d.get("attributes"), str) for d in downloads):
            raise CS2AssetError("Malformed ambientCG package attributes")
        packages = {d.get("attributes"): d for d in downloads if d.get("extension") == "zip"}
        attribute = next(
            (
                f"{resolution.upper()}-{fmt}"
                for fmt in ("PNG", "JPG")
                if f"{resolution.upper()}-{fmt}" in packages
            ),
            None,
        )
        if attribute is None:
            raise CS2AssetError(
                f"{identifier} has no {resolution} package. Available: "
                + ", ".join(sorted(str(k) for k in packages))
            )
        package = packages[attribute]
        url, size = package.get("url"), package.get("size")
        if (
            not isinstance(url, str)
            or urlsplit(url).scheme != "https"
            or not urlsplit(url).hostname
            or not isinstance(size, int)
            or size <= 0
        ):
            raise CS2AssetError("Invalid ambientCG package URL or size")
        filename = safe_relative_path(f"{identifier}_{attribute}.zip")
        source_url = f"https://ambientcg.com/a/{identifier}"
        return ResolvedAsset(
            AssetInfo(
                identifier,
                asset.get("title", identifier),
                "material",
                {**asset, "source_url": source_url},
            ),
            SourceRef(
                "ambientcg", f"ambientcg:{identifier}", identifier, "CC0-1.0", {"url": source_url}
            ),
            resolution,
            (DownloadFile("package", url, filename, size),),
            {
                "package": attribute,
                "format": attribute.split("-")[1].lower(),
                "normal_selection": "Prefer OpenGL; use DirectX when GL is unavailable",
            },
            namespace=f"ambientcg/{identifier}",
        )


class AmbientCGSource:
    def __init__(self, provider, cache, *, downloader=DownloadCache, progress=None):
        self.provider, self.cache = provider, cache
        self.downloader, self.progress = downloader, progress

    def resolve(self, value, resolution=None, *, refresh=False, asset_type=None):
        return validate_kind(
            self.provider.resolve_files(value, resolution, refresh=refresh), asset_type
        )

    def prepare(self, resolved, *, blender=None):
        return resolved

    def materialize(self, resolved, destination):
        with self.downloader(
            self.cache, progress=self.progress, offline=self.provider.offline
        ) as downloader:
            package = downloader.materialize(resolved.files, destination / "package")["package"]
        local = LocalFileProvider()
        try:
            discovered = local.resolve(str(package))
            material = local.materialize(discovered, destination / "textures")
        except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
            raise CS2AssetError(f"Cannot read ambientCG package: {exc}") from exc
        # Keep remote identity and package choices; local archive discovery supplies map semantics.
        resolved = replace(
            resolved,
            choices={**resolved.choices, **discovered.choices},
            warnings=resolved.warnings + discovered.warnings,
        )
        return normalized(resolved, material.input.maps, material.input_sha256)
