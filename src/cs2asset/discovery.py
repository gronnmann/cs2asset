"""Discover installed Steam applications and paired Hammer addon roots."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import vdf

from .errors import CS2AssetError


@dataclass(frozen=True)
class Installation:
    root: Path
    compiler: Path
    game_dir: Path
    content_dir: Path

    @property
    def game_context(self) -> Path:
        return self.game_dir / "csgo"


@dataclass(frozen=True)
class Project:
    name: str
    content_dir: Path
    game_dir: Path
    is_template: bool = False

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "content_dir": str(self.content_dir),
            "game_dir": str(self.game_dir),
            "is_template": self.is_template,
        }


def _unique_paths(paths):
    found = set()
    for path in paths:
        resolved = Path(path).expanduser().resolve()
        key = os.path.normcase(str(resolved))
        if key not in found:
            found.add(key)
            yield resolved


def steam_roots() -> list[Path]:
    candidates = []
    if os.environ.get("STEAM_PATH"):
        candidates.append(Path(os.environ["STEAM_PATH"]))
    if os.name == "nt":
        import winreg

        for hive, key, value in [
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ]:
            try:
                with winreg.OpenKey(hive, key) as handle:
                    candidates.append(Path(winreg.QueryValueEx(handle, value)[0]))
            except OSError:
                pass
        candidates.append(
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Steam"
        )
    else:
        candidates += [Path.home() / ".steam/steam", Path.home() / ".local/share/Steam"]
    return [p for p in _unique_paths(candidates) if p.is_dir()]


def _read_vdf(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8-sig") as stream:
            return vdf.load(stream)
    except (OSError, ValueError, SyntaxError):
        return {}


def steam_libraries(roots: list[Path] | None = None) -> list[Path]:
    candidates = []
    for root in roots if roots is not None else steam_roots():
        root = Path(root)
        candidates.append(root)
        data = _read_vdf(root / "steamapps/libraryfolders.vdf")
        folders = data.get("libraryfolders", data.get("LibraryFolders", {}))
        for key, value in folders.items():
            if str(key).isdigit():
                path = value.get("path") if isinstance(value, dict) else value
                if path:
                    candidates.append(Path(path))
    return [p for p in _unique_paths(candidates) if p.is_dir()]


def _installation(root: Path) -> Installation:
    root = root.expanduser().resolve()
    compiler = root / "game/bin/win64/resourcecompiler.exe"
    if not (root / "game/csgo").is_dir():
        raise CS2AssetError(f"Not a CS2 installation: {root}; expected game/csgo.")
    if not compiler.is_file() or not (root / "content").is_dir():
        raise CS2AssetError(
            f"CS2 Workshop Tools are missing from {root}. In Steam, open Counter-Strike 2 "
            "Properties > DLC and install Counter-Strike 2 Workshop Tools. "
            f"Expected compiler: {compiler}"
        )
    return Installation(root, compiler, root / "game", root / "content")


def discover_cs2(
    root: str | Path | None = None, steam_roots: list[Path] | None = None
) -> Installation:
    explicit = root or os.environ.get("CS2ASSET_CS2_ROOT")
    if explicit:
        return _installation(Path(explicit))
    failures = []
    for library in steam_libraries(steam_roots):
        state = _read_vdf(library / "steamapps/appmanifest_730.acf").get("AppState", {})
        installdir = state.get("installdir")
        if not installdir:
            continue
        candidate = library / "steamapps/common" / installdir
        try:
            # A corrupt manifest must not redirect discovery outside its library.
            candidate.resolve().relative_to((library / "steamapps/common").resolve())
            return _installation(candidate)
        except ValueError:
            continue
        except CS2AssetError as exc:
            failures.append(str(exc))
    if failures:
        raise CS2AssetError("\n".join(failures))
    raise CS2AssetError(
        "CS2 was not found in Steam libraries. Install CS2 and Workshop Tools, "
        "or set --cs2-root / CS2ASSET_CS2_ROOT to the installation directory."
    )


_NON_PROJECTS = {"workshop_items", "vpks"}
_TEMPLATES = {"addon_template", "cs_script_demo", "ar_laststop"}


def discover_projects(installation: Installation) -> list[Project]:
    content = installation.content_dir / "csgo_addons"
    game = installation.game_dir / "csgo_addons"
    names = set()
    for parent in (content, game):
        if parent.is_dir():
            names.update(p.name for p in parent.iterdir() if p.is_dir())
    return [
        Project(name, content / name, game / name, name in _TEMPLATES)
        for name in sorted(names)
        if name not in _NON_PROJECTS and not name.startswith("cs2asset_stage_")
    ]


def resolve_project(installation: Installation, name_or_path: str | Path) -> Project:
    value = str(name_or_path)
    content_base = (installation.content_dir / "csgo_addons").resolve()
    game_base = (installation.game_dir / "csgo_addons").resolve()
    if "/" in value or "\\" in value or Path(value).is_absolute():
        target = Path(value).expanduser().resolve()
        if target.parent not in (content_base, game_base):
            raise CS2AssetError(
                "Project path must be an immediate addon directory under "
                f"{content_base} or {game_base}."
            )
        name = target.name
    else:
        name = value
    if (
        name in {"", ".", ".."}
        or name in _NON_PROJECTS
        or not re.fullmatch(r"[A-Za-z0-9_-]+", name)
    ):
        raise CS2AssetError(f"Invalid addon name: {name!r}")
    content = content_base / name
    game = game_base / name
    if not content.is_dir() and not game.is_dir():
        raise CS2AssetError(
            f"Addon {name!r} does not exist. Create it in CS2 Workshop Tools first."
        )
    # Resolved containment also rules out junctions/symlinks into another project.
    if content.resolve().parent != content_base or game.resolve().parent != game_base:
        raise CS2AssetError("Addon directories must not redirect outside the addon roots.")
    return Project(name, content, game, name in _TEMPLATES)


def check_write_access(project: Project) -> dict[str, bool]:
    result = {}
    for kind, directory in (("content", project.content_dir), ("game", project.game_dir)):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=directory):
                pass
            result[kind] = True
        except OSError:
            result[kind] = False
    return result


def discover_blender(override: str | Path | None = None) -> Path | None:
    explicit = override or os.environ.get("CS2ASSET_BLENDER")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise CS2AssetError(f"Configured Blender executable does not exist: {path}")
        return path
    executable = shutil.which("blender")
    if executable:
        return Path(executable).resolve()
    candidates = []
    for root in [
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Blender Foundation",
        Path.home() / "AppData/Local/Programs/Blender Foundation",
    ]:
        if root.is_dir():
            candidates.extend(root.glob("Blender*/blender.exe"))
    for library in steam_libraries():
        state = _read_vdf(library / "steamapps/appmanifest_365670.acf").get("AppState", {})
        if state.get("installdir"):
            candidates.append(library / "steamapps/common" / state["installdir"] / "blender.exe")
        candidates.append(library / "steamapps/common/Blender/blender.exe")
    candidates = [p for p in _unique_paths(candidates) if p.is_file()]

    def version_key(path):
        return tuple(int(x) for x in re.findall(r"\d+", path.parent.name))

    return max(candidates, key=version_key) if candidates else None


def executable_version(path: Path, args: tuple[str, ...] = ("--version",)) -> str:
    try:
        proc = subprocess.run(
            [str(path), *args],
            capture_output=True,
            text=True,
            check=False,
            errors="replace",
            timeout=15,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        output = (proc.stdout + proc.stderr).strip()
        return output.splitlines()[0] if output else f"exit {proc.returncode}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable ({exc})"
