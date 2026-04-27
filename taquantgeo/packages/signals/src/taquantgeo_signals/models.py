"""SQLAlchemy ORM model for the ``signals`` table.

Registered on ``taquantgeo_core.schemas.Base`` so ``alembic upgrade head``
picks it up from the shared metadata without env.py changes.

We deliberately do NOT use ``from __future__ import annotations`` here:
SQLAlchemy resolves Mapped type hints at class-definition time and needs
``date``, ``datetime``, etc. available in module globals at runtime.
"""

from datetime import date, datetime

from sqlalchemy import JSON, BigInteger, Date, DateTime, Float, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from taquantgeo_core.schemas import Base


class Signal(Base):
    __tablename__ = "signals"

    # SQLite's auto-increment rowid alias requires INTEGER (not BIGINT);
    # Postgres gets bigserial via BigInteger. The variant keeps both paths
    # working so persistence tests can use sqlite locally and the live DB
    # is bigserial per the data model.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    as_of: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    route: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    forward_demand_ton_miles: Mapped[int] = mapped_column(BigInteger, nullable=False)
    forward_supply_count: Mapped[int] = mapped_column(Integer, nullable=False)
    dark_fleet_supply_adjustment: Mapped[int] = mapped_column(Integer, nullable=False)
    tightness: Mapped[float] = mapped_column(Float, nullable=False)
    tightness_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Use JSONB on Postgres, fall back to JSON on other dialects (SQLite
    # test runs in particular). ``with_variant`` is a server-side swap so
    # the declarative schema stays dialect-agnostic.
    components: Mapped[dict[str, int | float]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_signals_route_as_of", "route", "as_of"),
        # Unique on (as_of, route) is the upsert key. Defined here rather
        # than as a UniqueConstraint so alembic emits a regular unique index
        # that ON CONFLICT can target by name. Named with ``_uq`` suffix
        # (rather than ``uq_`` prefix) because the ``uq_`` prefix is
        # reserved for ``sa.UniqueConstraint`` in this repo (see
        # 0001_initial_vessels.py) and this DDL emits a unique *index*.
        Index("ix_signals_as_of_route_uq", "as_of", "route", unique=True),
    )

    def __repr__(self) -> str:
        return (
            f"<Signal as_of={self.as_of} route={self.route!r} "
            f"tightness={self.tightness:.2f} z={self.tightness_z}>"
        )
