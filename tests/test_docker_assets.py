from pathlib import Path


def test_compose_defines_complete_local_stack() -> None:
    compose = Path("compose.yaml").read_text(encoding="utf-8")

    for service in (
        "storage-a:",
        "storage-b:",
        "control-plane:",
        "agent-a:",
        "agent-b:",
        "prometheus:",
    ):
        assert service in compose

    assert "CONFIGSYNC_STORAGE_NODES: http://storage-a:8101,http://storage-b:8101" in compose
    assert "CONFIGSYNC_CONTROL_PLANE: http://control-plane:8000" in compose
    assert '"8000:8000"' in compose
    assert '"9090:9090"' in compose


def test_dockerfile_contains_all_python_services() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "COPY control_plane ./control_plane" in dockerfile
    assert "COPY storage_service ./storage_service" in dockerfile
    assert "COPY agent ./agent" in dockerfile
    assert "pip install --no-cache-dir -r requirements.txt" in dockerfile


def test_prometheus_scrapes_control_plane() -> None:
    prometheus = Path("prometheus.yml").read_text(encoding="utf-8")

    assert 'targets: ["control-plane:8000"]' in prometheus
