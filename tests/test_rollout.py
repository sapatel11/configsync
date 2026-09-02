import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from agent.reconciler import ConfigSyncAgent
from control_plane.main import create_app
from tests.helpers import MemoryArtifactStore


def _make_agent(client: TestClient, tmp_path: Path, customer: str) -> ConfigSyncAgent:
    return ConfigSyncAgent(
        customer=customer,
        config_name="firewall",
        control_plane_url="http://testserver",
        state_dir=tmp_path / "agent-state",
        client=client,
    )


def _drive_agents_until_done(future, *agents: ConfigSyncAgent):
    deadline = time.monotonic() + 3.0
    while not future.done() and time.monotonic() < deadline:
        for agent in agents:
            agent.reconcile_once()
        time.sleep(0.02)
    return future.result(timeout=1.0)


def test_canary_rollout_advances_customer_b_only_after_customer_a(tmp_path: Path) -> None:
    app = create_app(
        f"sqlite:///{tmp_path / 'test.db'}",
        artifact_store=MemoryArtifactStore(),
        rollout_timeout_seconds=1.0,
        rollout_poll_interval_seconds=0.02,
    )

    with TestClient(app) as client:
        client.post(
            "/configs/firewall",
            json={"content": {"service": "firewall", "port": 443}},
        )
        customer_a = _make_agent(client, tmp_path, "customer-a")
        customer_b = _make_agent(client, tmp_path, "customer-b")
        assert customer_a.reconcile_once() is True
        assert customer_b.reconcile_once() is True

        update = client.put(
            "/configs/firewall",
            headers={"If-Match": "1"},
            json={"content": {"service": "firewall", "port": 8443}},
        )
        assert update.status_code == 200

        assert client.get(
            "/customers/customer-a/configs/firewall/desired"
        ).json()["version"] == 1
        assert client.get(
            "/customers/customer-b/configs/firewall/desired"
        ).json()["version"] == 1

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(client.post, "/rollouts/firewall")
            rollout = _drive_agents_until_done(future, customer_a, customer_b)

        assert rollout.status_code == 200
        assert rollout.json()["status"] == "succeeded"
        assert rollout.json()["previous_version"] == 1
        assert rollout.json()["target_version"] == 2

        status_a = client.get(
            "/customers/customer-a/configs/firewall/status"
        ).json()
        status_b = client.get(
            "/customers/customer-b/configs/firewall/status"
        ).json()
        assert status_a["applied_version"] == 2
        assert status_b["applied_version"] == 2

        assert client.get(
            "/customers/customer-a/configs/firewall/desired"
        ).json()["version"] == 2
        assert client.get(
            "/customers/customer-b/configs/firewall/desired"
        ).json()["version"] == 2

        metrics = client.get("/metrics").text
        assert "rollouts_total 1.0" in metrics
        assert "rollout_failures_total 0.0" in metrics
        assert "rollbacks_total 0.0" in metrics


def test_failed_canary_restores_previous_desired_version(tmp_path: Path) -> None:
    app = create_app(
        f"sqlite:///{tmp_path / 'test.db'}",
        artifact_store=MemoryArtifactStore(),
        rollout_timeout_seconds=1.0,
        rollout_poll_interval_seconds=0.02,
    )

    with TestClient(app) as client:
        client.post(
            "/configs/firewall",
            json={"content": {"service": "firewall", "port": 443}},
        )
        customer_a = _make_agent(client, tmp_path, "customer-a")
        customer_b = _make_agent(client, tmp_path, "customer-b")
        assert customer_a.reconcile_once() is True
        assert customer_b.reconcile_once() is True

        update = client.put(
            "/configs/firewall",
            headers={"If-Match": "1"},
            json={"content": {"service": "firewall", "port": 70000}},
        )
        assert update.status_code == 200
        assert update.json()["version"] == 2

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(client.post, "/rollouts/firewall")
            rollout = _drive_agents_until_done(future, customer_a, customer_b)

        assert rollout.status_code == 200
        assert rollout.json()["status"] == "rolled_back"
        assert "port must be" in rollout.json()["error"]

        assert customer_a.current_version() == 1
        assert customer_b.current_version() == 1
        assert client.get(
            "/customers/customer-a/configs/firewall/desired"
        ).json()["version"] == 1
        assert client.get(
            "/customers/customer-b/configs/firewall/desired"
        ).json()["version"] == 1

        customer_a.reconcile_once()
        status_a = client.get(
            "/customers/customer-a/configs/firewall/status"
        ).json()
        assert status_a["applied_version"] == 1
        assert status_a["status"] == "synced"

        metrics = client.get("/metrics").text
        assert "rollouts_total 1.0" in metrics
        assert "rollout_failures_total 1.0" in metrics
        assert "rollbacks_total 1.0" in metrics
