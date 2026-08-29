from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from control_plane.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Config(Base):
    """Authoritative pointer to a configuration's current version."""

    __tablename__ = "configs"

    name: Mapped[str] = mapped_column(String(120), primary_key=True)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    versions: Mapped[list["ConfigVersion"]] = relationship(
        back_populates="config",
        cascade="all, delete-orphan",
    )


class ConfigVersion(Base):
    """An immutable snapshot of configuration content."""

    __tablename__ = "config_versions"
    __table_args__ = (
        UniqueConstraint("config_name", "version", name="uq_config_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    config_name: Mapped[str] = mapped_column(
        ForeignKey("configs.name"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
    )
    config: Mapped[Config] = relationship(back_populates="versions")

