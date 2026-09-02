# ConfigSync

ConfigSync is a compact distributed configuration rollout service built to demonstrate backend and distributed-systems fundamentals with Python, FastAPI, SQLite, replicated artifact storage, reconciliation, canary rollout, and rollback.

## Current capabilities

ConfigSync now provides:

- versioned configuration APIs;
- optimistic concurrency with `If-Match` and `409 Conflict`;
- SHA-256-addressed artifacts replicated to two storage nodes;
- checksum validation and read failover;
- customer reconciliation agents with desired-vs-actual state;
- per-customer desired targets for staged rollout;
- `customer-a` as a canary before `customer-b`;
- automatic restoration of the previous desired version when a rollout fails.

The control plane keeps authoritative metadata in SQLite. Configuration payloads live in the replicated artifact store. `Config.current_version` identifies the newest candidate version, while `ConfigDesiredState.stable_version` identifies the version customers should receive when they are not participating in a staged rollout.

This is intentionally a compact educational system. The artifact layer is a replicated distributed artifact store, not a complete distributed filesystem, and the project does not implement consensus, FUSE, chunking, or distributed locking.

## Local setup

From the repository root in PowerShell:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest -v
```

Start storage node A:

```powershell
$env:CONFIGSYNC_STORAGE_DIR = ".\artifact-data-a"
.venv\Scripts\python.exe -m uvicorn storage_service.main:app --port 8101
```

Start storage node B in a second terminal:

```powershell
$env:CONFIGSYNC_STORAGE_DIR = ".\artifact-data-b"
.venv\Scripts\python.exe -m uvicorn storage_service.main:app --port 8102
```

Start the control plane in a third terminal:

```powershell
$env:CONFIGSYNC_STORAGE_NODES = "http://127.0.0.1:8101,http://127.0.0.1:8102"
.venv\Scripts\python.exe -m uvicorn control_plane.main:app --reload
```

Start `customer-a` in a fourth terminal:

```powershell
$env:CONFIGSYNC_CUSTOMER = "customer-a"
$env:CONFIGSYNC_CONFIG = "firewall"
$env:CONFIGSYNC_CONTROL_PLANE = "http://127.0.0.1:8000"
.venv\Scripts\python.exe -m agent.main
```

Start `customer-b` in a fifth terminal:

```powershell
$env:CONFIGSYNC_CUSTOMER = "customer-b"
$env:CONFIGSYNC_CONFIG = "firewall"
$env:CONFIGSYNC_CONTROL_PLANE = "http://127.0.0.1:8000"
.venv\Scripts\python.exe -m agent.main
```

Open <http://127.0.0.1:8000/docs> for interactive API documentation.

## Successful canary rollout demo

Create version 1:

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

After the agents reconcile, both customers should report version 1.

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

The control plane targets `customer-a` first. After `customer-a` reports version 2 as `synced`, the control plane targets `customer-b`. When both succeed, the rollout status becomes `succeeded` and version 2 becomes the stable desired version.

Check customer status:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/customers/customer-a/configs/firewall/status
Invoke-RestMethod -Uri http://127.0.0.1:8000/customers/customer-b/configs/firewall/status
```

## Failed-canary rollback demo

Create another candidate with an invalid port, using the newest version in `If-Match`:

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

`customer-a` rejects the invalid configuration and reports an error. The rollout becomes `rolled_back`, `customer-b` never receives the bad candidate, and the desired target is restored to the previous stable version.

## Consistency model

The control plane maintains authoritative version and rollout metadata transactionally in SQLite. Customer agents reconcile asynchronously, so actual customer state is eventually consistent with the desired target.

During a rollout it is therefore expected to temporarily observe states such as:

```text
newest candidate = 2
stable version   = 1
customer-a       = 2
customer-b       = 1
```

That temporary divergence is deliberate and is what enables health-gated staged deployment.
