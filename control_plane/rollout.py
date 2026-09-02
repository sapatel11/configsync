import time

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from control_plane import models


ACTIVE_ROLLOUT_STATUSES = ("canary", "rolling_out")


class RolloutConflict(RuntimeError):
    """Raised when a rollout cannot be started from the current state."""


class RolloutCoordinator:
    """Stage a candidate through a canary and roll back on reported failure."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        canary_customer: str = "customer-a",
        remaining_customers: tuple[str, ...] = ("customer-b",),
        timeout_seconds: float = 8.0,
        poll_interval_seconds: float = 0.1,
    ) -> None:
        self.session_factory = session_factory
        self.canary_customer = canary_customer
        self.remaining_customers = remaining_customers
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def _stable_state(
        self,
        session: Session,
        config: models.Config,
    ) -> models.ConfigDesiredState:
        state = session.get(models.ConfigDesiredState, config.name)
        if state is None:
            state = models.ConfigDesiredState(
                config_name=config.name,
                stable_version=config.current_version,
            )
            session.add(state)
            session.flush()
        return state

    def _set_target(
        self,
        session: Session,
        customer: str,
        config_name: str,
        version: int,
    ) -> None:
        target = session.get(models.CustomerConfigTarget, (customer, config_name))
        if target is None:
            session.add(
                models.CustomerConfigTarget(
                    customer=customer,
                    config_name=config_name,
                    desired_version=version,
                )
            )
        else:
            target.desired_version = version

        state = session.get(models.CustomerConfigState, (customer, config_name))
        if state is not None:
            state.status = "pending"
            state.error = None

    def _wait_for_customer(
        self,
        customer: str,
        config_name: str,
        target_version: int,
    ) -> tuple[bool, str | None]:
        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            with self.session_factory() as session:
                state = session.get(
                    models.CustomerConfigState,
                    (customer, config_name),
                )
                if state is not None:
                    if state.status == "synced" and state.applied_version == target_version:
                        return True, None
                    if state.status == "error":
                        return False, state.error or f"{customer} reported an error"
            time.sleep(self.poll_interval_seconds)
        return False, f"Timed out waiting for {customer} to apply version {target_version}"

    def _mark_rollout(
        self,
        rollout_id: int,
        status: str,
        error: str | None = None,
    ) -> models.Rollout:
        with self.session_factory() as session:
            rollout = session.get(models.Rollout, rollout_id)
            if rollout is None:
                raise RuntimeError("rollout record disappeared")
            rollout.status = status
            rollout.error = error
            session.commit()
            session.refresh(rollout)
            session.expunge(rollout)
            return rollout

    def _rollback(
        self,
        rollout_id: int,
        customers: tuple[str, ...],
        config_name: str,
        previous_version: int,
        error: str,
    ) -> models.Rollout:
        with self.session_factory() as session:
            for customer in customers:
                self._set_target(session, customer, config_name, previous_version)
            session.commit()
        return self._mark_rollout(rollout_id, "rolled_back", error)

    def run(self, config_name: str) -> models.Rollout:
        with self.session_factory() as session:
            config = session.get(models.Config, config_name)
            if config is None:
                raise KeyError(config_name)

            active = session.scalar(
                select(models.Rollout).where(
                    models.Rollout.config_name == config_name,
                    models.Rollout.status.in_(ACTIVE_ROLLOUT_STATUSES),
                )
            )
            if active is not None:
                raise RolloutConflict("A rollout is already in progress")

            stable = self._stable_state(session, config)
            candidate_version = config.current_version
            previous_version = stable.stable_version
            if candidate_version == previous_version:
                raise RolloutConflict("No newer configuration version is waiting to roll out")

            rollout = models.Rollout(
                config_name=config_name,
                target_version=candidate_version,
                previous_version=previous_version,
                status="canary",
            )
            session.add(rollout)
            self._set_target(
                session,
                self.canary_customer,
                config_name,
                candidate_version,
            )
            session.commit()
            session.refresh(rollout)
            rollout_id = rollout.id

        healthy, error = self._wait_for_customer(
            self.canary_customer,
            config_name,
            candidate_version,
        )
        if not healthy:
            return self._rollback(
                rollout_id,
                (self.canary_customer,),
                config_name,
                previous_version,
                error or "Canary failed",
            )

        self._mark_rollout(rollout_id, "rolling_out")
        with self.session_factory() as session:
            for customer in self.remaining_customers:
                self._set_target(session, customer, config_name, candidate_version)
            session.commit()

        for customer in self.remaining_customers:
            healthy, error = self._wait_for_customer(
                customer,
                config_name,
                candidate_version,
            )
            if not healthy:
                return self._rollback(
                    rollout_id,
                    (self.canary_customer, *self.remaining_customers),
                    config_name,
                    previous_version,
                    error or f"{customer} failed",
                )

        with self.session_factory() as session:
            config = session.get(models.Config, config_name)
            if config is None:
                raise RuntimeError("configuration disappeared during rollout")
            stable = self._stable_state(session, config)
            stable.stable_version = candidate_version
            for customer in (self.canary_customer, *self.remaining_customers):
                target = session.get(
                    models.CustomerConfigTarget,
                    (customer, config_name),
                )
                if target is not None:
                    session.delete(target)
            rollout = session.get(models.Rollout, rollout_id)
            if rollout is None:
                raise RuntimeError("rollout record disappeared")
            rollout.status = "succeeded"
            rollout.error = None
            session.commit()
            session.refresh(rollout)
            session.expunge(rollout)
            return rollout
