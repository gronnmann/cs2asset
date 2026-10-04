import numpy as np
import pytest

from cs2asset.errors import CS2AssetError
from cs2asset.images import linear_to_srgb, normalize_map, read_image, rotate_panorama, write_image


def test_straight_alpha_preserves_color_channels(tmp_path):
    pixels = np.broadcast_to([0.8, 0.4, 0.2, 0.25], (3, 4, 4)).copy()
    source = write_image(tmp_path / "cutout.png", pixels)
    np.testing.assert_allclose(read_image(source), pixels, atol=2 / 65535)


def test_hdr_roundtrip_preserves_range(tmp_path):
    pixels = np.array([[[0, 0.125, 4], [12, 256, 2048]]], dtype=np.float32)
    source = write_image(tmp_path / "lighting.exr", pixels, hdr=True)
    np.testing.assert_allclose(read_image(source), pixels, rtol=0.001)


@pytest.mark.parametrize("value", [-0.1, 65505, float("inf"), float("nan")])
def test_invalid_hdr_values_rejected(tmp_path, value):
    with pytest.raises(CS2AssetError):
        write_image(tmp_path / "bad.exr", np.full((2, 2, 3), value), hdr=True)


@pytest.mark.parametrize("shape", [(0, 3, 3), (3,), (2, 2, 5), (2, 2, 2, 2)])
def test_invalid_image_shapes_rejected(tmp_path, shape):
    with pytest.raises(CS2AssetError, match="nonempty image"):
        write_image(tmp_path / "bad.png", np.zeros(shape))


def test_periodic_yaw_and_fractional_seam_interpolation():
    pixels = np.array([[[0.0], [2.0], [4.0], [6.0]]])
    np.testing.assert_array_equal(rotate_panorama(pixels, 0), pixels)
    np.testing.assert_array_equal(rotate_panorama(pixels, 360), pixels)
    np.testing.assert_array_equal(rotate_panorama(pixels, -90), np.roll(pixels, -1, axis=1))
    np.testing.assert_array_equal(rotate_panorama(pixels, 450), np.roll(pixels, 1, axis=1))
    np.testing.assert_array_equal(rotate_panorama(pixels, 45), [[[3.0], [1.0], [3.0], [5.0]]])


@pytest.mark.parametrize("yaw", [float("nan"), float("inf")])
def test_invalid_yaw_rejected(yaw):
    with pytest.raises(CS2AssetError, match="finite"):
        rotate_panorama(np.zeros((2, 4, 3)), yaw)


def test_normal_conversion_changes_only_green(tmp_path):
    normal = np.broadcast_to([0.3, 0.7, 0.9], (4, 4, 3)).copy()
    source = write_image(tmp_path / "dx.png", normal)
    converted = normalize_map(source, tmp_path / "gl.png", "normal", normal_format="dx")
    expected = normal.copy()
    expected[:, :, 1] = 1 - expected[:, :, 1]
    np.testing.assert_allclose(read_image(converted), expected, atol=2 / 65535)
    unchanged = normalize_map(source, tmp_path / "unchanged.png", "normal", normal_format="gl")
    np.testing.assert_allclose(read_image(unchanged), normal, atol=2 / 65535)


def test_linear_color_conversion_leaves_data_maps_linear(tmp_path):
    linear = np.full((4, 4, 3), 0.18, dtype=np.float32)
    source = write_image(tmp_path / "linear.exr", linear, hdr=True)
    color = normalize_map(source, tmp_path / "color.png", "base_color")
    roughness = normalize_map(source, tmp_path / "roughness.png", "roughness")
    np.testing.assert_allclose(read_image(color), linear_to_srgb(linear), atol=0.0002)
    np.testing.assert_allclose(read_image(roughness), linear[:, :, :1], atol=0.0002)


def test_normal_needs_rgb_and_unknown_roles_are_rejected(tmp_path):
    source = write_image(tmp_path / "gray.png", np.full((2, 2), 0.5))
    with pytest.raises(CS2AssetError, match="RGB"):
        normalize_map(source, tmp_path / "normal.png", "normal")
    with pytest.raises(CS2AssetError, match="Unknown"):
        normalize_map(source, tmp_path / "map.png", "arm")


def test_gray_alpha_becomes_rgb_without_using_alpha_as_green(tmp_path):
    source = write_image(tmp_path / "gray_alpha.png", np.broadcast_to([0.6, 0.2], (4, 4, 2)))
    normalized = normalize_map(source, tmp_path / "rgb.png", "base_color")
    np.testing.assert_allclose(read_image(normalized), 0.6, atol=2 / 65535)
