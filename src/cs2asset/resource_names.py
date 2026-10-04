"""Readable, portable resource basenames shared with the Blender worker."""

import re
import unicodedata


def resource_name(value: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    name = re.sub(r"[^a-z0-9_-]+", "_", ascii_name.lower()).strip("_")[:64] or "asset"
    if name in {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(10)),
        *(f"lpt{i}" for i in range(10)),
    }:
        name = "asset_" + name
    return name


def model_material_names(asset_name: str, source_names: list[str]) -> list[str]:
    base = resource_name(asset_name)
    if len(source_names) == 1:
        return [base]
    names = [resource_name(f"{base}_{source}") for source in source_names]
    result = []
    for index, name in enumerate(names):
        candidate = name
        if names.count(name) > 1:
            attempt = 0
            while True:
                suffix = f"_m{index:03d}" + (f"_{attempt}" if attempt else "")
                candidate = name[: 64 - len(suffix)] + suffix
                if candidate not in names and candidate not in result:
                    break
                attempt += 1
        result.append(candidate)
    return result
