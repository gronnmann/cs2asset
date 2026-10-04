"""Provider choice happens here, once per source."""

import re
from pathlib import Path

from cs2asset.cache import DownloadCache
from cs2asset.errors import CS2AssetError
from cs2asset.provider import PolyHavenProvider, parse_asset_id

from .local import LocalDirectoryProvider, LocalFileProvider, canonical_path
from .polyhaven import PolyHavenSource


def is_local(value: str) -> bool:
    if Path(value).expanduser().exists() or re.match(r"^[a-zA-Z]:", value):
        return True
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value):
        return False
    return value.startswith((".", "/", "\\", "~")) or any(c in value for c in "/\\.")


def canonical_source(value: str) -> str:
    if is_local(value):
        return str(canonical_path(value))
    if ":" in value and value.split(":", 1)[0] not in {"polyhaven", "https", "http"}:
        return value
    return f"polyhaven:{parse_asset_id(value)}"


class SourceRegistry:
    def __init__(
        self,
        cache: Path,
        *,
        offline=False,
        legacy_provider=None,
        downloader=DownloadCache,
        progress=None,
    ):
        self.cache, self.offline = cache, offline
        self.legacy_provider = legacy_provider
        self.downloader, self.progress = downloader, progress
        self._owned_provider = None
        self._factories = {"polyhaven": self._polyhaven_source}

    def register(self, scheme, factory):
        """Add a provider factory without changing the CLI pipeline or converters."""
        if not re.fullmatch(r"[a-z][a-z0-9+.-]*", scheme) or len(scheme) == 1:
            raise CS2AssetError("Provider schemes must be lowercase names, not Windows drive letters")
        self._factories[scheme] = factory

    def provider_for(self, value):
        if is_local(value):
            cls = LocalDirectoryProvider if Path(value).is_dir() else LocalFileProvider
            return cls()
        scheme = value.split(":", 1)[0] if ":" in value else "polyhaven"
        if scheme in {"https", "http"}:
            parse_asset_id(value)
            scheme = "polyhaven"
        factory = self._factories.get(scheme)
        if factory is None:
            raise CS2AssetError(f"Unsupported source provider: {scheme}")
        return factory()

    def _polyhaven_source(self):
        provider = self.legacy_provider
        if provider is None:
            if self._owned_provider is None:
                self._owned_provider = PolyHavenProvider(self.cache, offline=self.offline)
            provider = self._owned_provider
        return PolyHavenSource(
            provider, self.cache, downloader=self.downloader, progress=self.progress
        )

    def close(self):
        if self._owned_provider:
            self._owned_provider.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def source_provider(provider, value, cache, *, downloader=DownloadCache, progress=None):
    if hasattr(provider, "provider_for"):
        return provider.provider_for(value)
    if hasattr(provider, "resolve") and hasattr(provider, "materialize"):
        return provider
    if is_local(value):
        return LocalFileProvider()
    return PolyHavenSource(provider, cache, downloader=downloader, progress=progress)


def record_source(record: dict) -> dict:
    """Read schema-1 records without changing ownership or writing a migration."""
    if "source" in record:
        return record["source"]
    asset = record.get("asset")
    if asset and record.get("id", "").startswith("polyhaven:"):
        return {
            "provider": "polyhaven",
            "id": asset,
            "uri": f"polyhaven:{asset}",
            "license": "CC0-1.0",
            "extensions": {"url": record.get("source_url")},
        }
    raise CS2AssetError("Import record has no recognizable source provenance")
