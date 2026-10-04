"""Poly Haven API normalization independent of conversion and Valve tooling."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self
from urllib.parse import unquote, urlencode, urlsplit

import httpx

from cs2asset.cache import (
    RETRY_STATUSES,
    USER_AGENT,
    MetadataCache,
    retry_delay,
    safe_relative_path,
)
from cs2asset.errors import CS2AssetError

ASSET_TYPES = {0: "hdris", 1: "textures", 2: "models"}


def parse_asset_id(value: str) -> str:
    value = value.strip()
    if value.startswith(("https://", "http://")):
        parsed = urlsplit(value)
        if parsed.hostname not in {"polyhaven.com", "www.polyhaven.com"}:
            raise CS2AssetError(
                "Expected a Poly Haven asset URL (https://polyhaven.com/a/asset_id)"
            )
        match = re.fullmatch(r"/(?:[a-z]{2}/)?a/([a-z0-9_-]+)/?", parsed.path)
        if not match:
            raise CS2AssetError("Expected a Poly Haven asset URL ending in /a/asset_id")
        value = match.group(1)
    elif value.startswith("polyhaven:"):
        value = value.removeprefix("polyhaven:")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", value):
        raise CS2AssetError(f"Invalid Poly Haven asset ID: {value!r}")
    return value


@dataclass(frozen=True)
class Asset:
    id: str
    name: str
    type: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def authors(self) -> dict[str, str]:
        return self.metadata.get("authors", {})

    @property
    def dimensions(self) -> list[float] | None:
        """Poly Haven dimensions are millimetres."""
        return self.metadata.get("dimensions")

    @property
    def source_url(self) -> str:
        return f"https://polyhaven.com/a/{self.id}"


@dataclass(frozen=True)
class DownloadFile:
    key: str
    url: str
    relative_path: str
    size: int | None = None
    md5: str | None = None


@dataclass(frozen=True)
class ResolvedAsset:
    asset: Asset
    resolution: str
    files: tuple[DownloadFile, ...]
    choices: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @property
    def total_size(self) -> int:
        return sum(item.size or 0 for item in self.files)


class PolyHavenProvider:
    def __init__(
        self,
        cache_dir: Path,
        *,
        client: httpx.Client | None = None,
        ttl: float = 86400,
        retries: int = 3,
        offline: bool = False,
    ) -> None:
        self.cache = MetadataCache(Path(cache_dir) / "metadata", ttl)
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=httpx.Timeout(30, connect=15),
        )
        self._owns_client = client is None
        self.retries = max(1, retries)
        self.last_warning: str | None = None
        self.offline = offline

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(
        self, endpoint: str, *, refresh: bool = False, params: dict[str, Any] | None = None
    ) -> Any:
        key = endpoint + ("?" + urlencode(sorted(params.items())) if params else "")
        cached = self.cache.get(key, stale=self.offline)
        if self.offline:
            if cached is None:
                raise CS2AssetError(
                    f"No cached Poly Haven metadata for {endpoint}; run online first."
                )
            return cached
        if cached is not None and not refresh:
            return cached
        error: Exception | None = None
        for attempt in range(self.retries):
            response = None
            try:
                response = self.client.get(
                    "https://api.polyhaven.com" + endpoint,
                    params=params,
                    headers={"User-Agent": USER_AGENT},
                )
                response.raise_for_status()
                value = response.json()
                if not isinstance(value, dict):
                    raise TypeError("Expected a JSON object from the Poly Haven API")
                self.cache.put(key, value)
                return value
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                error = exc
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code not in RETRY_STATUSES
                ):
                    break
                if attempt + 1 < self.retries:
                    time.sleep(retry_delay(response, attempt))
        stale = self.cache.get(key, stale=True)
        if stale is not None:
            self.last_warning = "Poly Haven API unavailable; using cached metadata."
            return stale
        raise CS2AssetError(f"Poly Haven request failed ({endpoint}): {error}")

    @staticmethod
    def _asset(asset_id: str, data: dict[str, Any]) -> Asset:
        if not isinstance(data, dict):
            raise CS2AssetError(f"Malformed Poly Haven metadata for {asset_id}")
        kind = ASSET_TYPES.get(data.get("type"), data.get("type"))
        if kind not in ASSET_TYPES.values():
            raise CS2AssetError(f"Unknown Poly Haven asset type for {asset_id}: {kind!r}")
        return Asset(parse_asset_id(asset_id), data.get("name", asset_id), kind, data)

    def get_asset(self, asset_id: str, *, refresh: bool = False) -> Asset:
        asset_id = parse_asset_id(asset_id)
        return self._asset(asset_id, self._get(f"/info/{asset_id}", refresh=refresh))

    def get_files(self, asset_id: str, *, refresh: bool = False) -> dict[str, Any]:
        return self._get(f"/files/{parse_asset_id(asset_id)}", refresh=refresh)

    def search(
        self, query: str, asset_type: str | None = None, *, limit: int = 20, refresh: bool = False
    ) -> list[Asset]:
        if asset_type not in {None, "all", *ASSET_TYPES.values()}:
            raise CS2AssetError("Asset type must be hdris, textures, models, or all")
        query = query.strip().lower()
        if len(query) > 100:
            raise CS2AssetError("Poly Haven search queries must be at most 100 characters")
        if not 1 <= limit <= 500:
            raise CS2AssetError("Search limit must be between 1 and 500")
        if query:
            try:
                response = self._get(
                    "/search",
                    refresh=refresh,
                    params={"q": query, "t": asset_type or "all", "limit": limit},
                )
                results = response["results"]
                catalog = self.cache.get("/assets", stale=True) or {}
                assets = []
                for result in results[:limit]:
                    slug = result["slug"]
                    assets.append(
                        self._asset(slug, catalog[slug])
                        if slug in catalog
                        else self.get_asset(slug, refresh=refresh)
                    )
                return assets
            except (CS2AssetError, KeyError, TypeError) as exc:
                self.last_warning = f"API search unavailable; using catalog keyword matching: {exc}"
        catalog = self._get("/assets", refresh=refresh)
        words = query.split()
        results = []
        for asset_id, data in catalog.items():
            asset = self._asset(asset_id, data)
            if asset_type not in {None, "all", asset.type}:
                continue
            haystack = " ".join(
                [
                    asset_id,
                    asset.name,
                    str(data.get("tags", [])),
                    str(data.get("category", "")),
                    str(data.get("categories", [])),
                    str(data.get("description", "")),
                ]
            ).lower()
            if all(word in haystack for word in words):
                results.append(asset)
        return sorted(results, key=lambda asset: (asset.id != query, asset.name.lower()))[:limit]

    @staticmethod
    def _download(key: str, entry: dict[str, Any], path: str | None = None) -> DownloadFile:
        url = entry.get("url", "")
        if urlsplit(url).scheme != "https" or not urlsplit(url).hostname:
            raise CS2AssetError(f"Invalid download URL in Poly Haven metadata for {key}")
        filename = path or unquote(urlsplit(url).path.rsplit("/", 1)[-1])
        size = entry.get("size")
        md5 = entry.get("md5")
        if size is not None and (not isinstance(size, int) or size < 0):
            raise CS2AssetError(f"Invalid download size for {key}")
        if md5 is not None and not re.fullmatch(r"[a-fA-F0-9]{32}", md5):
            raise CS2AssetError(f"Invalid MD5 checksum metadata for {key}")
        return DownloadFile(key, url, safe_relative_path(filename), size, md5)

    @staticmethod
    def _format(
        variants: dict[str, Any], preference: tuple[str, ...]
    ) -> tuple[str, dict[str, Any]]:
        for extension in preference:
            if extension in variants and isinstance(variants[extension], dict):
                return extension, variants[extension]
        raise CS2AssetError(f"No supported file format; available: {', '.join(variants)}")

    def resolve_files(
        self, asset: Asset | str, resolution: str | None = None, *, refresh: bool = False
    ) -> ResolvedAsset:
        if isinstance(asset, str):
            asset = self.get_asset(asset, refresh=refresh)
        data = self.get_files(asset.id, refresh=refresh)
        resolution = (resolution or ("4k" if asset.type == "hdris" else "2k")).lower()
        files: list[DownloadFile] = []
        choices: dict[str, Any] = {}
        warnings: list[str] = []
        if asset.type == "hdris":
            group = "hdri"
        elif asset.type == "models":
            group = next(
                (key for key in ("blend", "gltf", "fbx") if resolution in data.get(key, {})), ""
            )
            if not group:
                group = next((key for key in ("blend", "gltf", "fbx") if key in data), "")
        else:
            group = next(
                (key for key in ("Diffuse", "diff", "BaseColor", "Color", "Albedo") if key in data),
                "",
            )
        variants = data.get(group, {})
        if resolution not in variants:
            available = sorted(
                variants, key=lambda r: int(r[:-1]) if re.fullmatch(r"\d+k", r) else 0
            )
            raise CS2AssetError(
                f"{asset.id} has no {resolution} {group or 'supported'} variant. "
                f"Available resolutions: {', '.join(available) or 'none'}"
            )
        if asset.type == "hdris":
            extension, entry = self._format(variants[resolution], ("exr", "hdr"))
            files.append(self._download("hdri", entry))
            choices.update(format=extension, projection="equirectangular")
            if asset.metadata.get("attributes", {}).get("environment") == "indoor":
                warnings.append("This Poly Haven HDRI is an indoor environment.")
        elif asset.type == "models":
            extension, entry = self._format(variants[resolution], ("blend", "gltf", "glb", "fbx"))
            files.append(self._download("model", entry))
            for path, dependency in entry.get("include", {}).items():
                files.append(self._download("dependency:" + path, dependency, path))
            choices["format"] = extension
        else:
            channels = {
                "base_color": ("Diffuse", "diff", "BaseColor", "Color", "Albedo"),
                "normal": ("nor_gl", "nor_dx"),
                "roughness": ("Rough", "Roughness", "rough"),
                "ao": ("AO", "ao"),
                "metalness": ("Metal", "Metallic", "Metalness", "metal"),
                "opacity": ("Alpha", "Opacity", "alpha"),
                "height": ("Displacement", "disp"),
            }
            for channel, names in channels.items():
                name = next((name for name in names if resolution in data.get(name, {})), None)
                if name is None:
                    if channel not in {"height", "opacity"}:
                        warnings.append(
                            f"No {channel} map at {resolution}; converter default applies."
                        )
                    continue
                extension, entry = self._format(
                    data[name][resolution], ("png", "tif", "tiff", "exr", "jpg")
                )
                files.append(self._download(channel, entry))
                choices[channel] = {"map": name, "format": extension}
                if channel == "normal":
                    choices["normal_convention"] = "opengl" if name == "nor_gl" else "directx"
            # Poly Haven's ARM map has a documented R=AO, G=roughness, B=metalness layout.
            missing = {"ao", "roughness", "metalness"} - {item.key for item in files}
            if missing and resolution in data.get("arm", {}):
                extension, entry = self._format(data["arm"][resolution], ("png", "exr", "jpg"))
                files.append(self._download("arm", entry))
                choices["arm_channels"] = {"ao": "R", "roughness": "G", "metalness": "B"}
                warnings = [
                    w for w in warnings if not any(w.startswith(f"No {c} ") for c in missing)
                ]
        paths = [item.relative_path.casefold() for item in files]
        if len(paths) != len(set(paths)):
            raise CS2AssetError("Poly Haven metadata contains colliding dependency paths")
        return ResolvedAsset(asset, resolution, tuple(files), choices, tuple(warnings))
