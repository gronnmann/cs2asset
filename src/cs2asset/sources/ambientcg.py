"""ambientCG API v3 materials, models, and HDR panoramas."""

import re
import shutil
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np

from cs2asset.assets import AssetInfo, ResolvedAsset, SourceRef
from cs2asset.cache import DownloadCache, MetadataCache, safe_relative_path
from cs2asset.errors import CS2AssetError
from cs2asset.images import read_image, write_image
from cs2asset.installer import file_hash
from cs2asset.provider import Asset, DownloadFile, PolyHavenProvider

from .base import input_path, normalized, validate_kind
from .local import MODEL_EXTENSIONS, LocalFileProvider, archive_entries

KINDS = {"material": "material", "3d-model": "model", "hdri": "sky"}
SEARCH_TYPES = {
    "textures": "material",
    "material": "material",
    "models": "3d-model",
    "model": "3d-model",
    "hdris": "hdri",
    "sky": "hdri",
    "hdri": "hdri",
}


def package_resolution(attribute):
    match = re.search(r"(?:^|-)([1-9]\d*K)(?:-|$)", attribute.upper())
    return match[1].lower() if match else None


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
        if asset_type not in {None, "all", *SEARCH_TYPES}:
            raise CS2AssetError("Asset type must be textures, models, or hdris")
        if not 1 <= limit <= 500:
            raise CS2AssetError("Search limit must be between 1 and 500")
        data = self._assets(
            {
                "q": query,
                "type": SEARCH_TYPES.get(asset_type, "material,3d-model,hdri"),
                "limit": limit,
                "include": "title,type,url",
            },
            refresh,
        )
        try:
            return [
                Asset(
                    parse_asset_id(a["id"]),
                    a.get("title", a["id"]),
                    {"material": "textures", "3d-model": "models", "hdri": "hdris"}[a["type"]],
                    a,
                )
                for a in data[:limit]
                if a.get("type") in KINDS
            ]
        except (KeyError, TypeError) as exc:
            raise CS2AssetError("Malformed ambientCG search result") from exc

    def get_asset_metadata(self, value, *, refresh=False):
        identifier = parse_asset_id(value)
        data = self._assets({"id": identifier, "include": "downloads,title,type,url,maps"}, refresh)
        matches = [a for a in data if str(a.get("id", "")).lower() == identifier.lower()]
        if len(matches) != 1:
            raise CS2AssetError(f"ambientCG asset not found: {identifier}")
        asset = matches[0]
        identifier = parse_asset_id(asset["id"])
        if asset.get("type") not in KINDS:
            raise CS2AssetError(f"Unsupported ambientCG type: {asset.get('type')}")
        downloads = asset.get("downloads")
        if not isinstance(downloads, list) or any(not isinstance(d, dict) for d in downloads):
            raise CS2AssetError("Malformed ambientCG downloads metadata")
        if any(not isinstance(d.get("attributes"), str) for d in downloads):
            raise CS2AssetError("Malformed ambientCG package attributes")
        return asset

    def available_resolutions(self, value, *, refresh=False):
        asset = self.get_asset_metadata(value, refresh=refresh)
        return sorted(
            {
                r
                for d in asset["downloads"]
                if d.get("extension") == "zip" and (r := package_resolution(d["attributes"]))
            },
            key=lambda r: int(r[:-1]),
        )

    def resolve_files(self, value, resolution=None, *, refresh=False):
        asset = self.get_asset_metadata(value, refresh=refresh)
        identifier = parse_asset_id(asset["id"])
        kind = KINDS[asset["type"]]
        resolution = (resolution or ("4k" if kind == "sky" else "2k")).lower()
        downloads = asset["downloads"]
        packages = {d.get("attributes"): d for d in downloads if d.get("extension") == "zip"}
        if kind == "material":
            preferences = [f"{resolution.upper()}-{fmt}" for fmt in ("PNG", "JPG")]
        elif kind == "sky":
            preferences = [resolution.upper()]
        else:
            # Standard geometry quality first, with HQ/LQ fallbacks; resolution is texture quality.
            preferences = [
                f"{level}-{resolution.upper()}-{fmt}"
                for level in ("SQ", "HQ", "LQ")
                for fmt in ("PNG", "JPG")
            ]
            preferences += [f"{resolution.upper()}-{fmt}" for fmt in ("PNG", "JPG")]
        attribute = next((p for p in preferences if p in packages), None)
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
                kind,
                {**asset, "source_url": source_url},
            ),
            SourceRef(
                "ambientcg", f"ambientcg:{identifier}", identifier, "CC0-1.0", {"url": source_url}
            ),
            resolution,
            (DownloadFile("package", url, filename, size),),
            {
                "package": attribute,
                "format": attribute.split("-")[-1].lower() if kind != "sky" else "exr",
                "geometry_quality": attribute.split("-")[0] if kind == "model" else None,
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
        if resolved.asset.kind == "model":
            self._temporary = tempfile.TemporaryDirectory(prefix="cs2asset-ambientcg-")
            root = Path(self._temporary.name)
            package = self._download(resolved, root)
            self._extract(package, root / "source")
            model = self._select(root / "source", MODEL_EXTENSIONS)
            local = LocalFileProvider()
            source_root = (root / "source").resolve()
            discovered = local.resolve(str(model))
            if any(
                not Path(f.source_path).resolve().is_relative_to(source_root)
                for f in discovered.files
            ):
                raise CS2AssetError("Model package references dependencies outside its archive")
            self._prepared = local.prepare(discovered, blender=blender)
            if any(
                not Path(f.source_path).resolve().is_relative_to(source_root)
                for f in self._prepared.files
            ):
                raise CS2AssetError("Model package references dependencies outside its archive")
        return resolved

    def _download(self, resolved, destination):
        with self.downloader(
            self.cache, progress=self.progress, offline=self.provider.offline
        ) as downloader:
            return downloader.materialize(resolved.files, destination / "package")["package"]

    @staticmethod
    def _extract(package, destination):
        with zipfile.ZipFile(package) as archive:
            for entry in archive_entries(archive):
                target = input_path(destination, entry.filename.replace("\\", "/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out)

    @staticmethod
    def _select(root, extensions):
        candidates = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in extensions]
        order = (
            [".blend", ".glb", ".gltf", ".fbx", ".obj"]
            if ".obj" in extensions
            else [".exr", ".hdr"]
        )
        for extension in order:
            matches = [p for p in candidates if p.suffix.lower() == extension]
            if len(matches) > 1:
                raise CS2AssetError(f"Ambiguous ambientCG package: multiple {extension} sources")
            if matches:
                return matches[0]
        raise CS2AssetError("ambientCG package contains no supported source file")

    def materialize(self, resolved, destination):
        if resolved.asset.kind == "model":
            if not hasattr(self, "_prepared"):
                raise CS2AssetError("Model package must be prepared with Blender before import")
            model = LocalFileProvider().materialize(self._prepared, destination / "model")
            return normalized(
                resolved,
                model.input.dependencies,
                model.input_sha256,
                remap=model.input.dependency_remap,
            )
        package = self._download(resolved, destination)
        if resolved.asset.kind == "sky":
            self._extract(package, destination / "sky")
            panorama = self._select(destination / "sky", {".exr", ".hdr"})
            original_hash = file_hash(panorama)
            pixels = read_image(panorama)
            minimum = float(pixels[:, :, :3].min())
            # ambientCG EXRs can have tiny below-zero compression/reconstruction noise.
            # Preserve the downloaded source; normalize only a bounded working copy.
            if -0.01 <= minimum < 0:
                count = int(np.count_nonzero(pixels[:, :, :3] < 0))
                pixels[:, :, :3] = np.maximum(pixels[:, :, :3], 0)
                panorama = write_image(
                    destination / "normalized_sky.exr", pixels, hdr=True, full_float=True
                )
                resolved = replace(
                    resolved,
                    choices={
                        **resolved.choices,
                        "source_hdri_sha256": original_hash,
                        "negative_radiance_clamped": count,
                        "source_minimum_radiance": minimum,
                    },
                    warnings=(
                        *resolved.warnings,
                        f"Clamped {count} small negative ambientCG HDR components (minimum {minimum:.6g}) to zero.",
                    ),
                )
            return normalized(resolved, {"hdri": panorama}, {"hdri": file_hash(panorama)})
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
