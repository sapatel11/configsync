from control_plane.artifact_store import ArtifactStoreError


class MemoryArtifactStore:
    """Small deterministic artifact store used by control-plane tests."""

    def __init__(self):
        self.artifacts: dict[str, bytes] = {}

    def put(self, checksum: str, content: bytes) -> None:
        self.artifacts[checksum] = content

    def get(self, checksum: str) -> bytes:
        try:
            return self.artifacts[checksum]
        except KeyError as exc:
            raise ArtifactStoreError("artifact missing") from exc
