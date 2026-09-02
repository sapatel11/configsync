# ConfigSync

ConfigSync is a compact distributed configuration rollout service built to demonstrate backend and distributed-systems fundamentals with Python, FastAPI, SQLite, replicated artifact storage, reconciliation, canary rollout, rollback, and Prometheus observability.

## Current capabilities

ConfigSync provides:

- versioned configuration APIs;
- optimistic concurrency with `If-Match` and `409 Conflict`;
- SHA-256-addressed artifacts replicated to two storage nodes;
- checksum validation and read failover;
- customer reconciliation agents with desired-vs-actual state;
- `customer-a` as a health-gated canary before `customer-b`;
- automatic restoration of the previous desired version when a rollout fails;
- Prometheus metrics for rollouts, failures, rollbacks, storage errors, and agent sync lag;
- a Docker Compose stack for the complete local distributed system.

The control plane keeps authoritative metadata in SQLite. Configuration payloads live in the replicated artifact store. `Config.current_version` identifies the newest candidate version, while `ConfigDesiredState.stable_version` identifies the version customers receive when they are not participating in a staged rollout.

This is intentionally a compact educational system. The artifact layer is a replicated distributed artifact store, not a complete distributed filesystem, and the project does not implement consensus, FUSE, chunking, or distributed locking.

## Tests

From the repository root in PowerShell:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest -v
```

## Docker Compose

Docker Compose runs the complete local topology:

```text
storage-a ----\
               \
                -> control-plane -> agent-a (customer-a)
storage-b ----/                 -> agent-b (customer-b)
                     |
                     +-----------> Prometheus
```

The services are:

- `storage-a` on host port `8101`;
- `storage-b` on host port `8102`;
- `control-plane` on host port `8000`;
- `agent-a` for `customer-a`;
- `agent-b` for `customer-b`;
- `prometheus` on host port `9090`.

Validate the Compose file:

```powershell
docker compose config
```

Build and start everything:

```powershell
docker compose up --build
```

Or run it in the background:

```powershell
docker compose up --build -d
```

Check service state:

```powershell
docker compose ps
```

Useful URLs:

- API docs: <http://127.0.0.1:8000/docs>
- ConfigSync metrics: <http://127.0.0.1:8000/metrics>
- Prometheus: <http://127.0.0.1:9090>

The control-plane database, both storage replicas, and both agents use independent named volumes so state survives ordinary container restarts.

To stop the stack while preserving state:

```powershell
docker compose down
```

To completely reset the local demo, including SQLite, stored artifacts, and agent state:

```powershell
docker compose down -v
```

## Successful canary rollout demo

With the Compose stack running, create version 1:

```powershell
$body = @{
    content = @{
        service = "firewall"
        port = 443
        enabled = $true
    }
} | ConvertTo-Json

Invoke-RestMethod `
    -Method Post `
    -Uri http://127.0.0.1:8000/configs/firewall `
    -ContentType application/json `
    -Body $body
```

The two agents automatically reconcile to version 1.

Create candidate version 2:

```powershell
$updatedBody = @{
    content = @{
        service = "firewall"
        port = 8443
        enabled = $true
    }
} | ConvertTo-Json

Invoke-RestMethod `
    -Method Put `
    -Uri http://127.0.0.1:8000/configs/firewall `
    -Headers @{ "If-Match" = "1" } `
    -ContentType application/json `
    -Body $updatedBody
```

Creating version 2 does not immediately expose it to either customer. Start the rollout:

```powershell
Invoke-RestMethod `
    -Method Post `
    -Uri http://127.0.0.1:8000/rollouts/firewall
```

The control plane targets `customer-a` first. After the canary reports version 2 as `synced`, `customer-b` receives the same target. When both succeed, version 2 becomes stable.

Check customer status:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/customers/customer-a/configs/firewall/status
Invoke-RestMethod -Uri http://127.0.0.1:8000/customers/customer-b/configs/firewall/status
```

## Failed-canary rollback demo

Create an invalid candidate:

```powershell
$badBody = @{
    content = @{
        service = "firewall"
        port = 70000
        enabled = $true
    }
} | ConvertTo-Json

Invoke-RestMethod `
    -Method Put `
    -Uri http://127.0.0.1:8000/configs/firewall `
    -Headers @{ "If-Match" = "2" } `
    -ContentType application/json `
    -Body $badBody
```

Start another rollout:

```powershell
Invoke-RestMethod `
    -Method Post `
    -Uri http://127.0.0.1:8000/rollouts/firewall
```

`customer-a` rejects the invalid configuration. The rollout becomes `rolled_back`, `customer-b` never receives the bad candidate, and the desired target returns to the previous stable version.

## Failure and observability demo

Stop the first storage node:

```powershell
docker compose stop storage-a
```

A read can still succeed by falling back to storage B:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/configs/firewall
```

At the same time, the failure is visible in `/metrics` through `storage_node_errors_total`.

Restart the node:

```powershell
docker compose start storage-a
```

Prometheus scrapes the control plane every five seconds. Useful metric names are:

```text
rollouts_total
rollout_failures_total
rollbacks_total
agent_sync_lag_seconds
storage_node_errors_total
```

## Consistency model

The control plane maintains authoritative version and rollout metadata transactionally in SQLite. Customer agents reconcile asynchronously, so actual customer state is eventually consistent with the desired target.

During a rollout it is expected to temporarily observe:

```text
newest candidate = 2
stable version   = 1
customer-a       = 2
customer-b       = 1
```

That temporary divergence is deliberate and enables health-gated staged deployment.
