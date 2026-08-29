import json
import os
from collections.abc import Iterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from control_plane import models
from control_plane.database import Base, create_database, session_scope
from control_plane.schemas import ConfigCreate, ConfigResponse

DEFAULT_DATABASE_URL = "sqlite:///./configsync.db"


def create_app(database_url: str | None = None) -> FastAPI:
    """Build a control-plane app with an isolated database connection."""
    resolved_database_url = database_url or os.getenv(
        "CONFIGSYNC_DATABASE_URL",
        DEFAULT_DATABASE_URL,
    )
    engine, session_factory = create_database(resolved_database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(title="ConfigSync Control Plane", lifespan=lifespan)

    def get_session() -> Iterator[Session]:
        yield from session_scope(session_factory)

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
        """Create a named configuration at immutable version 1."""
        config = models.Config(name=name, current_version=1)
        version = models.ConfigVersion(
            config_name=name,
            version=1,
            content=json.dumps(payload.content, sort_keys=True),
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
        return ConfigResponse(
            name=name,
            version=version.version,
            content=json.loads(version.content),
            created_at=version.created_at,
        )

    @app.get("/configs/{name}", response_model=ConfigResponse)
    def get_config(
        name: str,
        session: Session = Depends(get_session),
    ) -> ConfigResponse:
        """Return the authoritative current version of a configuration."""
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

        return ConfigResponse(
            name=name,
            version=version.version,
            content=json.loads(version.content),
            created_at=version.created_at,
        )

    return app


app = create_app()

