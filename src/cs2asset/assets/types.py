"""The boundary between source resolution and Source 2 conversion."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

AssetKind = Literal["material", "model", "sky"]
LEGACY_TYPES = {"material": "textures", "model": "models", "sky": "hdris"}


@dataclass(frozen=True)
class SourceRef:
    provider: str
    uri: str
    id: str | None = None
    license: str | None = None
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AssetInfo:
    id: str
    name: str
    kind: AssetKind
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def type(self) -> str:
        return LEGACY_TYPES[self.kind]


@dataclass(frozen=True)
class SourceFile:
    key: str
    relative_path: str
    source_path: str
    size: int
    sha256: str
    archive_member: str | None = None


@dataclass(frozen=True)
class ResolvedAsset:
    asset: AssetInfo
    source: SourceRef
    resolution: str
    files: tuple[Any, ...]
    choices: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    namespace: str = ""

    @property
    def total_size(self) -> int:
        return sum(f.size or 0 for f in self.files)

    @property
    def download_size(self) -> int:
        return sum(f.size or 0 for f in self.files if hasattr(f, "url"))

    @property
    def identity(self) -> str:
        return f"{self.source.provider}:{self.asset.id}"


@dataclass(frozen=True)
class MaterialInput:
    maps: dict[str, Path]
    normal_format: str = "gl"
    constants: dict = field(default_factory=dict)
    packed_layouts: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelInput:
    path: Path
    dependencies: dict[str, Path] = field(default_factory=dict)
    # Canonical original dependency path -> snapshot path, for Blender images.
    dependency_remap: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SkyInput:
    path: Path


@dataclass(frozen=True)
class NormalizedAsset:
    resolved: ResolvedAsset
    input: MaterialInput | ModelInput | SkyInput
    input_sha256: dict[str, str]
    snapshot: dict = field(default_factory=dict)


@dataclass(frozen=True)
class BlendInput:
    layers: tuple[NormalizedAsset, NormalizedAsset]
    name: str
    contract_version: str | None = None
