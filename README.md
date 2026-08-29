# ConfigSync

ConfigSync is a compact distributed configuration rollout service. It is built
incrementally to demonstrate versioned desired state, optimistic concurrency,
replicated artifact storage, reconciliation, canary rollout, and rollback.

## Phase 1: minimal control plane

The current phase provides a FastAPI control plane backed by SQLite. It can:

- create a named configuration at version 1;
- retrieve the authoritative current version;
- reject duplicate configuration names with `409 Conflict`.

Configuration content is temporarily stored in SQLite. A later phase will move
artifacts to two replicated storage services while keeping authoritative metadata
in the control-plane database.

## Local setup

From the repository root in PowerShell:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m uvicorn control_plane.main:app --reload
```

Open <http://127.0.0.1:8000/docs> for the interactive API documentation.

Create a configuration:

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

Retrieve it:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/configs/firewall
```

