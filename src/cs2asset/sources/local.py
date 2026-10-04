"""Local files, material directories, and bounded texture archives."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import unquote, urlsplit

from cs2asset.assets import AssetInfo, ResolvedAsset, SourceFile, SourceRef
from cs2asset.assets.material import discover_maps
from cs2asset.cache import safe_relative_path
from cs2asset.compiler import run_process
from cs2asset.config import atomic_json
from cs2asset.errors import CS2AssetError
from cs2asset.installer import file_hash

from .base import input_path, normalized, validate_kind

MODEL_EXTENSIONS = {".blend", ".glb", ".gltf", ".fbx", ".obj"}
ARCHIVE_LIMIT = 4 * 1024**3
ARCHIVE_FILE_LIMIT = 4096


def canonical_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def local_identity(path: Path) -> str:
    canonical = os.path.normcase(str(path.resolve()))
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    name = path.name if path.is_dir() else path.stem
    slug = re.sub(r"[^a-z0-9_-]+", "_", name.lower()).strip("_")[:64] or "asset"
    return f"{slug}_{digest}"


def resolution_limit(resolution: str | None) -> int | None:
    if resolution in {None, "native"}:
        return None
    match = re.fullmatch(r"([1-9]\d*)(k)?", resolution.lower())
    if not match:
        raise CS2AssetError("Local resolution must be native, a pixel size, or a size such as 2k")
    pixels = int(match[1]) * (1024 if match[2] else 1)
    if pixels > 32768:
        raise CS2AssetError("Local resolution exceeds 32768 pixels")
    return pixels


def archive_entries(archive: zipfile.ZipFile):
    entries, seen, total = [], set(), 0
    for item in archive.infolist():
        name = item.filename.replace("\\", "/")
        safe_relative_path(name.rstrip("/"))
        if stat.S_ISLNK(item.external_attr >> 16):
            raise CS2AssetError(f"Archive links are unsupported: {name}")
        if item.is_dir():
            continue
        if item.flag_bits & 1:
            raise CS2AssetError(f"Encrypted archive member is unsupported: {name}")
        if name.casefold() in seen:
            raise CS2AssetError(f"Colliding archive paths: {name}")
        seen.add(name.casefold())
        total += item.file_size
        if len(seen) > ARCHIVE_FILE_LIMIT or total > ARCHIVE_LIMIT:
            raise CS2AssetError("Texture archive exceeds limits (4096 files / 4 GiB)")
        entries.append(item)
    return entries


def _file(key: str, path: Path, root: Path) -> SourceFile:
    if not path.is_file():
        raise CS2AssetError(f"Missing source dependency: {path}")
    return SourceFile(
        key, path.relative_to(root).as_posix(), str(path), path.stat().st_size, file_hash(path)
    )


def _text_dependencies(source: Path) -> set[Path]:
    """Cheap dependency discovery; Blender completes format/material inspection."""
    dependencies = {source}
    try:
        if source.suffix.lower() == ".gltf":
            data = json.loads(source.read_text(encoding="utf-8-sig"))
            for item in data.get("buffers", []) + data.get("images", []):
                uri = item.get("uri", "")
                if not uri or uri.startswith("data:"):
                    continue
                if urlsplit(uri).scheme or Path(unquote(uri)).is_absolute():
                    raise CS2AssetError(f"glTF dependency must be a local relative URI: {uri}")
                dependencies.add((source.parent / unquote(uri)).resolve())
        elif source.suffix.lower() == ".obj":
            for line in source.read_text(encoding="utf-8-sig").splitlines():
                if line.strip().startswith("mtllib "):
                    name = line.strip()[7:].strip()
                    path = (source.parent / name).resolve()
                    if Path(name).is_absolute():
                        raise CS2AssetError("Use relative mtllib references in local OBJ files")
                    dependencies.add(path)
    except (ValueError, UnicodeError, TypeError, AttributeError) as exc:
        raise CS2AssetError(f"Cannot read model dependencies in {source}: {exc}") from exc
    for path in dependencies:
        if not path.is_file():
            raise CS2AssetError(f"Model {source} references missing dependency: {path}")
    return dependencies


class LocalFileProvider:
    def resolve(self, value, resolution=None, *, refresh=False, asset_type=None):
        path = canonical_path(value)
        if not path.exists():
            raise CS2AssetError(f"Local source does not exist: {path}")
        limit = resolution_limit(resolution)
        choices, warnings = {"max_dimension": limit}, ()
        if path.is_dir():
            kind = "material"
            names = [p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file()]
            maps, map_choices, warnings = discover_maps(names)
            choices.update(map_choices)
            for name in maps.values():
                if not (path / name).resolve().is_relative_to(path):
                    raise CS2AssetError(
                        f"Material texture redirects outside its source folder: {name}"
                    )
            files = tuple(_file(k, (path / n).resolve(), path) for k, n in maps.items())
        elif path.suffix.lower() == ".zip":
            kind = "material"
            try:
                with zipfile.ZipFile(path) as archive:
                    entries = archive_entries(archive)
                    maps, map_choices, warnings = discover_maps([i.filename for i in entries])
                    choices.update(map_choices)
                    by_name = {i.filename: i for i in entries}
                    files_list = []
                    for key, member in maps.items():
                        with archive.open(member) as stream:
                            digest = hashlib.file_digest(stream, "sha256").hexdigest()
                        files_list.append(
                            SourceFile(
                                key, member, str(path), by_name[member].file_size, digest, member
                            )
                        )
                    files = tuple(files_list)
            except (zipfile.BadZipFile, RuntimeError) as exc:
                raise CS2AssetError(f"Cannot read texture archive {path}: {exc}") from exc
        elif path.suffix.lower() in MODEL_EXTENSIONS:
            kind = "model"
            deps = _text_dependencies(path)
            root = Path(os.path.commonpath([str(p.parent) for p in deps]))
            files = tuple(
                _file(
                    "model" if p == path else f"dependency:{p.relative_to(root).as_posix()}",
                    p,
                    root,
                )
                for p in sorted(deps)
            )
            warnings = (
                "Model materials and complete dependencies require Blender inspection; dry-run does not run Blender.",
            )
        elif path.suffix.lower() in {".hdr", ".exr"}:
            kind = "sky"
            files = (_file("hdri", path, path.parent),)
        else:
            raise CS2AssetError(
                f"Unsupported local input: {path}. Use a PBR texture directory/ZIP, "
                ".hdr/.exr panorama, or .blend/.glb/.gltf/.fbx/.obj static model."
            )
        identifier = local_identity(path)
        resolved = ResolvedAsset(
            AssetInfo(identifier, path.name if path.is_dir() else path.stem, kind),
            SourceRef("local", str(path)),
            resolution or "native",
            files,
            choices,
            warnings,
            f"local/{identifier}",
        )
        return validate_kind(resolved, asset_type)

    def prepare(self, resolved, *, blender=None):
        if resolved.asset.kind != "model":
            return resolved
        if blender is None:
            raise CS2AssetError("Blender is required to inspect local model dependencies")
        source = next(Path(f.source_path) for f in resolved.files if f.key == "model")
        with tempfile.TemporaryDirectory(prefix="cs2asset-inspect-") as temp:
            root = Path(temp)
            job, result = root / "job.json", root / "result.json"
            atomic_json(
                job,
                {
                    "source": str(source),
                    "output_dir": str(root),
                    "result": str(result),
                    "operation": "inspect",
                },
            )
            worker = Path(__file__).parents[1] / "blender_worker.py"
            run_process(
                [
                    str(blender),
                    "--background",
                    "--factory-startup",
                    "--disable-autoexec",
                    "--python-exit-code",
                    "1",
                    "--python",
                    str(worker),
                    "--",
                    str(job),
                ],
                root / "inspect.log",
            )
            data = json.loads(result.read_text(encoding="utf-8"))
        deps = {Path(f.source_path) for f in resolved.files}
        deps.update(Path(p).resolve() for p in data["dependencies"])
        try:
            root = Path(os.path.commonpath([str(p.parent) for p in deps]))
        except ValueError as exc:
            raise CS2AssetError(
                "Model dependencies on different drives must be packed into the model"
            ) from exc
        files = tuple(
            _file(
                "model" if p == source else f"dependency:{p.relative_to(root).as_posix()}", p, root
            )
            for p in sorted(deps)
        )
        return replace(resolved, files=files, warnings=())

    def materialize(self, resolved, destination):
        paths, hashes, remap = {}, {}, {}
        for item in resolved.files:
            target = input_path(destination, item.relative_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            if item.archive_member:
                try:
                    with zipfile.ZipFile(item.source_path) as archive:
                        archive_entries(archive)
                        with archive.open(item.archive_member) as stream, target.open("wb") as out:
                            shutil.copyfileobj(stream, out)
                except (zipfile.BadZipFile, KeyError, RuntimeError) as exc:
                    raise CS2AssetError(
                        f"Archive changed during import: {item.source_path}: {exc}"
                    ) from exc
            else:
                shutil.copy2(item.source_path, target)
                remap[os.path.normcase(item.source_path)] = str(target.resolve())
            if file_hash(target) != item.sha256:
                raise CS2AssetError(
                    f"Source changed during import: {item.source_path}; retry the import"
                )
            paths[item.key], hashes[item.key] = target, item.sha256
        snapshot = {
            "resolved": asdict(resolved),
            "root": str(destination.resolve()),
            "hashes": hashes,
            "dependency_remap": remap,
        }
        atomic_json(destination / "snapshot.json", snapshot)
        return normalized(resolved, paths, hashes, snapshot=snapshot, remap=remap)


class LocalDirectoryProvider(LocalFileProvider):
    """Directories use the same local snapshot and provenance guarantees."""


class SnapshotProvider:
    """Explicit, verified cached-input rebuilds never read the original sources."""

    def __init__(self, snapshot: dict):
        self.snapshot = snapshot

    def resolve(self, value, resolution=None, *, refresh=False, asset_type=None):
        data = self.snapshot["resolved"]
        return validate_kind(
            ResolvedAsset(
                AssetInfo(**data["asset"]),
                SourceRef(**data["source"]),
                data["resolution"],
                tuple(SourceFile(**f) for f in data["files"]),
                data["choices"],
                tuple(data["warnings"]),
                data["namespace"],
            ),
            asset_type,
        )

    def prepare(self, resolved, *, blender=None):
        for item in resolved.files:
            path = input_path(Path(self.snapshot["root"]), item.relative_path)
            if not path.is_file() or file_hash(path) != item.sha256:
                raise CS2AssetError(f"Missing or modified cached input: {path}")
        return resolved

    def materialize(self, resolved, destination):
        root = Path(self.snapshot["root"])
        paths, remap = {}, {}
        for item in resolved.files:
            source = input_path(root, item.relative_path)
            if not source.is_file() or file_hash(source) != item.sha256:
                raise CS2AssetError(f"Missing or modified cached input: {source}")
            target = input_path(destination, item.relative_path)
            if source.resolve() != target.resolve():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            paths[item.key] = target
            remap[os.path.normcase(item.source_path)] = str(target.resolve())
        snapshot = {**self.snapshot, "root": str(destination.resolve()), "dependency_remap": remap}
        return normalized(resolved, paths, self.snapshot["hashes"], snapshot=snapshot, remap=remap)
