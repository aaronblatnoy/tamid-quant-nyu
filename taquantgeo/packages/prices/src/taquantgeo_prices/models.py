"""SQLAlchemy ORM model for the ``prices`` table.

Registered on ``taquantgeo_core.schemas.Base`` so ``alembic upgrade head``
picks it up from the shared metadata without env.py changes.

We deliberately do NOT use ``from __future__ import annotations`` here:
SQLAlchemy resolves Mapped type hints at class-definition time and needs
``date``, ``datetime``, etc. available in module globals at runtime.

All money is integer cents — CLAUDE.md invariant. Volume is ``bigint``
because yfinance occasionally emits values > 2^31 on blowout days in
the high-float names (though TNK et al. rarely get there).
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from taquantgeo_core.schemas import Base


class Price(Base):
    __tablename__ = "prices"

    # SQLite only treats INTEGER primary keys as rowid aliases; Postgres
    # gets bigserial via BigInteger. The variant keeps tests on sqlite
    # working while production remains bigserial.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    as_of: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    open_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    high_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    low_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    close_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Unique index (not UniqueConstraint) on (ticker, as_of) so upserts
        # can target it by column set via ON CONFLICT. A unique index on this
        # column order already serves equality and range lookups, so a
        # duplicate non-unique composite would only add dead write-cost.
        # Matches the ``_uq`` suffix convention set by 0002_signals_table.py.
        Index("ix_prices_ticker_as_of_uq", "ticker", "as_of", unique=True),
    )

    def __repr__(self) -> str:
        return f"<Price ticker={self.ticker!r} as_of={self.as_of} close_cents={self.close_cents}>"
