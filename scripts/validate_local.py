"""Repeatable end-to-end validation in a dedicated addon, using real Valve tools.

Run with ``uv run python scripts/validate_local.py``. Add ``--map`` to prepare
and compile a copy of Valve's supplied template containing the imported assets.
"""

import argparse
import json
import re
from pathlib import Path
from uuid import uuid4

from cs2asset.blend import create_blend_asset
from cs2asset.compiler import run_process
from cs2asset.config import cache_dir
from cs2asset.discovery import Project, discover_cs2
from cs2asset.pipeline import ImportOptions, import_asset, rebuild_asset
from cs2asset.sources import SourceRegistry


def validation_map(installation, project, records, logs):
    template = installation.content_dir / "csgo_addons/addon_template/maps/xxx_mapname_xxx.vmap"
    if not template.is_file():
        raise RuntimeError(f"Valve's validation template is unavailable: {template}")
    destination = project.content_dir / "maps/cs2asset_validation.vmap"
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_process(
        [
            str(installation.compiler.with_name("dmxconvert.exe")),
            "-i",
            str(template),
            "-o",
            str(destination),
            "-oe",
            "keyvalues2",
        ],
        logs / "map-convert.log",
    )
    texture = records["concrete_floor_01"]["resources"][0]
    sky = records["sunset_jhbcentral"]["resources"][0]
    model = records["dirty_football"]["resources"][0]
    text = destination.read_text(encoding="utf-8")
    text = text.replace("materials/dev/reflectivity_30.vmat", texture)
    text = text.replace("materials/dev/dev_measuregeneric01.vmat", texture)
    text = text.replace("materials/editor/toolscene_lighting_sky_dust.vmat", sky)
    entity = f'''"CMapEntity"
            {{
                "id" "elementid" "{uuid4()}"
                "nodeID" "int" "900001"
                "children" "element_array" []
                "entity_properties" "EditGameClassProps"
                {{
                    "id" "elementid" "{uuid4()}"
                    "classname" "string" "prop_static"
                    "targetname" "string" "cs2asset_football"
                    "model" "string" "{model}"
                    "solid" "string" "6"
                }}
                "origin" "vector3" "512 256 8"
                "angles" "qangle" "0 0 0"
                "scales" "vector3" "1 1 1"
                "editorOnly" "bool" "0"
            }},
'''
    pattern = r'("world" "CMapWorld"\s*\{.*?"children" "element_array"\s*\[)'
    text, count = re.subn(pattern, lambda m: m[1] + "\n" + entity, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError("Installed Valve map template has an unsupported world structure")
    destination.write_text(text, encoding="utf-8")
    output = run_process(
        [
            str(installation.compiler),
            "-nop4",
            "-game",
            str(installation.game_context),
            "-i",
            str(destination),
        ],
        logs / "map-compile.log",
        timeout=1200,
        progress=lambda line: print(line, flush=True),
    )
    package = project.game_dir / "maps/cs2asset_validation.vpk"
    if (
        not package.is_file()
        or package.stat().st_size == 0
        or not re.search(r"OK:\s*\d+ compiled,\s*0 failed", output)
    ):
        raise RuntimeError(f"Map build did not produce a validated package; see {logs}")
    return {
        "source": str(destination),
        "package": str(package),
        "log": str(logs / "map-compile.log"),
        "compiler_output_tail": output.splitlines()[-12:],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--map", action="store_true", help="Compile a disposable Valve template map"
    )
    parser.add_argument("--resolution", default="1k")
    parser.add_argument(
        "--asset",
        action="append",
        default=[],
        help="Local or provider input; repeat for several imports",
    )
    parser.add_argument(
        "--blend", nargs=2, metavar=("A", "B"), help="Import a blend from two material sources"
    )
    parser.add_argument("--blend-name", default="validation_blend")
    parser.add_argument(
        "--rebuild", action="store_true", help="Also rebuild each installed import by its ID"
    )
    args = parser.parse_args()
    if args.map and (args.asset or args.blend):
        parser.error("--map uses the default Poly Haven validation set; omit --asset/--blend")
    installation = discover_cs2()
    project = Project(
        "cs2asset_validation",
        installation.content_dir / "csgo_addons/cs2asset_validation",
        installation.game_dir / "csgo_addons/cs2asset_validation",
    )
    project.content_dir.mkdir(parents=True, exist_ok=True)
    project.game_dir.mkdir(parents=True, exist_ok=True)
    records = {}
    inputs = args.asset or (
        [] if args.blend else ["concrete_floor_01", "sunset_jhbcentral", "dirty_football"]
    )
    with SourceRegistry(cache_dir(), offline=args.offline) as provider:
        for asset in inputs:
            records[asset] = import_asset(
                installation,
                project,
                provider,
                cache_dir(),
                asset,
                ImportOptions(resolution=args.resolution),
                phase=print,
            )
        if args.blend:
            records[f"blend:{args.blend_name}"] = create_blend_asset(
                installation,
                project,
                provider,
                cache_dir(),
                *args.blend,
                args.blend_name,
                ImportOptions(resolution=args.resolution),
                phase=print,
            )
        if args.rebuild:
            for key, record in list(records.items()):
                records[key] = rebuild_asset(
                    installation, project, provider, cache_dir(), record["id"], phase=print
                )[0]
    summary = {"project": project.as_dict(), "imports": records}
    logs = Path(".validation") / "end-to-end"
    logs.mkdir(parents=True, exist_ok=True)
    if args.map:
        summary["map"] = validation_map(installation, project, records, logs)
    (logs / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Evidence: {logs / 'summary.json'}")


if __name__ == "__main__":
    main()
