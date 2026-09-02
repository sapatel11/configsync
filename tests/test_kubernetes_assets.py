from pathlib import Path


K8S_DIR = Path("k8s")


def test_kubernetes_manifests_cover_platform_and_customer_namespaces() -> None:
    namespaces = (K8S_DIR / "00-namespaces.yaml").read_text(encoding="utf-8")
    platform = (K8S_DIR / "platform.yaml").read_text(encoding="utf-8")
    customer_a = (K8S_DIR / "customer-a.yaml").read_text(encoding="utf-8")
    customer_b = (K8S_DIR / "customer-b.yaml").read_text(encoding="utf-8")

    for namespace in ("configsync", "customer-a", "customer-b"):
        assert f"name: {namespace}" in namespaces

    for service_name in ("storage-a", "storage-b", "control-plane", "prometheus"):
        assert f"name: {service_name}" in platform

    assert "CONFIGSYNC_STORAGE_NODES" in platform
    assert "http://storage-a:8101,http://storage-b:8101" in platform
    assert "control-plane.configsync.svc.cluster.local:8000" in platform

    assert "CONFIGSYNC_CUSTOMER\n              value: customer-a" in customer_a
    assert "CONFIGSYNC_CUSTOMER\n              value: customer-b" in customer_b
    assert "http://control-plane.configsync.svc.cluster.local:8000" in customer_a
    assert "http://control-plane.configsync.svc.cluster.local:8000" in customer_b


def test_kubernetes_workloads_use_local_configsync_image_and_persistent_state() -> None:
    manifests = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            K8S_DIR / "platform.yaml",
            K8S_DIR / "customer-a.yaml",
            K8S_DIR / "customer-b.yaml",
        )
    )

    assert manifests.count("image: configsync:local") == 5
    assert manifests.count("kind: PersistentVolumeClaim") == 5
    assert "readinessProbe:" in manifests
    assert "livenessProbe:" in manifests
    assert "agent_sync_lag_seconds" not in manifests
