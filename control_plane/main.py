import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from control_plane import models
from control_plane.artifact_store import (
    ArtifactStore,
    ArtifactStoreError,
    ReplicatedArtifactStore,
)
from control_plane.database import Base, create_database, session_scope
from control_plane.schemas import ConfigCreate, ConfigResponse, ConfigUpdate

DEFAULT_DATABASE_URL = "sqlite:///./configsync.db"


def serialize_content(content: dict) -> bytes:
    return json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")


def create_app(
    database_url: str | None = None,
    artifact_store: ArtifactStore | None = None,
) -> FastAPI:
    """Build a control-plane app with isolated database and artifact storage."""
    resolved_database_url = database_url or os.getenv(
        "CONFIGSYNC_DATABASE_URL",
        DEFAULT_DATABASE_URL,
    )
    resolved_artifact_store = artifact_store or ReplicatedArtifactStore.from_environment()
    engine, session_factory = create_database(resolved_database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(title="ConfigSync Control Plane", lifespan=lifespan)

    def get_session() -> Iterator[Session]:
        yield from session_scope(session_factory)

    def store_payload(content: dict) -> tuple[str, bytes]:
        artifact = serialize_content(content)
        checksum = hashlib.sha256(artifact).hexdigest()
        try:
            resolved_artifact_store.put(checksum, artifact)
        except ArtifactStoreError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Artifact replication failed",
            ) from exc
        return checksum, artifact

    def load_payload(checksum: str) -> dict:
        try:
            artifact = resolved_artifact_store.get(checksum)
        except ArtifactStoreError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No valid artifact replica is available",
            ) from exc

        if hashlib.sha256(artifact).hexdigest() != checksum:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Artifact checksum validation failed",
            )
        return json.loads(artifact)

    def to_response(name: str, version: models.ConfigVersion) -> ConfigResponse:
        return ConfigResponse(
            name=name,
            version=version.version,
            checksum=version.checksum,
            content=load_payload(version.checksum),
            created_at=version.created_at,
        )

    @app.post(
        "/configs/{name}",
        response_model=ConfigResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_config(
        name: str,
        payload: ConfigCreate,
        session: Session = Depends(get_session),
    ) -> ConfigResponse:
        """Create version 1 after replicating its artifact to both storage nodes."""
        checksum, _ = store_payload(payload.content)
        config = models.Config(name=name, current_version=1)
        version = models.ConfigVersion(
            config_name=name,
            version=1,
            checksum=checksum,
        )
        session.add_all([config, version])

        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Configuration '{name}' already exists",
            ) from exc

        session.refresh(version)
        return to_response(name, version)

    @app.put("/configs/{name}", response_model=ConfigResponse)
    def update_config(
        name: str,
        payload: ConfigUpdate,
        if_match: str | None = Header(default=None, alias="If-Match"),
        session: Session = Depends(get_session),
    ) -> ConfigResponse:
        """Replicate a new artifact and atomically advance its version pointer."""
        if if_match is None:
            raise HTTPException(
                status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                detail="If-Match header is required",
            )

        normalized_if_match = if_match.strip()
        if not normalized_if_match.isascii() or not normalized_if_match.isdigit():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="If-Match must be a positive integer version",
            )

        expected_version = int(normalized_if_match)
        if expected_version < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="If-Match must be a positive integer version",
            )

        if session.get(models.Config, name) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Configuration '{name}' was not found",
            )

        checksum, _ = store_payload(payload.content)
        next_version = expected_version + 1
        result = session.execute(
            update(models.Config)
            .where(
                models.Config.name == name,
                models.Config.current_version == expected_version,
            )
            .values(current_version=next_version)
        )

        if result.rowcount != 1:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Configuration '{name}' changed since version "
                    f"{expected_version}"
                ),
            )

        version = models.ConfigVersion(
            config_name=name,
            version=next_version,
            checksum=checksum,
        )
        session.add(version)

        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Configuration '{name}' was updated concurrently",
            ) from exc

        session.refresh(version)
        return to_response(name, version)

    @app.get("/configs/{name}", response_model=ConfigResponse)
    def get_config(
        name: str,
        session: Session = Depends(get_session),
    ) -> ConfigResponse:
        """Return the current config by resolving its artifact from storage."""
        config = session.get(models.Config, name)
        if config is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Configuration '{name}' was not found",
            )

        version = session.scalar(
            select(models.ConfigVersion).where(
                models.ConfigVersion.config_name == name,
                models.ConfigVersion.version == config.current_version,
            )
        )
        if version is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Current configuration version is missing",
            )

        return to_response(name, version)

    return app


app = create_app()
