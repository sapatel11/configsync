import hashlib
import json
import time
from pathlib import Path
from typing import Protocol

import httpx


class HttpClient(Protocol):
    def get(self, url: str): ...
    def put(self, url: str, **kwargs): ...


def serialize_content(content: dict) -> bytes:
    return json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")


def validate_config(content: dict) -> None:
    """Apply a small deterministic validation rule useful for rollout demos."""
    port = content.get("port")
    if port is not None and (not isinstance(port, int) or not 1 <= port <= 65535):
        raise ValueError("port must be an integer between 1 and 65535")


class ConfigSyncAgent:
    """Poll desired state and converge local state toward it."""

    def __init__(
        self,
        customer: str,
        config_name: str,
        control_plane_url: str,
        state_dir: Path,
        poll_interval_seconds: float = 2.0,
        client: HttpClient | None = None,
    ) -> None:
        self.customer = customer
        self.config_name = config_name
        self.control_plane_url = control_plane_url.rstrip("/")
        self.state_dir = state_dir
        self.poll_interval_seconds = poll_interval_seconds
        self.client = client or httpx.Client(timeout=3.0)

    @property
    def config_path(self) -> Path:
        return self.state_dir / self.customer / f"{self.config_name}.json"

    @property
    def metadata_path(self) -> Path:
        return self.state_dir / self.customer / f"{self.config_name}.state.json"

    def current_version(self) -> int:
        try:
            metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            return int(metadata["version"])
        except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
            return 0

    def report_state(self, applied_version: int, status: str, error: str | None = None) -> None:
        response = self.client.put(
            f"{self.control_plane_url}/customers/{self.customer}/configs/"
            f"{self.config_name}/status",
            json={
                "applied_version": applied_version,
                "status": status,
                "error": error,
            },
        )
        response.raise_for_status()

    def activate(self, content: dict, version: int, checksum: str) -> None:
        customer_dir = self.config_path.parent
        customer_dir.mkdir(parents=True, exist_ok=True)

        config_tmp = self.config_path.with_suffix(".json.tmp")
        config_tmp.write_bytes(serialize_content(content))
        config_tmp.replace(self.config_path)

        metadata_tmp = self.metadata_path.with_suffix(".json.tmp")
        metadata_tmp.write_text(
            json.dumps({"version": version, "checksum": checksum}, sort_keys=True),
            encoding="utf-8",
        )
        metadata_tmp.replace(self.metadata_path)

    def reconcile_once(self) -> bool:
        """Perform one reconciliation pass. Return True when local state changes."""
        applied_version = self.current_version()
        try:
            response = self.client.get(
                f"{self.control_plane_url}/configs/{self.config_name}"
            )
            response.raise_for_status()
            desired = response.json()

            artifact = serialize_content(desired["content"])
            actual_checksum = hashlib.sha256(artifact).hexdigest()
            if actual_checksum != desired["checksum"]:
                raise ValueError("desired configuration checksum mismatch")

            validate_config(desired["content"])

            if desired["version"] == applied_version:
                self.report_state(applied_version, "synced")
                return False

            self.activate(
                desired["content"],
                desired["version"],
                desired["checksum"],
            )
            self.report_state(desired["version"], "synced")
            return True
        except Exception as exc:
            try:
                self.report_state(applied_version, "error", str(exc))
            except Exception:
                pass
            return False

    def run_forever(self) -> None:
        while True:
            self.reconcile_once()
            time.sleep(self.poll_interval_seconds)
