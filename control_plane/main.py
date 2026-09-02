import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from prometheus_client import CONTENT_TYPE_LATEST
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
from control_plane.metrics import ConfigSyncMetrics
from control_plane.rollout import RolloutConflict, RolloutCoordinator
from control_plane.schemas import (
    ConfigCreate,
    ConfigResponse,
    ConfigUpdate,
    CustomerStateResponse,
    CustomerStateUpdate,
    RolloutResponse,
)

DEFAULT_DATABASE_URL = "sqlite:///./configsync.db"


def serialize_content(content: dict) -> bytes:
    return json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")


def create_app(
    database_url: str | None = None,
    artifact_store: ArtifactStore | None = None,
    rollout_timeout_seconds: float = 8.0,
    rollout_poll_interval_seconds: float = 0.1,
) -> FastAPI:
    """Build a control-plane app with isolated database and artifact storage."""
    resolved_database_url = database_url or os.getenv(
        "CONFIGSYNC_DATABASE_URL",
        DEFAULT_DATABASE_URL,
    )
    metrics = ConfigSyncMetrics()
    resolved_artifact_store = artifact_store or ReplicatedArtifactStore.from_environment(
        error_callback=metrics.record_storage_error
    )
    engine, session_factory = create_database(resolved_database_url)
    rollout_coordinator = RolloutCoordinator(
        session_factory,
        timeout_seconds=rollout_timeout_seconds,
        poll_interval_seconds=rollout_poll_interval_seconds,
        metrics=metrics,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(title="ConfigSync Control Plane", lifespan=lifespan)
    app.state.metrics = metrics

    def get_session() -> Iterator[Session]:
        yield from session_scope(session_factory)

    def store_payload(content: dict) -> str:
        artifact = serialize_content(content)
        checksum = hashlib.sha256(artifact).hexdigest()
        try:
            resolved_artifact_store.put(checksum, artifact)
        except ArtifactStoreError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Artifact replication failed",
            ) from exc
        return checksum

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

    def get_version_record(
        session: Session,
        name: str,
        version_number: int,
    ) -> models.ConfigVersion:
        version = session.scalar(
            select(models.ConfigVersion).where(
                models.ConfigVersion.config_name == name,
                models.ConfigVersion.version == version_number,
            )
        )
        if version is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Configuration version metadata is missing",
            )
        return version

    def get_stable_state(
        session: Session,
        config: models.Config,
    ) -> models.ConfigDesiredState:
        desired = session.get(models.ConfigDesiredState, config.name)
        if desired is None:
            desired = models.ConfigDesiredState(
                config_name=config.name,
                stable_version=config.current_version,
            )
            session.add(desired)
            session.commit()
            session.refresh(desired)
        return desired

    def customer_desired_version(
        session: Session,
        customer: str,
        config: models.Config,
    ) -> int:
        target = session.get(models.CustomerConfigTarget, (customer, config.name))
        if target is not None:
            return target.desired_version
        return get_stable_state(session, config).stable_version

    def update_sync_lag_metric(
        session: Session,
        customer: str,
        config: models.Config,
        applied_version: int,
        report_status: str,
    ) -> None:
        desired_version = customer_desired_version(session, customer, config)
        lag = 0.0
        if report_status != "synced" or applied_version != desired_version:
            desired_record = get_version_record(session, config.name, desired_version)
            created_at = desired_record.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            lag = max(0.0, (datetime.now(timezone.utc) - created_at).total_seconds())
        metrics.agent_sync_lag_seconds.labels(
            customer=customer,
            config_name=config.name,
        ).set(lag)

    def to_response(name: str, version: models.ConfigVersion) -> ConfigResponse:
        return ConfigResponse(
            name=name,
            version=version.version,
            checksum=version.checksum,
            content=load_payload(version.checksum),
            created_at=version.created_at,
        )

    def to_customer_state_response(
        state_record: models.CustomerConfigState,
    ) -> CustomerStateResponse:
        return CustomerStateResponse(
            customer=state_record.customer,
            config_name=state_record.config_name,
            applied_version=state_record.applied_version,
            status=state_record.status,
            error=state_record.error,
            updated_at=state_record.updated_at,
        )

    def to_rollout_response(rollout: models.Rollout) -> RolloutResponse:
        return RolloutResponse(
            id=rollout.id,
            config_name=rollout.config_name,
            target_version=rollout.target_version,
            previous_version=rollout.previous_version,
            status=rollout.status,
            error=rollout.error,
            created_at=rollout.created_at,
            updated_at=rollout.updated_at,
        )

    @app.get("/metrics", include_in_schema=False)
    def prometheus_metrics() -> Response:
        return Response(content=metrics.render(), media_type=CONTENT_TYPE_LATEST)

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
        """Create version 1 and make it the initial stable desired version."""
        checksum = store_payload(payload.content)
        config = models.Config(name=name, current_version=1)
        version = models.ConfigVersion(
            config_name=name,
            version=1,
            checksum=checksum,
        )
        desired = models.ConfigDesiredState(config_name=name, stable_version=1)
        session.add_all([config, version, desired])

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
        """Create a candidate version without immediately exposing it to all agents."""
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

        checksum = store_payload(payload.content)
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
        """Return the newest configuration version, including an unrolled candidate."""
        config = session.get(models.Config, name)
        if config is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Configuration '{name}' was not found",
            )
        return to_response(
            name,
            get_version_record(session, name, config.current_version),
        )

    @app.get(
        "/customers/{customer}/configs/{name}/desired",
        response_model=ConfigResponse,
    )
    def get_customer_desired_config(
        customer: str,
        name: str,
        session: Session = Depends(get_session),
    ) -> ConfigResponse:
        """Return the customer-specific rollout target or the stable version."""
        config = session.get(models.Config, name)
        if config is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Configuration '{name}' was not found",
            )
        desired_version = customer_desired_version(session, customer, config)
        return to_response(
            name,
            get_version_record(session, name, desired_version),
        )

    @app.put(
        "/customers/{customer}/configs/{name}/status",
        response_model=CustomerStateResponse,
    )
    def report_customer_state(
        customer: str,
        name: str,
        payload: CustomerStateUpdate,
        session: Session = Depends(get_session),
    ) -> CustomerStateResponse:
        """Record the actual state most recently observed by a customer agent."""
        config = session.get(models.Config, name)
        if config is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Configuration '{name}' was not found",
            )

        state_record = session.get(models.CustomerConfigState, (customer, name))
        if state_record is None:
            state_record = models.CustomerConfigState(
                customer=customer,
                config_name=name,
                applied_version=payload.applied_version,
                status=payload.status,
                error=payload.error,
            )
            session.add(state_record)
        else:
            state_record.applied_version = payload.applied_version
            state_record.status = payload.status
            state_record.error = payload.error

        session.commit()
        session.refresh(state_record)
        update_sync_lag_metric(
            session,
            customer,
            config,
            payload.applied_version,
            payload.status,
        )
        return to_customer_state_response(state_record)

    @app.get(
        "/customers/{customer}/configs/{name}/status",
        response_model=CustomerStateResponse,
    )
    def get_customer_state(
        customer: str,
        name: str,
        session: Session = Depends(get_session),
    ) -> CustomerStateResponse:
        """Return the last actual state reported by a customer agent."""
        state_record = session.get(models.CustomerConfigState, (customer, name))
        if state_record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No state reported for customer '{customer}' and config '{name}'",
            )
        return to_customer_state_response(state_record)

    @app.post("/rollouts/{name}", response_model=RolloutResponse)
    def start_rollout(name: str) -> RolloutResponse:
        """Roll the newest candidate through customer-a before customer-b."""
        try:
            rollout = rollout_coordinator.run(name)
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Configuration '{name}' was not found",
            ) from exc
        except RolloutConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        return to_rollout_response(rollout)

    @app.get("/rollouts/{rollout_id}", response_model=RolloutResponse)
    def get_rollout(
        rollout_id: int,
        session: Session = Depends(get_session),
    ) -> RolloutResponse:
        rollout = session.get(models.Rollout, rollout_id)
        if rollout is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Rollout {rollout_id} was not found",
            )
        return to_rollout_response(rollout)

    return app


app = create_app()
