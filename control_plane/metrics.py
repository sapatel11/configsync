from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest


class ConfigSyncMetrics:
    """Small app-local Prometheus registry for ConfigSync signals."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.rollouts_total = Counter(
            "rollouts_total",
            "Rollouts started.",
            registry=self.registry,
        )
        self.rollout_failures_total = Counter(
            "rollout_failures_total",
            "Rollouts that failed their health gate.",
            registry=self.registry,
        )
        self.rollbacks_total = Counter(
            "rollbacks_total",
            "Automatic rollbacks performed.",
            registry=self.registry,
        )
        self.agent_sync_lag_seconds = Gauge(
            "agent_sync_lag_seconds",
            "Seconds a customer agent is behind its desired configuration.",
            ["customer", "config_name"],
            registry=self.registry,
        )
        self.storage_node_errors_total = Counter(
            "storage_node_errors_total",
            "Storage-node operation failures.",
            ["node", "operation"],
            registry=self.registry,
        )

    def record_storage_error(self, node: str, operation: str) -> None:
        self.storage_node_errors_total.labels(node=node, operation=operation).inc()

    def render(self) -> bytes:
        return generate_latest(self.registry)
