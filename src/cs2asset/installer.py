"""Journaled publication into paired addon roots, with ownership and hash checks.

The journal is written before changing destination files. Its backups and old
manifest make interrupted publication recoverable on the next invocation. A
project lock serializes cooperating processes; unexpected edits are never lost.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from filelock import FileLock, Timeout

from .config import atomic_json, read_json
from .discovery import Project
from .errors import CS2AssetError


def file_hash(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_relative(value: str) -> str:
    """Accept portable Source 2 resource paths, rejecting Windows aliases/ADS."""
    value = str(value)
    path = PurePosixPath(value)
    if not value or "\\" in value or path.is_absolute() or value != path.as_posix():
        raise CS2AssetError(f"Unsafe resource path: {value!r}")
    reserved = {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
    for part in path.parts:
        if (
            part in {".", ".."}
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", part)
            or part.endswith(".")
            or part.split(".")[0].lower() in reserved
        ):
            raise CS2AssetError(f"Unsafe resource path: {value!r}")
    if not path.parts:
        raise CS2AssetError(f"Unsafe resource path: {value!r}")
    return path.as_posix()


def contained_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = root / safe_relative(relative)
    if not path.resolve().is_relative_to(root):
        raise CS2AssetError(f"Resource path escapes {root}: {relative}")
    return path


def _hash_or_none(path: Path) -> str | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise CS2AssetError(f"Expected a regular file, found a directory: {path}")
    return file_hash(path)


def _source_identity(record: dict) -> tuple | None:
    """Protect stable import IDs without requiring provenance in legacy records."""
    sources = record.get("sources")
    if sources is None:
        source = record.get("source")
        if source is None:
            return None
        sources = [source]
    if not isinstance(sources, list) or not sources:
        raise CS2AssetError("Invalid source provenance in import record")
    identity = []
    for source in sources:
        if not isinstance(source, dict) or not all(
            isinstance(source.get(key), str) and source[key] for key in ("provider", "uri")
        ):
            raise CS2AssetError("Invalid source provenance in import record")
        identity.append((source["provider"], os.path.normcase(source["uri"])))
    return tuple(identity)


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=".cs2asset-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            with source.open("rb") as input_stream:
                shutil.copyfileobj(input_stream, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class Installer:
    def __init__(self, project: Project, lock_timeout: float = 10):
        self.project = project
        self.roots = {"content": project.content_dir.resolve(), "game": project.game_dir.resolve()}
        self.meta = contained_path(self.roots["content"], ".cs2asset")
        self.manifest_path = contained_path(self.roots["content"], ".cs2asset/imports.json")
        self.journal_path = contained_path(self.roots["content"], ".cs2asset/transaction.json")
        self.lock_timeout = lock_timeout

    @contextmanager
    def _locked(self):
        self.meta.mkdir(parents=True, exist_ok=True)
        try:
            with FileLock(str(self.meta / "project.lock"), timeout=self.lock_timeout):
                yield
        except Timeout as exc:
            raise CS2AssetError(
                f"Another import is using project {self.project.name!r}; retry when it finishes."
            ) from exc

    def _manifest(self) -> dict:
        manifest = read_json(self.manifest_path, {"schema_version": 1, "imports": {}})
        if manifest.get("schema_version") != 1 or not isinstance(manifest.get("imports"), dict):
            raise CS2AssetError(f"Unsupported or invalid import manifest: {self.manifest_path}")
        return manifest

    def imports(self) -> dict[str, dict]:
        with self._locked():
            self._recover_locked()
            return self._manifest()["imports"]

    def recover(self) -> bool:
        with self._locked():
            return self._recover_locked()

    def _destination(self, kind: str, relative: str) -> Path:
        if kind not in self.roots:
            raise CS2AssetError(f"Invalid output root in transaction: {kind!r}")
        relative = safe_relative(relative)
        if PurePosixPath(relative).parts[0].lower() not in {"materials", "models"}:
            raise CS2AssetError("Import outputs must be under materials/ or models/.")
        return contained_path(self.roots[kind], relative)

    def _backup_dir(self, journal: dict) -> Path:
        identifier = journal.get("id", "")
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise CS2AssetError("Invalid transaction journal identifier.")
        return contained_path(self.meta, f"transactions/{identifier}")

    def _clean_journal(self, journal: dict):
        # Resolve and validate before recursive deletion; never trust journal paths.
        backup_dir = self._backup_dir(journal).resolve()
        transactions = (self.meta / "transactions").resolve()
        if backup_dir.parent != transactions or not transactions.is_relative_to(
            self.meta.resolve()
        ):
            raise CS2AssetError("Transaction backup directory escaped project metadata.")
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        self.journal_path.unlink(missing_ok=True)

    def _recover_locked(self) -> bool:
        if not self.journal_path.exists():
            return False
        journal = read_json(self.journal_path)
        if journal.get("state") == "committed":
            self._clean_journal(journal)
            return True
        backup_dir = self._backup_dir(journal)
        actions = journal.get("actions")
        if not isinstance(actions, list) or "old_manifest" not in journal:
            raise CS2AssetError(f"Invalid transaction journal: {self.journal_path}")
        # Validate every destination and backup before restoring any file.
        restorations = []
        for index, action in enumerate(actions):
            target = self._destination(action["root"], action["path"])
            current = _hash_or_none(target)
            before, after = action["before"], action["after"]
            if current == before:
                continue  # Never published, or already restored before another interruption.
            if current != after:
                raise CS2AssetError(
                    f"Recovery found an externally edited file: {target}. "
                    f"Backups are retained in {backup_dir}; restore or move this file before retrying."
                )
            backup = contained_path(backup_dir, str(index))
            if before is not None and _hash_or_none(backup) != before:
                raise CS2AssetError(f"Missing or corrupt recovery backup: {backup}")
            restorations.append((target, backup if before is not None else None))
        for target, backup in reversed(restorations):
            if backup is None:
                target.unlink(missing_ok=True)
            else:
                _copy_atomic(backup, target)
        atomic_json(self.manifest_path, journal["old_manifest"])
        self._clean_journal(journal)
        return True

    def install(
        self,
        import_id: str,
        content_files: dict[str, Path],
        game_files: dict[str, Path],
        metadata: dict,
        overwrite: bool = False,
    ) -> dict:
        """Publish a complete import. Explicit overwrite only applies to owned files.

        Identical dependencies may be shared by imports. Changing a shared file
        requires a distinct variant path so another asset cannot be broken.
        """
        if not import_id or not isinstance(import_id, str):
            raise CS2AssetError("An import must have a nonempty stable identifier.")
        with self._locked():
            self._recover_locked()
            old_manifest = self._manifest()
            records = old_manifest["imports"]
            proposed_source = _source_identity(metadata)
            previous_source = _source_identity(records.get(import_id, {}))
            if (
                previous_source is not None
                and proposed_source is not None
                and previous_source != proposed_source
            ):
                raise CS2AssetError(
                    f"Import identity collision: {import_id} already belongs to different sources. "
                    "Use a distinct source location or recipe name."
                )
            owners: dict[tuple[str, str], list[tuple[str, dict]]] = {}
            for identifier, record in records.items():
                for output in record.get("outputs", []):
                    relative = safe_relative(output["path"])
                    self._destination(output["root"], relative)
                    owners.setdefault((output["root"], relative.lower()), []).append(
                        (identifier, output)
                    )
            proposed = {}
            outputs = []
            for kind, files in (("content", content_files), ("game", game_files)):
                for relative, staged in files.items():
                    relative = safe_relative(relative)
                    self._destination(kind, relative)
                    key = (kind, relative.lower())
                    if key in proposed:
                        raise CS2AssetError(f"Duplicate case-insensitive resource path: {relative}")
                    staged = Path(staged)
                    if not staged.is_file():
                        raise CS2AssetError(f"Missing staged output: {staged}")
                    digest = file_hash(staged)
                    output = {
                        "root": kind,
                        "path": relative,
                        "sha256": digest,
                        "size": staged.stat().st_size,
                    }
                    outputs.append(output)
                    proposed[key] = (output, staged)
            if not outputs:
                raise CS2AssetError("Cannot install an import with no outputs.")
            actions = []
            for key, (output, staged) in proposed.items():
                target = self._destination(output["root"], output["path"])
                before = _hash_or_none(target)
                previous = owners.get(key, [])
                if before is not None and not previous:
                    raise CS2AssetError(
                        f"Refusing to overwrite a file not owned by cs2asset: {target}"
                    )
                if (
                    before is not None
                    and previous
                    and not overwrite
                    and any(before != info["sha256"] for _, info in previous)
                ):
                    raise CS2AssetError(
                        f"User-edited output: {target}. Use --overwrite explicitly or another variant."
                    )
                other = [(owner, info) for owner, info in previous if owner != import_id]
                if any(info["sha256"] != output["sha256"] for _, info in other):
                    raise CS2AssetError(
                        f"Output is shared with another import: {target}; use another variant."
                    )
                if before != output["sha256"]:
                    actions.append(
                        {
                            "root": output["root"],
                            "path": output["path"],
                            "before": before,
                            "after": output["sha256"],
                            "source": str(staged.resolve()),
                        }
                    )
            # Remove obsolete outputs only when this import is their sole owner.
            for old in records.get(import_id, {}).get("outputs", []):
                key = (old["root"], old["path"].lower())
                if key in proposed or any(owner != import_id for owner, _ in owners.get(key, [])):
                    continue
                target = self._destination(old["root"], old["path"])
                before = _hash_or_none(target)
                if before is not None and before != old["sha256"] and not overwrite:
                    raise CS2AssetError(
                        f"Refusing to remove a user-edited obsolete output: {target}"
                    )
                if before is not None:
                    actions.append(
                        {"root": old["root"], "path": old["path"], "before": before, "after": None}
                    )
            record = {
                **metadata,
                "id": import_id,
                "installed_at": datetime.now(UTC).isoformat(),
                "outputs": outputs,
            }
            new_manifest = {"schema_version": 1, "imports": {**records, import_id: record}}
            journal = {
                "id": uuid.uuid4().hex,
                "state": "prepared",
                "actions": actions,
                "old_manifest": old_manifest,
            }
            backup_dir = self._backup_dir(journal)
            backup_dir.mkdir(parents=True)
            for index, action in enumerate(actions):
                if action["before"] is not None:
                    _copy_atomic(
                        self._destination(action["root"], action["path"]), backup_dir / str(index)
                    )
                    if file_hash(backup_dir / str(index)) != action["before"]:
                        raise CS2AssetError(
                            "A project file changed while preparing the transaction; retry."
                        )
            atomic_json(self.journal_path, journal)
            try:
                for action in actions:
                    target = self._destination(action["root"], action["path"])
                    if _hash_or_none(target) != action["before"]:
                        raise CS2AssetError(f"File changed during publication: {target}")
                    if action["after"] is None:
                        target.unlink()
                    else:
                        source = Path(action["source"])
                        if file_hash(source) != action["after"]:
                            raise CS2AssetError(f"Staged file changed during publication: {source}")
                        _copy_atomic(source, target)
                        if file_hash(target) != action["after"]:
                            raise CS2AssetError(
                                f"Published file failed checksum validation: {target}"
                            )
                atomic_json(self.manifest_path, new_manifest)
                journal["state"] = "committed"
                atomic_json(self.journal_path, journal)
            except BaseException:
                self._recover_locked()
                raise
            self._clean_journal(journal)
            return record
