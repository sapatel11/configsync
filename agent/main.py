import os
from pathlib import Path

from agent.reconciler import ConfigSyncAgent


def main() -> None:
    customer = os.getenv("CONFIGSYNC_CUSTOMER", "customer-a")
    config_name = os.getenv("CONFIGSYNC_CONFIG", "firewall")
    control_plane_url = os.getenv("CONFIGSYNC_CONTROL_PLANE", "http://127.0.0.1:8000")
    state_dir = Path(os.getenv("CONFIGSYNC_AGENT_STATE_DIR", "./agent-state"))
    poll_interval = float(os.getenv("CONFIGSYNC_POLL_INTERVAL_SECONDS", "2"))

    agent = ConfigSyncAgent(
        customer=customer,
        config_name=config_name,
        control_plane_url=control_plane_url,
        state_dir=state_dir,
        poll_interval_seconds=poll_interval,
    )
    agent.run_forever()


if __name__ == "__main__":
    main()
