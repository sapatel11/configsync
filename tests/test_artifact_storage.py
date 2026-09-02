import hashlib

import httpx

from control_plane.artifact_store import ReplicatedArtifactStore


def response(status_code: int, url: str, content: bytes = b"") -> httpx.Response:
    return httpx.Response(
        status_code,
        content=content,
        request=httpx.Request("GET", url),
    )


def test_write_replicates_to_both_nodes(monkeypatch) -> None:
    calls: list[str] = []

    def fake_put(url: str, content: bytes, timeout: float):
        calls.append(url)
        return response(204, url)

    monkeypatch.setattr(httpx, "put", fake_put)
    store = ReplicatedArtifactStore(["http://storage-a", "http://storage-b"])
    content = b'{"port":443}'
    checksum = hashlib.sha256(content).hexdigest()

    store.put(checksum, content)

    assert calls == [
        f"http://storage-a/artifacts/{checksum}",
        f"http://storage-b/artifacts/{checksum}",
    ]


def test_read_falls_back_to_second_node_when_first_is_unavailable(monkeypatch) -> None:
    content = b'{"port":443}'
    checksum = hashlib.sha256(content).hexdigest()
    calls: list[str] = []

    def fake_get(url: str, timeout: float):
        calls.append(url)
        if url.startswith("http://storage-a"):
            raise httpx.ConnectError(
                "storage-a is down",
                request=httpx.Request("GET", url),
            )
        return response(200, url, content)

    monkeypatch.setattr(httpx, "get", fake_get)
    store = ReplicatedArtifactStore(["http://storage-a", "http://storage-b"])

    artifact = store.get(checksum)

    assert artifact == content
    assert calls == [
        f"http://storage-a/artifacts/{checksum}",
        f"http://storage-b/artifacts/{checksum}",
    ]


def test_read_rejects_corrupt_first_replica_and_uses_second(monkeypatch) -> None:
    content = b'{"port":443}'
    checksum = hashlib.sha256(content).hexdigest()

    def fake_get(url: str, timeout: float):
        if url.startswith("http://storage-a"):
            return response(200, url, b"corrupt")
        return response(200, url, content)

    monkeypatch.setattr(httpx, "get", fake_get)
    store = ReplicatedArtifactStore(["http://storage-a", "http://storage-b"])

    assert store.get(checksum) == content
