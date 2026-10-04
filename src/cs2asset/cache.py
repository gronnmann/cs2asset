"""Verified shared downloads and expiring, offline-capable API metadata."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import urlsplit

import httpx
from filelock import FileLock, Timeout

from cs2asset import __version__
from cs2asset.errors import CS2AssetError

if TYPE_CHECKING:
    from cs2asset.provider import DownloadFile

USER_AGENT = f"cs2asset/{__version__}"
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}


def safe_relative_path(value: str) -> str:
    """Reject traversal, Windows aliases, ADS, device names, and absolute paths."""
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if (
        not value
        or PureWindowsPath(value).drive
        or normalized.startswith("/")
        or any(p in {"", ".", ".."} for p in parts)
    ):
        raise CS2AssetError(f"Unsafe asset dependency path: {value!r}")
    for part in parts:
        if (
            re.search(r'[<>:"|?*\x00-\x1f]', part)
            or part.endswith((".", " "))
            or part.split(".")[0].upper()
            in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *(f"COM{i}" for i in range(10)),
                *(f"LPT{i}" for i in range(10)),
            }
        ):
            raise CS2AssetError(f"Unsafe asset dependency path: {value!r}")
    return PurePosixPath(normalized).as_posix()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def retry_delay(response: httpx.Response | None, attempt: int) -> float:
    value = response.headers.get("Retry-After") if response is not None else None
    if value:
        try:
            delay = float(value)
        except ValueError:
            try:
                delay = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
            except (ValueError, TypeError):
                delay = 2**attempt
        if delay > 60:
            raise CS2AssetError(f"Server rate limit: retry after {value}; try again later.")
        return max(0, delay)
    return min(2**attempt, 8)


class MetadataCache:
    def __init__(self, directory: Path, ttl: float = 86400) -> None:
        self.directory = Path(directory)
        self.ttl = ttl

    def path(self, key: str) -> Path:
        return self.directory / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def get(self, key: str, *, stale: bool = False) -> Any | None:
        try:
            record = json.loads(self.path(key).read_text(encoding="utf-8"))
            if stale or time.time() - record["fetched_at"] < self.ttl:
                return record["data"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def put(self, key: str, value: Any) -> None:
        atomic_json(self.path(key), {"fetched_at": time.time(), "data": value})


class DownloadCache:
    """Bounded downloads; only complete, verified files enter the shared cache.

    Deliberately restarts interrupted downloads. No unvalidated HTTP range requests.
    The progress callback receives (file key, downloaded bytes, expected bytes).
    """

    def __init__(
        self,
        cache_dir: Path,
        *,
        client: httpx.Client | None = None,
        workers: int = 4,
        retries: int = 3,
        progress: Callable[[str, int, int | None], None] | None = None,
        offline: bool = False,
    ) -> None:
        self.directory = Path(cache_dir) / "downloads"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=httpx.Timeout(90, connect=20),
            limits=httpx.Limits(max_connections=4),
        )
        self._owns_client = client is None
        self.workers = max(1, min(workers, 8))
        self.retries = max(1, retries)
        self.progress = progress
        self.offline = offline
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _hashes(path: Path) -> tuple[str, str]:
        md5, sha = hashlib.md5(usedforsecurity=False), hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                md5.update(chunk)
                sha.update(chunk)
        return md5.hexdigest(), sha.hexdigest()

    def _valid(self, path: Path, item: DownloadFile) -> bool:
        try:
            if item.size is not None and path.stat().st_size != item.size:
                return False
            md5, sha = self._hashes(path)
            if item.md5:
                return md5 == item.md5.lower()
            record = json.loads(path.with_suffix(path.suffix + ".json").read_text())
            return record["sha256"] == sha
        except (OSError, ValueError, KeyError):
            return False

    def download(self, item: DownloadFile) -> Path:
        safe_relative_path(item.relative_path)
        if urlsplit(item.url).scheme != "https":
            raise CS2AssetError(f"Download URL must use HTTPS: {item.url}")
        identity = hashlib.sha256(f"{item.url}\n{item.md5}\n{item.size}".encode()).hexdigest()
        suffix = Path(item.relative_path).suffix
        destination = self.directory / (identity + suffix)
        with self._locks_guard:
            lock = self._locks.setdefault(identity, threading.Lock())
        process_lock = FileLock(str(destination) + ".lock", timeout=120)
        try:
            process_lock.acquire()
        except Timeout as exc:
            raise CS2AssetError(
                f"Another cs2asset process is downloading {item.key}; try again later."
            ) from exc
        try:
            with lock:
                return self._download_locked(item, destination, identity)
        finally:
            process_lock.release()

    def _download_locked(self, item: DownloadFile, destination: Path, identity: str) -> Path:
        # The OS lock proves no other process is using this item's temporary files.
        for partial in self.directory.glob(identity + "*.part"):
            partial.unlink(missing_ok=True)
        if self._valid(destination, item):
            if self.progress:
                self.progress(item.key, destination.stat().st_size, item.size)
            return destination
        if self.offline:
            raise CS2AssetError(
                f"No verified cached download for {item.key}; run an online import first."
            )
        last_error: Exception | None = None
        for attempt in range(self.retries):
            fd, name = tempfile.mkstemp(prefix=identity, suffix=".part", dir=self.directory)
            os.close(fd)
            temporary = Path(name)
            response = None
            try:
                with self.client.stream(
                    "GET",
                    item.url,
                    headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
                ) as response:
                    response.raise_for_status()
                    if response.status_code != 200:
                        raise CS2AssetError("Unexpected partial download response; refusing it.")
                    downloaded = 0
                    md5, sha = hashlib.md5(usedforsecurity=False), hashlib.sha256()
                    with temporary.open("wb") as stream:
                        for chunk in response.iter_bytes(256 * 1024):
                            downloaded += len(chunk)
                            if item.size is not None and downloaded > item.size:
                                raise CS2AssetError(f"Download exceeds expected size: {item.key}")
                            stream.write(chunk)
                            md5.update(chunk)
                            sha.update(chunk)
                            if self.progress:
                                self.progress(item.key, downloaded, item.size)
                    if item.size is not None and downloaded != item.size:
                        raise CS2AssetError(
                            f"Download size mismatch for {item.key}: "
                            f"expected {item.size}, got {downloaded}"
                        )
                    if item.md5 and md5.hexdigest() != item.md5.lower():
                        raise CS2AssetError(f"MD5 checksum mismatch for {item.key}")
                    os.replace(temporary, destination)
                    atomic_json(
                        destination.with_suffix(destination.suffix + ".json"),
                        {
                            "url": item.url,
                            "size": downloaded,
                            "md5": md5.hexdigest(),
                            "sha256": sha.hexdigest(),
                            "downloaded_at": time.time(),
                        },
                    )
                    return destination
            except (httpx.HTTPError, CS2AssetError) as exc:
                last_error = exc
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code not in RETRY_STATUSES
                ):
                    break
                if attempt + 1 < self.retries:
                    time.sleep(retry_delay(response, attempt))
            finally:
                temporary.unlink(missing_ok=True)
        raise CS2AssetError(f"Could not download {item.key} from {item.url}: {last_error}")

    def download_all(self, files: Iterable[DownloadFile]) -> dict[str, Path]:
        items = tuple(files)
        if len({item.key for item in items}) != len(items):
            raise CS2AssetError("Duplicate download keys in asset metadata")
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            paths = list(executor.map(self.download, items))
        return {item.key: path for item, path in zip(items, paths, strict=True)}

    def materialize(self, files: Iterable[DownloadFile], destination: Path) -> dict[str, Path]:
        items = tuple(files)
        root = Path(destination).resolve()
        targets = {item.key: root / safe_relative_path(item.relative_path) for item in items}
        if len({str(path).casefold() for path in targets.values()}) != len(items):
            raise CS2AssetError("Colliding dependency paths in asset metadata")
        for path in targets.values():
            if not path.resolve().is_relative_to(root):
                raise CS2AssetError(f"Dependency escapes destination through a link: {path}")
        downloads = self.download_all(items)
        for item in items:
            path = targets[item.key]
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(downloads[item.key], path)
        return targets
