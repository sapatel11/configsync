import json
from pathlib import Path

from fastapi.testclient import TestClient

from agent.reconciler import ConfigSyncAgent
from control_plane.main import create_app
from tests.helpers import MemoryArtifactStore


def test_agent_eventually_converges_to_new_desired_version(tmp_path: Path) -> None:
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

        assert agent.current_version() == 0
        assert agent.reconcile_once() is True

        initial_status = client.get(
            "/customers/customer-a/configs/firewall/status"
        )
        assert initial_status.status_code == 200
        assert initial_status.json()["applied_version"] == 1

        update = client.put(
            "/configs/firewall",
            headers={"If-Match": "1"},
            json={"content": {"service": "firewall", "port": 8443}},
        )
        assert update.status_code == 200
        assert update.json()["version"] == 2

        before_reconcile = client.get(
            "/customers/customer-a/configs/firewall/status"
        )
        assert before_reconcile.json()["applied_version"] == 1

        assert agent.reconcile_once() is True

        after_reconcile = client.get(
            "/customers/customer-a/configs/firewall/status"
        )
        assert after_reconcile.json()["applied_version"] == 2
        assert after_reconcile.json()["status"] == "synced"

    activated = json.loads(agent.config_path.read_text(encoding="utf-8"))
    assert activated["port"] == 8443
    assert agent.current_version() == 2


def test_invalid_desired_config_preserves_previous_actual_state(tmp_path: Path) -> None:
    app = create_app(
        f"sqlite:///{tmp_path / 'test.db'}",
        artifact_store=MemoryArtifactStore(),
    )

    with TestClient(app) as client:
        client.post(
            "/configs/firewall",
            json={"content": {"service": "firewall", "port": 443}},
        )
        agent = ConfigSyncAgent(
            customer="customer-a",
            config_name="firewall",
            control_plane_url="http://testserver",
            state_dir=tmp_path / "agent-state",
            client=client,
        )
        assert agent.reconcile_once() is True

        update = client.put(
            "/configs/firewall",
            headers={"If-Match": "1"},
            json={"content": {"service": "firewall", "port": 70000}},
        )
        assert update.status_code == 200
        assert update.json()["version"] == 2

        assert agent.reconcile_once() is False
        status_response = client.get(
            "/customers/customer-a/configs/firewall/status"
        )

    assert agent.current_version() == 1
    assert status_response.status_code == 200
    assert status_response.json()["applied_version"] == 1
    assert status_response.json()["status"] == "error"
    assert "port must be" in status_response.json()["error"]
