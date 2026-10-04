import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from cs2asset.cache import DownloadCache, MetadataCache, safe_relative_path
from cs2asset.errors import CS2AssetError
from cs2asset.provider import DownloadFile


def item(payload=b"asset", path="textures/image.png", key="image"):
    return DownloadFile(
        key,
        "https://example.com/image.png",
        path,
        len(payload),
        hashlib.md5(payload, usedforsecurity=False).hexdigest(),
    )


@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "x/../../escape",
        "/absolute",
        "C:/absolute",
        "C:relative",
        "\\\\server\\path",
        "foo:bar",
        "foo/CON.jpg",
        "a/./b",
        "a//b",
        "trailing.",
        "a/nul.txt",
        "a/thing ",
        "a\x00b",
    ],
)
def test_unsafe_paths(path):
    with pytest.raises(CS2AssetError):
        safe_relative_path(path)


def test_valid_windows_dependency():
    assert safe_relative_path("textures\\image.png") == "textures/image.png"


def test_verified_cache_hit_and_corrupt_cache_redownload(tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, content=b"asset")

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        cache = DownloadCache(tmp_path, client=client, retries=1)
        path = cache.download(item())
        assert cache.download(item()) == path
        assert len(calls) == 1
        path.write_bytes(b"wrong")
        assert cache.download(item()).read_bytes() == b"asset"
        assert len(calls) == 2
        record = json.loads(path.with_suffix(".png.json").read_text())
        assert record["sha256"] == hashlib.sha256(b"asset").hexdigest()


@pytest.mark.parametrize("content", [b"short", b"too much data", b"wrong"])
def test_size_and_checksum_failures_never_promoted(tmp_path, content):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=content))
    )
    cache = DownloadCache(tmp_path, client=client, retries=1)
    with pytest.raises(CS2AssetError):
        cache.download(item())
    assert not list(cache.directory.glob("*.png"))
    assert not list(cache.directory.glob("*.part"))


def test_materialize_preserves_dependencies(tmp_path):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"asset"))
    )
    cache = DownloadCache(tmp_path / "cache", client=client, retries=1)
    result = cache.materialize([item()], tmp_path / "model")
    assert result["image"] == tmp_path / "model" / "textures" / "image.png"
    assert result["image"].read_bytes() == b"asset"


def test_retry_after_is_observed(tmp_path, monkeypatch):
    calls, sleeps = [], []
    monkeypatch.setattr("cs2asset.cache.time.sleep", sleeps.append)

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, content=b"asset")

    client = httpx.Client(transport=httpx.MockTransport(respond))
    cache = DownloadCache(tmp_path, client=client)
    assert cache.download(item()).read_bytes() == b"asset"
    assert sleeps == [3]


def test_permanent_error_not_retried(tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    with pytest.raises(CS2AssetError, match="404"):
        DownloadCache(tmp_path, client=client).download(item())
    assert len(calls) == 1


def test_partial_response_rejected(tmp_path):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(206, content=b"asset"))
    )
    with pytest.raises(CS2AssetError, match="partial"):
        DownloadCache(tmp_path, client=client, retries=1).download(item())


def test_metadata_expiry_and_corruption(tmp_path):
    cache = MetadataCache(tmp_path, ttl=-1)
    cache.put("key", {"data": 2})
    assert cache.get("key") is None
    assert cache.get("key", stale=True) == {"data": 2}
    cache.path("key").write_text("broken")
    assert cache.get("key", stale=True) is None


def test_case_collisions_rejected_before_download(tmp_path):
    with DownloadCache(tmp_path) as cache, pytest.raises(CS2AssetError, match="Colliding"):
        cache.materialize(
            [item(path="a.PNG", key="a"), item(path="A.png", key="b")], tmp_path / "out"
        )


def test_no_md5_uses_recorded_sha256(tmp_path):
    entry = DownloadFile("image", "https://example.com/image.png", "image.png", 5)
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, content=b"asset")

    client = httpx.Client(transport=httpx.MockTransport(respond))
    cache = DownloadCache(tmp_path, client=client)
    path = cache.download(entry)
    assert cache.download(entry) == path
    assert len(calls) == 1
    path.write_bytes(b"wrong")
    assert cache.download(entry).read_bytes() == b"asset"
    assert len(calls) == 2


def test_offline_cache_hit_and_miss_never_call_network(tmp_path):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"asset"))
    )
    cache = DownloadCache(tmp_path, client=client)
    downloaded = cache.download(item())

    def respond(request):
        pytest.fail("Offline mode must never contact the download server")

    offline = DownloadCache(
        tmp_path, client=httpx.Client(transport=httpx.MockTransport(respond)), offline=True
    )
    assert offline.download(item()) == downloaded
    downloaded.write_bytes(b"wrong")
    with pytest.raises(CS2AssetError, match="No verified cached download"):
        offline.download(item())


def test_interrupted_partial_recovered_under_lock(tmp_path):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"asset"))
    )
    cache = DownloadCache(tmp_path, client=client)
    entry = item()
    identity = hashlib.sha256(f"{entry.url}\n{entry.md5}\n{entry.size}".encode()).hexdigest()
    orphan = cache.directory / (identity + "abandoned.part")
    orphan.write_bytes(b"broken partial")
    assert cache.download(entry).read_bytes() == b"asset"
    assert not orphan.exists()


def test_independent_caches_share_lock_and_download_once(tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, content=b"asset")

    client = httpx.Client(transport=httpx.MockTransport(respond))
    caches = [DownloadCache(tmp_path, client=client) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda cache: cache.download(item()), caches))
    assert len(set(paths)) == 1
    assert len(calls) == 1


def test_excessive_retry_after_fails_without_retrying_early(tmp_path, monkeypatch):
    def sleep(delay):
        pytest.fail("Should not wait a long time or ignore the requested delay")

    monkeypatch.setattr("cs2asset.cache.time.sleep", sleep)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(429, headers={"Retry-After": "3600"})
        )
    )
    with pytest.raises(CS2AssetError, match="try again later"):
        DownloadCache(tmp_path, client=client).download(item())
