import hashlib
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, status

STORAGE_DIRECTORY = Path(os.getenv("CONFIGSYNC_STORAGE_DIR", "./artifact-data"))

app = FastAPI(title="ConfigSync Artifact Storage")


def artifact_path(checksum: str) -> Path:
    if len(checksum) != 64 or any(ch not in "0123456789abcdef" for ch in checksum):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Artifact checksum must be a lowercase SHA-256 hex digest",
        )
    return STORAGE_DIRECTORY / checksum


@app.put("/artifacts/{checksum}", status_code=status.HTTP_204_NO_CONTENT)
async def put_artifact(checksum: str, request: Request) -> Response:
    content = await request.body()
    if hashlib.sha256(content).hexdigest() != checksum:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Artifact content does not match checksum",
        )

    STORAGE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = artifact_path(checksum)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_bytes(content)
    temp_path.replace(path)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/artifacts/{checksum}")
def get_artifact(checksum: str) -> Response:
    path = artifact_path(checksum)
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact was not found",
        )
    return Response(content=path.read_bytes(), media_type="application/octet-stream")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
