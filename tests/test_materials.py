import numpy as np
import pytest

from cs2asset.errors import CS2AssetError
from cs2asset.images import linear_to_srgb, read_image, write_image
from cs2asset.materials import create_material, create_sky


def test_missing_maps_use_correct_constants_and_color_space(tmp_path):
    material, warnings = create_material(
        tmp_path,
        "materials/test",
        {},
        constants={"base_color": [0.18, 0.18, 0.18, 1], "roughness": 0.7, "metalness": 0.3},
    )
    assert material.is_file()
    assert len(warnings) == 5
    np.testing.assert_allclose(
        read_image(material.parent / "base_color.png"),
        linear_to_srgb(np.array(0.18)),
        atol=2 / 65535,
    )
    np.testing.assert_allclose(read_image(material.parent / "roughness.png"), 0.7, atol=2 / 65535)
    np.testing.assert_allclose(read_image(material.parent / "metalness.png"), 0.3, atol=2 / 65535)
    np.testing.assert_allclose(
        read_image(material.parent / "normal.png")[0, 0], [0.5, 0.5, 1], atol=2 / 65535
    )
    np.testing.assert_allclose(read_image(material.parent / "ao.png"), 1)
    assert "F_ALPHA_TEST" not in material.read_text()


@pytest.mark.parametrize("channels", [2, 4])
def test_alpha_uses_source_alpha_and_color_stays_unpremultiplied(tmp_path, channels):
    rgba = [0.8, 0.4, 0.2, 0.25] if channels == 4 else [0.8, 0.25]
    source = write_image(tmp_path / "cutout.png", np.broadcast_to(rgba, (4, 4, channels)))
    material, warnings = create_material(tmp_path, "materials/cutout", {"base_color": source})
    text = material.read_text()
    assert 'F_ALPHA_TEST "1"' in text
    assert 'TextureTranslucency "materials/cutout/opacity.png"' in text
    np.testing.assert_allclose(read_image(material.parent / "opacity.png"), 0.25, atol=2 / 65535)
    expected_color = rgba[:3] if channels == 4 else [0.8, 0.8, 0.8]
    np.testing.assert_allclose(
        read_image(material.parent / "base_color.png")[0, 0], expected_color, atol=2 / 65535
    )
    assert any("alpha testing" in warning for warning in warnings)


def test_explicit_opacity_overrides_color_alpha(tmp_path):
    color = write_image(tmp_path / "color.png", np.broadcast_to([1, 0, 0, 0.2], (4, 4, 4)))
    opacity = write_image(tmp_path / "opacity.png", np.full((4, 4, 1), 0.8))
    material, _ = create_material(
        tmp_path, "materials/explicit", {"base_color": color, "opacity": opacity}
    )
    np.testing.assert_allclose(read_image(material.parent / "opacity.png"), 0.8, atol=2 / 65535)


def test_sky_preserves_radiance_and_applies_exposure(tmp_path):
    pixels = np.full((4, 8, 3), 8.0, dtype=np.float32)
    source = write_image(tmp_path / "sun.exr", pixels, hdr=True)
    sky, info = create_sky(tmp_path, "materials/skybox/test", source, yaw=12.5, exposure=2)
    np.testing.assert_allclose(read_image(sky.parent / "sky.exr"), 32.0)
    assert info["maximum_radiance"] == 32
    assert info["panorama_width"] == 8
    assert info["expected_face_size"] == 2
    assert 'SkyTexture "materials/skybox/test/sky.exr"' in sky.read_text()
    assert 'shader "sky.vfx"' in sky.read_text()


def test_sky_rejects_tonemapped_preview_and_wrong_projection(tmp_path):
    preview = write_image(tmp_path / "preview.png", np.ones((4, 8, 3)))
    with pytest.raises(CS2AssetError, match="tonemapped"):
        create_sky(tmp_path, "materials/skybox/test", preview)
    cube = write_image(tmp_path / "cube.exr", np.ones((4, 4, 3)), hdr=True)
    with pytest.raises(CS2AssetError, match="2:1"):
        create_sky(tmp_path, "materials/skybox/test", cube)


@pytest.mark.parametrize("tiling", [0, -1, float("nan"), float("inf")])
def test_invalid_tiling_rejected(tmp_path, tiling):
    with pytest.raises(CS2AssetError, match="tiling"):
        create_material(tmp_path, "materials/test", {}, tiling=tiling)
