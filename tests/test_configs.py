from pathlib import Path

from fastapi.testclient import TestClient

from control_plane.main import create_app
from tests.helpers import MemoryArtifactStore


def test_create_then_get_configuration(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    app = create_app(database_url, MemoryArtifactStore())

    with TestClient(app) as client:
        create_response = client.post(
            "/configs/firewall",
            json={
                "content": {
                    "service": "firewall",
                    "port": 443,
                    "enabled": True,
                }
            },
        )
        get_response = client.get("/configs/firewall")

    assert create_response.status_code == 201
    assert create_response.json()["version"] == 1
    assert len(create_response.json()["checksum"]) == 64
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "firewall"
    assert get_response.json()["content"]["port"] == 443


def test_duplicate_configuration_returns_conflict(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    app = create_app(database_url, MemoryArtifactStore())

    with TestClient(app) as client:
        first_response = client.post(
            "/configs/firewall",
            json={"content": {"port": 443}},
        )
        duplicate_response = client.post(
            "/configs/firewall",
            json={"content": {"port": 8443}},
        )

    assert first_response.status_code == 201
    assert duplicate_response.status_code == 409
    assert duplicate_response.json() == {
        "detail": "Configuration 'firewall' already exists"
    }


def test_missing_configuration_returns_not_found(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    app = create_app(database_url, MemoryArtifactStore())

    with TestClient(app) as client:
        response = client.get("/configs/missing")

    assert response.status_code == 404
