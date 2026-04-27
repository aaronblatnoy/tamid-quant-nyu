"""SQLAlchemy ORM models. Source of truth for table schemas — alembic
generates migrations from `Base.metadata`.

We deliberately do NOT use `from __future__ import annotations` here:
SQLAlchemy's Mapped resolver evaluates type hints at class-definition time
and needs the referenced classes (datetime, etc.) available at runtime.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Vessel(Base):
    __tablename__ = "vessels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mmsi: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    imo: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    call_sign: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ship_type: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    length_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    beam_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    flag: Mapped[str | None] = mapped_column(String(2), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_vessels_ship_type_length", "ship_type", "length_m"),)

    def __repr__(self) -> str:
        return f"<Vessel mmsi={self.mmsi} imo={self.imo} name={self.name!r}>"
