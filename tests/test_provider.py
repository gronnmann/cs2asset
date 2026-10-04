import json
from pathlib import Path

import httpx
import pytest

from cs2asset.errors import CS2AssetError
from cs2asset.provider import PolyHavenProvider, parse_asset_id

FIXTURES = Path(__file__).parent / "fixtures" / "polyhaven"


@pytest.fixture
def provider(tmp_path):
    def respond(request):
        endpoint, asset_id = request.url.path.strip("/").split("/")
        path = FIXTURES / f"{asset_id}_{endpoint}.json"
        return httpx.Response(200, json=json.loads(path.read_text(encoding="utf-8")))

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        yield PolyHavenProvider(tmp_path, client=client, retries=1)


@pytest.mark.parametrize(
    "value",
    [
        "dirty_football",
        "polyhaven:dirty_football",
        "https://polyhaven.com/a/dirty_football",
        "https://polyhaven.com/pl/a/dirty_football/",
    ],
)
def test_asset_id(value):
    assert parse_asset_id(value) == "dirty_football"


@pytest.mark.parametrize(
    "value",
    [
        "../bad",
        "a/b",
        "polyhaven:",
        "https://evil.com/a/good",
        "https://polyhaven.com/collections/test",
        "other:thing",
    ],
)
def test_invalid_asset_id(value):
    with pytest.raises(CS2AssetError):
        parse_asset_id(value)


def test_live_texture_fixture(provider):
    resolved = provider.resolve_files("concrete_floor_01", "1k")
    roles = {file.key: file for file in resolved.files}
    assert {"base_color", "normal", "roughness", "height"} <= roles.keys()
    assert roles["base_color"].relative_path.endswith(".png")
    assert "nor_gl" in roles["normal"].url
    assert resolved.choices["normal_convention"] == "opengl"
    assert resolved.asset.dimensions
    assert resolved.total_size > 0
    assert all(file.md5 and file.size for file in resolved.files)


def test_live_model_dependencies(provider):
    resolved = provider.resolve_files("dirty_football", "1k")
    assert resolved.asset.type == "models"
    assert resolved.files[0].key == "model"
    assert resolved.files[0].relative_path.endswith(".blend")
    assert len(resolved.files) == 4
    assert all(file.relative_path.startswith("textures/") for file in resolved.files[1:])


def test_live_hdri_fixture(provider):
    resolved = provider.resolve_files("sunset_jhbcentral")
    assert resolved.resolution == "4k"
    assert len(resolved.files) == 1
    assert resolved.files[0].key == "hdri"
    assert resolved.files[0].relative_path.endswith(".exr")


def test_unavailable_resolution(provider):
    with pytest.raises(CS2AssetError, match="Available resolutions: 1k, 2k, 4k, 8k"):
        provider.resolve_files("concrete_floor_01", "3k")


def test_search_preserves_rank_and_uses_cache(tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers["User-Agent"].startswith("cs2asset/")
        assert request.url.params["q"] == "rock"
        assert request.url.params["t"] == "models"
        return httpx.Response(200, json={"results": [{"slug": "second"}, {"slug": "first"}]})

    client = httpx.Client(transport=httpx.MockTransport(respond))
    provider = PolyHavenProvider(tmp_path, client=client, retries=1)
    provider.cache.put(
        "/assets", {"first": {"name": "First", "type": 2}, "second": {"name": "Second", "type": 2}}
    )
    assert [asset.id for asset in provider.search(" ROCK ", "models")] == ["second", "first"]
    provider.search("rock", "models")
    assert len(calls) == 1


def test_offline_keyword_fallback(tmp_path):
    def respond(request):
        raise httpx.ConnectError("offline", request=request)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    provider = PolyHavenProvider(tmp_path, client=client, ttl=0, retries=1)
    provider.cache.put(
        "/assets",
        {
            "mossy_rock": {"name": "Mossy rock", "type": 2},
            "rock_floor": {"name": "Rock floor", "type": 1},
        },
    )
    assert [asset.id for asset in provider.search("rock", "models")] == ["mossy_rock"]
    assert provider.last_warning


def test_malicious_dependency_rejected(provider):
    files = provider.get_files("dirty_football")
    entry = files["blend"]["1k"]["blend"]
    entry["include"]["../../outside.jpg"] = next(iter(entry["include"].values()))
    provider.cache.put("/files/dirty_football", files)
    with pytest.raises(CS2AssetError, match="Unsafe"):
        provider.resolve_files("dirty_football", "1k")


def test_missing_map_and_known_packed_map(provider):
    files = provider.get_files("concrete_floor_01")
    files.pop("nor_gl", None)
    files.pop("nor_dx", None)
    files.pop("Rough", None)
    provider.cache.put("/files/concrete_floor_01", files)
    resolved = provider.resolve_files("concrete_floor_01", "1k")
    assert "normal" not in {file.key for file in resolved.files}
    assert any("No normal" in warning for warning in resolved.warnings)
    if "arm" in files:
        assert resolved.choices["arm_channels"]["roughness"] == "G"


def test_refresh_fetches_again(provider):
    asset = provider.get_asset("dirty_football")
    assert provider.get_asset("dirty_football", refresh=True) == asset


def test_offline_metadata_never_calls_network(tmp_path):
    def respond(request):
        pytest.fail("Offline mode must never contact Poly Haven")

    client = httpx.Client(transport=httpx.MockTransport(respond))
    provider = PolyHavenProvider(tmp_path, client=client, ttl=-1, offline=True)
    provider.cache.put("/info/dirty_football", {"type": 2, "name": "Football"})
    assert provider.get_asset("dirty_football", refresh=True).name == "Football"
    with pytest.raises(CS2AssetError, match="run online first"):
        provider.get_asset("missing")


def test_fallback_to_fbx_at_requested_resolution(provider):
    files = provider.get_files("dirty_football")
    del files["blend"]["1k"]
    files.pop("gltf", None)
    provider.cache.put("/files/dirty_football", files)
    resolved = provider.resolve_files("dirty_football", "1k")
    assert resolved.choices["format"] == "fbx"
    assert resolved.files[0].relative_path.endswith(".fbx")
    assert len(resolved.files) == 4


def test_directx_normal_is_explicit(provider):
    files = provider.get_files("concrete_floor_01")
    files.pop("nor_gl")
    provider.cache.put("/files/concrete_floor_01", files)
    assert provider.resolve_files("concrete_floor_01").choices["normal_convention"] == "directx"


def test_missing_md5_is_allowed_but_bad_md5_is_not(provider):
    files = provider.get_files("sunset_jhbcentral")
    del files["hdri"]["1k"]["exr"]["md5"]
    provider.cache.put("/files/sunset_jhbcentral", files)
    assert provider.resolve_files("sunset_jhbcentral", "1k").files[0].md5 is None
    files["hdri"]["1k"]["exr"]["md5"] = "not a checksum"
    provider.cache.put("/files/sunset_jhbcentral", files)
    with pytest.raises(CS2AssetError, match="MD5"):
        provider.resolve_files("sunset_jhbcentral", "1k")


def test_malformed_api_json_is_actionable(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
    provider = PolyHavenProvider(tmp_path, client=client, retries=1)
    with pytest.raises(CS2AssetError, match="Expected a JSON object"):
        provider.get_asset("bad")
