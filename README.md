# ConfigSync

ConfigSync is a compact distributed configuration rollout service. It is built
incrementally to demonstrate versioned desired state, optimistic concurrency,
replicated artifact storage, reconciliation, canary rollout, and rollback.

## Phase 2: optimistic concurrency

The current control plane is backed by SQLite. It can:

- create a named configuration at version 1;
- retrieve the authoritative current version;
- reject duplicate configuration names with `409 Conflict`;
- update a configuration only when the caller supplies the current version in
  `If-Match`;
- return `409 Conflict` for stale writers so concurrent updates cannot silently
  overwrite each other.

The mutable `Config.current_version` pointer and the immutable next
`ConfigVersion` snapshot are updated in one database transaction. Configuration
content is still stored in SQLite for now; a later phase will move artifacts to
two replicated storage services while keeping authoritative metadata in the
control-plane database.

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

Update version 1 to version 2:

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

Repeat the same request with `If-Match: 1`. Because the current version is now
2, the stale request returns `409 Conflict` instead of overwriting the winning
update.
