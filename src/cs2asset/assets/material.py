"""Conventional texture-name semantics, independent of any provider."""

import re
from pathlib import PurePosixPath

from cs2asset.errors import CS2AssetError

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tga", ".tif", ".tiff", ".bmp", ".exr"}
ALIASES = {
    "base_color": ("base_color", "basecolor", "albedo", "diffuse", "color", "diff"),
    "normal": ("normalgl", "normaldx", "nor_gl", "nor_dx", "normal", "nrm"),
    "roughness": ("roughness", "rough"),
    "metalness": ("metallic", "metalness", "metal"),
    "ao": ("ambient_occlusion", "ao"),
    "height": ("displacement", "height", "disp"),
    "opacity": ("opacity", "alpha"),
    "arm": ("arm", "orm"),
}


def match_map(name: str):
    path = PurePosixPath(name.replace("\\", "/"))
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        return None
    stem = re.sub(r"[ .-]+", "_", path.stem.lower())
    stem = re.sub(r"_(?:\d+k|\d+x\d+)$", "", stem)
    for role, aliases in ALIASES.items():
        for alias in aliases:
            # Also accept normal_gl/normal_dx, while keeping generic normal explicit.
            pattern = alias.replace("_", "_?")
            if alias in {"normalgl", "normaldx"}:
                pattern = alias.replace("normal", "normal_?")
            match = re.fullmatch(rf"(?:(.*?)_)?({pattern})", stem)
            if match:
                convention = (
                    "directx"
                    if alias in {"normaldx", "nor_dx"}
                    else "opengl"
                    if alias in {"normalgl", "nor_gl"}
                    else None
                )
                return role, (path.parent.as_posix(), match[1] or ""), convention
    return None


def discover_maps(names: list[str]):
    maps, groups, convention, warnings = {}, set(), None, []
    normals = []
    for name in sorted(names, key=str.casefold):
        match = match_map(name)
        if not match:
            continue
        role, group, normal = match
        groups.add(group)
        if role == "normal":
            normals.append((name, group, normal))
            continue
        if role in maps:
            raise CS2AssetError(
                f"Ambiguous {role} maps: {maps[role]}, {name}. "
                "Keep one texture set and one map per semantic in the folder."
            )
        maps[role] = name
    if normals:
        conventions = [item[2] for item in normals]
        if len(normals) > 1 and (len(normals) != 2 or set(conventions) != {"opengl", "directx"}):
            raise CS2AssetError("Ambiguous normal maps: " + ", ".join(n[0] for n in normals))
        selected = next((n for n in normals if n[2] == "opengl"), normals[0])
        maps["normal"], _, convention = selected
        if len(normals) == 2:
            warnings.append(f"Both DX and GL normals found; selected {selected[0]} (OpenGL).")
    if len(groups) > 1:
        raise CS2AssetError(
            "Multiple texture sets found: "
            + ", ".join(maps.values())
            + ". Put each material's maps in its own folder."
        )
    if not maps or "base_color" not in maps:
        raise CS2AssetError(
            "No recognizable PBR material set. Add an albedo/basecolor/diffuse/color map "
            "and optional normal, roughness, metalness, AO, height, or opacity maps."
        )
    if "normal" in maps and convention is None:
        warnings.append(
            "Normal convention is unspecified; assuming OpenGL. Use --normal-format dx if needed."
        )
    return (
        maps,
        {
            "normal_convention": convention or "opengl",
            "normal_convention_explicit": convention is not None,
            "normal_map": maps.get("normal"),
        },
        tuple(warnings),
    )
