from pathlib import Path

from fastapi.testclient import TestClient

from agent.reconciler import ConfigSyncAgent
from control_plane.main import create_app
from tests.helpers import MemoryArtifactStore


def test_candidate_version_does_not_bypass_staged_desired_state(tmp_path: Path) -> None:
    app = create_app(
        f"sqlite:///{tmp_path / 'test.db'}",
        artifact_store=MemoryArtifactStore(),
    )

    with TestClient(app) as client:
        create = client.post(
            "/configs/firewall",
            json={"content": {"service": "firewall", "port": 443}},
        )
        assert create.status_code == 201

        agent = ConfigSyncAgent(
            customer="customer-a",
            config_name="firewall",
            control_plane_url="http://testserver",
            state_dir=tmp_path / "agent-state",
            client=client,
        )

        assert agent.reconcile_once() is True
        assert agent.current_version() == 1

        update = client.put(
            "/configs/firewall",
            headers={"If-Match": "1"},
            json={"content": {"service": "firewall", "port": 8443}},
        )
        assert update.status_code == 200
        assert update.json()["version"] == 2

        newest = client.get("/configs/firewall")
        desired = client.get(
            "/customers/customer-a/configs/firewall/desired"
        )
        assert newest.json()["version"] == 2
        assert desired.json()["version"] == 1

        assert agent.reconcile_once() is False
        assert agent.current_version() == 1

        status_response = client.get(
            "/customers/customer-a/configs/firewall/status"
        )
        assert status_response.status_code == 200
        assert status_response.json()["applied_version"] == 1
        assert status_response.json()["status"] == "synced"
