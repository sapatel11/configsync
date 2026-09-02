import hashlib
import os
from collections.abc import Iterable
from typing import Protocol

import httpx


class ArtifactStoreError(RuntimeError):
    """Raised when replicated artifact storage cannot satisfy an operation."""


class ArtifactStore(Protocol):
    def put(self, checksum: str, content: bytes) -> None: ...
    def get(self, checksum: str) -> bytes: ...


class ReplicatedArtifactStore:
    """Write to every storage node and read from the first healthy valid replica."""

    def __init__(self, node_urls: Iterable[str], timeout_seconds: float = 2.0):
        self.node_urls = [url.rstrip("/") for url in node_urls]
        if len(self.node_urls) < 2:
            raise ValueError("At least two storage nodes are required")
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls) -> "ReplicatedArtifactStore":
        raw = os.getenv(
            "CONFIGSYNC_STORAGE_NODES",
            "http://127.0.0.1:8101,http://127.0.0.1:8102",
        )
        return cls(url.strip() for url in raw.split(",") if url.strip())

    def put(self, checksum: str, content: bytes) -> None:
        failures: list[str] = []
        for node_url in self.node_urls:
            try:
                response = httpx.put(
                    f"{node_url}/artifacts/{checksum}",
                    content=content,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                failures.append(f"{node_url}: {exc}")

        if failures:
            raise ArtifactStoreError(
                "Artifact was not replicated to every storage node: "
                + "; ".join(failures)
            )

    def get(self, checksum: str) -> bytes:
        failures: list[str] = []
        for node_url in self.node_urls:
            try:
                response = httpx.get(
                    f"{node_url}/artifacts/{checksum}",
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                content = response.content
                if hashlib.sha256(content).hexdigest() != checksum:
                    failures.append(f"{node_url}: checksum mismatch")
                    continue
                return content
            except httpx.HTTPError as exc:
                failures.append(f"{node_url}: {exc}")

        raise ArtifactStoreError(
            "No healthy storage replica returned a valid artifact: "
            + "; ".join(failures)
        )
