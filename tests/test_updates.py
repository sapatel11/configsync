from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from control_plane import models
from control_plane.database import create_database
from control_plane.main import create_app
from tests.helpers import MemoryArtifactStore


def create_test_app(database_url: str):
    return create_app(database_url, MemoryArtifactStore())


def create_firewall(client: TestClient) -> None:
    response = client.post(
        "/configs/firewall",
        json={"content": {"service": "firewall", "port": 443}},
    )
    assert response.status_code == 201


def test_update_with_matching_version_creates_next_snapshot(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    app = create_test_app(database_url)

    with TestClient(app) as client:
        create_firewall(client)
        response = client.put(
            "/configs/firewall",
            headers={"If-Match": "1"},
            json={"content": {"service": "firewall", "port": 8443}},
        )
        current = client.get("/configs/firewall")

    assert response.status_code == 200
    assert response.json()["version"] == 2
    assert response.json()["content"]["port"] == 8443
    assert current.status_code == 200
    assert current.json()["version"] == 2


def test_update_without_if_match_requires_precondition(tmp_path: Path) -> None:
    app = create_test_app(f"sqlite:///{tmp_path / 'test.db'}")
    with TestClient(app) as client:
        create_firewall(client)
        response = client.put("/configs/firewall", json={"content": {"port": 8443}})
    assert response.status_code == 428


def test_update_with_malformed_if_match_returns_bad_request(tmp_path: Path) -> None:
    app = create_test_app(f"sqlite:///{tmp_path / 'test.db'}")
    with TestClient(app) as client:
        create_firewall(client)
        response = client.put(
            "/configs/firewall",
            headers={"If-Match": "version-1"},
            json={"content": {"port": 8443}},
        )
    assert response.status_code == 400


def test_update_with_nonpositive_if_match_returns_bad_request(tmp_path: Path) -> None:
    app = create_test_app(f"sqlite:///{tmp_path / 'test.db'}")
    with TestClient(app) as client:
        create_firewall(client)
        response = client.put(
            "/configs/firewall",
            headers={"If-Match": "0"},
            json={"content": {"port": 8443}},
        )
    assert response.status_code == 400


def test_update_missing_configuration_returns_not_found(tmp_path: Path) -> None:
    app = create_test_app(f"sqlite:///{tmp_path / 'test.db'}")
    with TestClient(app) as client:
        response = client.put(
            "/configs/missing",
            headers={"If-Match": "1"},
            json={"content": {"port": 8443}},
        )
    assert response.status_code == 404


def test_stale_update_returns_conflict_and_keeps_winning_version(tmp_path: Path) -> None:
    app = create_test_app(f"sqlite:///{tmp_path / 'test.db'}")
    with TestClient(app) as client:
        create_firewall(client)
        winner = client.put(
            "/configs/firewall",
            headers={"If-Match": "1"},
            json={"content": {"port": 8443}},
        )
        stale = client.put(
            "/configs/firewall",
            headers={"If-Match": "1"},
            json={"content": {"port": 9443}},
        )
        current = client.get("/configs/firewall")

    assert winner.status_code == 200
    assert stale.status_code == 409
    assert current.json()["version"] == 2
    assert current.json()["content"]["port"] == 8443


def test_two_writers_from_same_version_allow_exactly_one_update(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    app = create_test_app(database_url)

    with TestClient(app) as client:
        create_firewall(client)

        def update_port(port: int):
            return client.put(
                "/configs/firewall",
                headers={"If-Match": "1"},
                json={"content": {"service": "firewall", "port": port}},
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(update_port, [8443, 9443]))

        current = client.get("/configs/firewall")

    assert sorted(response.status_code for response in responses) == [200, 409]
    assert current.status_code == 200
    assert current.json()["version"] == 2
    assert current.json()["content"]["port"] in {8443, 9443}

    engine, session_factory = create_database(database_url)
    with session_factory() as session:
        versions = session.scalars(
            select(models.ConfigVersion)
            .where(models.ConfigVersion.config_name == "firewall")
            .order_by(models.ConfigVersion.version)
        ).all()
    engine.dispose()

    assert [version.version for version in versions] == [1, 2]
    assert all(len(version.checksum) == 64 for version in versions)
