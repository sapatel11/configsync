# ConfigSync

ConfigSync is a compact distributed configuration rollout service. It is built
incrementally to demonstrate versioned desired state, optimistic concurrency,
replicated artifact storage, reconciliation, canary rollout, and rollback.

## Phase 3: replicated artifact storage

The control plane now keeps authoritative configuration metadata in SQLite while
configuration payloads are stored as replicated artifacts on two independent
storage services.

Current behavior:

- create and retrieve versioned configurations;
- protect updates with `If-Match` optimistic concurrency;
- calculate a SHA-256 checksum for every canonical JSON artifact;
- write each artifact to both storage nodes before committing metadata;
- store only the artifact checksum in `ConfigVersion` metadata;
- validate checksums when artifacts are read;
- fall back to the second storage node when the first is unavailable or returns
  corrupted content.

This is intentionally a replicated artifact store, not a complete distributed
filesystem. It does not implement chunking, consensus, FUSE, or distributed
locking.

## Local setup

From the repository root in PowerShell:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest -v
```

Phase 3 changes the development database schema from inline content to artifact
checksums. If you still have a `configsync.db` created by Phase 2, delete it once
before starting the new services:

```powershell
Remove-Item configsync.db -ErrorAction SilentlyContinue
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

Open <http://127.0.0.1:8000/docs> for the API documentation.

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

The response includes the configuration version and its SHA-256 `checksum`.
The same artifact should exist in both `artifact-data-a` and `artifact-data-b`.

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

## Storage-node failure demonstration

After creating a configuration, stop storage node A. Leave storage node B and
the control plane running, then retrieve the configuration:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/configs/firewall
```

The request should still succeed because the control plane attempts storage node
A first and then reads the checksum-validated replica from storage node B.

The automated test suite also covers replicated writes, first-node failure, and
fallback when the first replica returns corrupted content.
