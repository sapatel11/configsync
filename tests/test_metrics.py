from pathlib import Path

from fastapi.testclient import TestClient

from agent.reconciler import ConfigSyncAgent
from control_plane.main import create_app
from tests.helpers import MemoryArtifactStore


def test_metrics_endpoint_exposes_required_signals(tmp_path: Path) -> None:
    app = create_app(
        f"sqlite:///{tmp_path / 'test.db'}",
        artifact_store=MemoryArtifactStore(),
    )

    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    text = response.text
    for metric_name in (
        "rollouts_total",
        "rollout_failures_total",
        "rollbacks_total",
        "agent_sync_lag_seconds",
        "storage_node_errors_total",
    ):
        assert metric_name in text


def test_synced_agent_reports_zero_sync_lag(tmp_path: Path) -> None:
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
        metrics = client.get("/metrics").text

    assert (
        'agent_sync_lag_seconds{config_name="firewall",customer="customer-a"} 0.0'
        in metrics
    )
