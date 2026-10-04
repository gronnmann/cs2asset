"""Rich terminal interface for cs2asset."""

from __future__ import annotations

import functools
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TransferSpeedColumn
from rich.prompt import IntPrompt
from rich.table import Table

from . import __version__
from .config import cache_dir, load_config, save_project_config, save_user_config, user_config_path
from .discovery import (
    check_write_access,
    discover_blender,
    discover_cs2,
    discover_projects,
    executable_version,
    resolve_project,
)
from .errors import CS2AssetError
from .installer import Installer
from .pipeline import ImportOptions, import_asset, rebuild_asset
from .provider import PolyHavenProvider

app = typer.Typer(
    no_args_is_help=True,
    invoke_without_command=True,
    help="Import Poly Haven assets into CS2 Hammer addons.",
)
projects_app = typer.Typer(no_args_is_help=True, help="Select a Hammer addon.")
config_app = typer.Typer(no_args_is_help=True, help="Inspect and set import defaults.")
app.add_typer(projects_app, name="projects")
app.add_typer(config_app, name="config")
console = Console(highlight=False)
errors = Console(stderr=True, highlight=False)


def guarded(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        ctx = args[0] if args else kwargs.get("ctx")
        if ctx and ctx.obj and kwargs.get("json"):
            ctx.obj["json"] = True
        try:
            return fn(*args, **kwargs)
        except (CS2AssetError, OSError) as exc:
            if ctx and ctx.obj and ctx.obj.get("json"):
                console.print_json(data={"error": str(exc)})
            elif ctx and ctx.obj and ctx.obj.get("verbose"):
                errors.print_exception(show_locals=False)
            else:
                errors.print(f"Error: {exc}", style="red", markup=False)
            raise typer.Exit(1) from exc

    return wrapper


@app.callback()
def main(
    ctx: typer.Context,
    project: Annotated[
        str | None, typer.Option(help="Addon name or its content/game path.")
    ] = None,
    cs2_root: Annotated[
        Path | None, typer.Option(help="Override detected CS2 installation.")
    ] = None,
    blender: Annotated[Path | None, typer.Option(help="Override Blender executable.")] = None,
    json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    verbose: Annotated[
        bool, typer.Option(help="Show compiler output and diagnostic details.")
    ] = False,
    version: Annotated[bool, typer.Option("--version", is_eager=True)] = False,
):
    if version:
        console.print(__version__)
        raise typer.Exit()
    ctx.obj = {
        "project": project,
        "cs2_root": cs2_root,
        "blender": blender,
        "json": json,
        "verbose": verbose,
    }


def settings(ctx, project=None):
    return load_config(
        project, {k: v for k, v in ctx.obj.items() if k in {"project", "cs2_root", "blender"}}
    )


def installation(ctx):
    return discover_cs2(settings(ctx).get("cs2_root"))


def select_project(ctx, name=None, *, prompt=True):
    installed = installation(ctx)
    selected = name or settings(ctx).get("project")
    if selected:
        return installed, resolve_project(installed, selected)
    candidates = [p for p in discover_projects(installed) if not p.is_template]
    if not candidates:
        raise CS2AssetError("No Hammer addons found. Create an addon in CS2 Workshop Tools first.")
    if not prompt or not sys.stdin.isatty() or ctx.obj["json"]:
        raise CS2AssetError(
            "Select a project with 'cs2asset projects use NAME' or pass --project NAME."
        )
    table = Table(title="Choose a Hammer addon")
    table.add_column("#")
    table.add_column("Project")
    table.add_column("Content directory")
    for index, candidate in enumerate(candidates, 1):
        table.add_row(str(index), candidate.name, str(candidate.content_dir))
    console.print(table)
    choice = IntPrompt.ask(
        "Project number", choices=[str(n) for n in range(1, len(candidates) + 1)]
    )
    selected = candidates[choice - 1]
    save_user_config({"project": selected.name, "cs2_root": str(installed.root)})
    return installed, selected


def emit(ctx, data, *, json=False):
    if json or ctx.obj["json"]:
        console.print_json(data=data)
        return
    if isinstance(data, list):
        for record in data:
            emit(ctx, record)
        return
    if "resources" in data:
        console.print(
            f"Installed Poly Haven: {data['name']} ({data['resolution']})",
            style="green",
            markup=False,
        )
        for resource in data["resources"]:
            console.print(resource, markup=False)
        console.print(f"Variant: {data['variant']}", markup=False)
        for note in data.get("warnings", []):
            console.print(f"• {note}", style="yellow", markup=False)
    elif data.get("dry_run"):
        console.print(
            f"Poly Haven: {data['name']} → {data['project']} ({data['resolution']})", markup=False
        )
        console.print(f"Selected downloads: {data['download_bytes'] / 1048576:.1f} MiB")
        for item in data["files"]:
            console.print(f"{item['key']}: {item['relative_path']}", markup=False)
    else:
        console.print_json(data=data)


@app.command()
@guarded
def init(ctx: typer.Context, project: str | None = None, json: bool = False):
    """Discover tools and select the addon used by future imports."""
    installed, selected = select_project(ctx, project)
    blender = discover_blender(settings(ctx).get("blender"))
    writable = check_write_access(selected)
    if not all(writable.values()):
        raise CS2AssetError(f"Addon directories are not writable: {writable}")
    saved = {"project": selected.name, "cs2_root": str(installed.root)}
    if blender:
        saved["blender"] = str(blender)
    save_user_config(saved)
    emit(ctx, {**saved, "config": str(user_config_path())}, json=json)


@projects_app.command("list")
@guarded
def projects_list(ctx: typer.Context, json: bool = False):
    """List detected addons, including marked Valve examples."""
    projects = discover_projects(installation(ctx))
    if json or ctx.obj["json"]:
        emit(ctx, [p.as_dict() for p in projects], json=True)
        return
    table = Table(title="Hammer addons")
    for heading in ("Name", "Kind", "Content directory"):
        table.add_column(heading)
    for project in projects:
        table.add_row(
            project.name,
            "Valve example" if project.is_template else "Project",
            str(project.content_dir),
        )
    console.print(table)


@projects_app.command("use")
@guarded
def projects_use(ctx: typer.Context, name: str, json: bool = False):
    """Remember a selected addon."""
    installed, selected = select_project(ctx, name, prompt=False)
    if not all(check_write_access(selected).values()):
        raise CS2AssetError("Selected addon is not writable")
    save_user_config({"project": selected.name, "cs2_root": str(installed.root)})
    emit(ctx, selected.as_dict(), json=json)


@app.command()
@guarded
def doctor(ctx: typer.Context, project: str | None = None, json: bool = False):
    """Check local tools, addon paths, and image-processing capabilities."""
    import OpenImageIO as oiio

    installed = installation(ctx)
    chosen = project or settings(ctx).get("project")
    selected = resolve_project(installed, chosen) if chosen else None
    blender = discover_blender(settings(ctx, selected).get("blender"))
    report = {
        "cs2_root": str(installed.root),
        "compiler": str(installed.compiler),
        "compiler_help": executable_version(installed.compiler, ("-help",)),
        "blender": str(blender) if blender else None,
        "blender_version": executable_version(blender) if blender else None,
        "openimageio": oiio.VERSION_STRING,
        "cache": str(cache_dir()),
        "project": selected.as_dict() if selected else None,
        "write_access": check_write_access(selected) if selected else None,
        "capabilities": {"textures": True, "hdris": True, "models": blender is not None},
        "project_count": len(discover_projects(installed)),
    }
    if not report["compiler_help"].startswith("Usage: resourcecompiler"):
        raise CS2AssetError(f"Valve compiler cannot run: {report['compiler_help']}")
    emit(ctx, report, json=json)


@app.command()
@guarded
def search(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="Words to find in Poly Haven assets.")] = "",
    type: str | None = None,
    limit: int = 20,
    refresh: bool = False,
    json: bool = False,
    offline: bool = False,
):
    """Search Poly Haven assets by words and asset type."""
    with PolyHavenProvider(cache_dir(), offline=offline) as provider:
        assets = provider.search(query, type, limit=limit, refresh=refresh)
        if json or ctx.obj["json"]:
            emit(ctx, [asdict(a) for a in assets], json=True)
            return
        table = Table(title="Poly Haven assets")
        for heading in ("Asset ID", "Name", "Type"):
            table.add_column(heading)
        for asset in assets:
            table.add_row(asset.id, asset.name, asset.type)
        console.print(table)
        if provider.last_warning:
            console.print(provider.last_warning, style="yellow", markup=False)


@app.command()
@guarded
def info(
    ctx: typer.Context,
    asset: str,
    resolution: str | None = None,
    refresh: bool = False,
    json: bool = False,
    offline: bool = False,
):
    """Show Poly Haven metadata and files selected for an import."""
    from .pipeline import plan_import

    with PolyHavenProvider(cache_dir(), offline=offline) as provider:
        resolved = provider.resolve_files(asset, resolution, refresh=refresh)
        emit(
            ctx,
            {
                **plan_import(resolved, ImportOptions(resolution=resolved.resolution)),
                "metadata": resolved.asset.metadata,
            },
            json=json,
        )


def _perform(ctx, project, json, offline, operation):
    installed, selected = select_project(ctx, project)
    merged = settings(ctx, selected)
    quiet = json or ctx.obj["json"]
    tasks = {}
    progress = Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        console=errors,
        disable=quiet or not errors.is_terminal,
    )

    def phase(message):
        if not quiet:
            errors.print(message, markup=False)

    def downloaded(key, done, total):
        # Rich handles updates across download threads; task creation needs serialization.
        with progress._lock:
            if key not in tasks:
                tasks[key] = progress.add_task(key, total=total)
            progress.update(tasks[key], completed=done, total=total)

    with PolyHavenProvider(cache_dir(), offline=offline) as provider, progress:
        return operation(
            installed,
            selected,
            provider,
            merged,
            phase,
            downloaded,
            (lambda line: errors.print(line, markup=False)) if ctx.obj["verbose"] else None,
        )


@app.command("import")
@guarded
def import_command(
    ctx: typer.Context,
    asset: str,
    project: str | None = None,
    resolution: str | None = None,
    scale: float | None = None,
    collision: str | None = None,
    surface: str | None = None,
    tiling: float | None = None,
    yaw: float | None = None,
    exposure: float | None = None,
    normal_format: str | None = None,
    blender: Path | None = None,
    dry_run: bool = False,
    refresh: bool = False,
    overwrite: bool = False,
    force: bool = False,
    json: bool = False,
    offline: bool = False,
):
    """Download, convert, compile, and install a Poly Haven asset into Hammer."""
    explicit = {
        "resolution": resolution,
        "scale": scale,
        "collision": collision,
        "surface": surface,
        "tiling": tiling,
        "yaw": yaw,
        "exposure": exposure,
        "normal_format": normal_format,
    }

    def operation(installed, selected, provider, merged, phase, downloaded, compiler):
        options = ImportOptions(
            **{
                k: v
                for k, v in {
                    **{k: merged[k] for k in explicit if k in merged},
                    **{k: v for k, v in explicit.items() if v is not None},
                }.items()
            }
        )
        return import_asset(
            installed,
            selected,
            provider,
            cache_dir(),
            asset,
            options,
            blender=blender or merged.get("blender"),
            dry_run=dry_run,
            refresh=refresh,
            overwrite=overwrite,
            force=force,
            phase=phase,
            download_progress=downloaded,
            compiler_progress=compiler,
        )

    # Select/project writes are suppressed on noninteractive dry runs unless project exists.
    result = _perform(ctx, project, json, offline, operation)
    emit(ctx, result, json=json)


@app.command("list")
@guarded
def list_imports(ctx: typer.Context, project: str | None = None, json: bool = False):
    """List assets installed by cs2asset in the selected addon."""
    _, selected = select_project(ctx, project)
    records = list(Installer(selected).imports().values())
    if json or ctx.obj["json"]:
        emit(ctx, records, json=True)
        return
    table = Table(title=f"Imported Poly Haven assets: {selected.name}")
    for column in ("Asset", "Type", "Variant", "Resource"):
        table.add_column(column)
    for record in records:
        table.add_row(
            record["asset"], record["type"], record["variant"], "\n".join(record["resources"])
        )
    console.print(table)


@app.command()
@guarded
def rebuild(
    ctx: typer.Context,
    asset: str,
    project: str | None = None,
    variant: str | None = None,
    overwrite: bool = False,
    refresh: bool = False,
    force: bool = False,
    offline: bool = False,
    json: bool = False,
):
    """Rebuild installed variants using their recorded options and cached inputs."""

    def operation(installed, selected, provider, merged, phase, downloaded, compiler):
        return rebuild_asset(
            installed,
            selected,
            provider,
            cache_dir(),
            asset,
            variant=variant,
            blender=merged.get("blender"),
            overwrite=overwrite,
            refresh=refresh,
            force=force,
            phase=phase,
            download_progress=downloaded,
            compiler_progress=compiler,
        )

    emit(ctx, _perform(ctx, project, json, offline, operation), json=json)


@app.command()
@guarded
def recover(ctx: typer.Context, project: str | None = None, json: bool = False):
    """Recover an interrupted addon installation from its transaction journal."""
    _, selected = select_project(ctx, project)
    emit(ctx, {"project": selected.name, "recovered": Installer(selected).recover()}, json=json)


@config_app.command("show")
@guarded
def config_show(ctx: typer.Context, project: str | None = None):
    chosen = project or settings(ctx).get("project")
    selected = resolve_project(installation(ctx), chosen) if chosen else None
    effective = settings(ctx, selected)
    if selected:
        effective["project"] = selected.name
    emit(ctx, effective, json=True)


@config_app.command("set")
@guarded
def config_set(ctx: typer.Context, key: str, value: str, project: str | None = None):
    """Set a user default or a portable default for a specific addon."""
    types = {
        "resolution": str,
        "scale": float,
        "collision": str,
        "surface": str,
        "tiling": float,
        "yaw": float,
        "exposure": float,
        "normal_format": str,
    }
    if key not in types:
        raise CS2AssetError(f"Supported settings: {', '.join(types)}")
    try:
        parsed = types[key](value)
    except ValueError as exc:
        raise CS2AssetError(f"Invalid value for {key}: {value}") from exc
    ImportOptions(**{key: parsed}).validate()
    chosen = project or ctx.obj.get("project")
    if chosen:
        selected = resolve_project(installation(ctx), chosen)
        path = save_project_config(selected, {key: parsed})
    else:
        path = save_user_config({key: parsed})
    emit(ctx, {"setting": key, "value": parsed, "config": str(path)}, json=True)


if __name__ == "__main__":
    app()
