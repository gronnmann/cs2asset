import os
from uuid import uuid4

import numpy as np
import pytest

from cs2asset.compiler import compile_resources, run_process
from cs2asset.discovery import discover_cs2
from cs2asset.images import write_image
from cs2asset.materials import create_material, create_sky

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("CS2ASSET_INTEGRATION") != "1",
        reason="Set CS2ASSET_INTEGRATION=1 to compile with local Valve tools",
    ),
]


def test_real_valve_hdr_cubemap_and_alpha_material(tmp_path):
    installation = discover_cs2()
    content = installation.content_dir / "csgo_addons/cs2asset_material_probe"
    game = installation.game_dir / "csgo_addons/cs2asset_material_probe"
    game.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    source = write_image(tmp_path / "sky.exr", np.full((128, 256, 3), 4.0), hdr=True)
    sky, detail = create_sky(content, f"materials/skybox/cs2asset/tests/{token}", source)
    color = write_image(
        tmp_path / "color.png",
        np.broadcast_to(np.array([0.8, 0.4, 0.2, 0.25]), (64, 64, 4)).copy(),
    )
    material, _ = create_material(
        content, f"materials/cs2asset/tests/{token}", {"base_color": color}
    )
    outputs = compile_resources(installation, content, game, [sky, material], tmp_path / "logs")
    sky_textures = [
        path
        for name, path in outputs.items()
        if f"/{token}/" in name and name.endswith(".vtex_c") and "sky_exr" in name
    ]
    assert len(sky_textures) == 1
    reader = installation.compiler.with_name("resourceinfo.exe")
    sky_info = run_process(
        [str(reader), "-game", str(installation.game_context), "-i", str(sky_textures[0]), "-all"],
        tmp_path / "sky-info.log",
    )
    assert "VTEX_FLAG_CUBE_TEXTURE" in sky_info
    assert "VTEX_FORMAT_BC6H" in sky_info
    assert "Reflectivity = ( 4, 4, 4, 0 )" in sky_info
    assert detail["expected_face_size"] == 64
    compiled_material = game / (material.relative_to(content).as_posix() + "_c")
    material_info = run_process(
        [
            str(reader),
            "-game",
            str(installation.game_context),
            "-i",
            str(compiled_material),
            "-all",
        ],
        tmp_path / "material-info.log",
    )
    assert 'm_name = "F_ALPHA_TEST"' in material_info
    assert "LightSim_Opacity_A" in material_info
