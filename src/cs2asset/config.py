"""Atomic JSON configuration; CLI > project > user > discovery."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from platformdirs import user_cache_path
from platformdirs import user_config_path as platform_config_path

from .errors import CS2AssetError


def user_config_path() -> Path:
    return (
        Path(
            os.environ.get("CS2ASSET_CONFIG_DIR", platform_config_path("cs2asset", appauthor=False))
        )
        / "config.json"
    )


def cache_dir() -> Path:
    return Path(os.environ.get("CS2ASSET_CACHE_DIR", user_cache_path("cs2asset", appauthor=False)))


def project_config_path(project) -> Path:
    root = Path(project.content_dir if hasattr(project, "content_dir") else project).resolve()
    path = root / ".cs2asset/config.json"
    if not path.resolve().is_relative_to(root):
        raise CS2AssetError("Project metadata directory escapes the project.")
    return path


def read_json(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    try:
        with path.open(encoding="utf-8") as stream:
            result = json.load(stream)
        if not isinstance(result, dict):
            raise TypeError("expected a JSON object")
        return result
    except (OSError, ValueError, TypeError) as exc:
        raise CS2AssetError(f"Cannot read {path}: {exc}") from exc


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".cs2asset-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_config(project=None, overrides: dict | None = None) -> dict:
    settings = read_json(user_config_path())
    if project is not None:
        settings.update(read_json(project_config_path(project)))
    settings.update({k: v for k, v in (overrides or {}).items() if v is not None})
    return settings


def save_user_config(settings: dict) -> Path:
    path = user_config_path()
    existing = read_json(path)
    existing.update(settings)
    atomic_json(path, existing)
    return path


def save_project_config(project, settings: dict) -> Path:
    machine_keys = {"cs2_root", "blender", "compiler", "project", "cache_dir"}
    if machine_keys.intersection(settings):
        raise CS2AssetError(
            "Machine-specific paths belong in user configuration, not project configuration."
        )
    path = project_config_path(project)
    existing = read_json(path)
    existing.update(settings)
    atomic_json(path, existing)
    return path
