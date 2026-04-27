"""prices table

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-21

Adds the daily OHLCV table for the shipping-equity proxy basket (FRO,
DHT, INSW, EURN, TNK). Matches ``taquantgeo_prices.models.Price`` exactly;
any drift would cause ORM / DDL mismatch errors at first upsert. Source
selection and adjustment semantics are documented in
``docs/adrs/0008-equity-price-source.md``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prices",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("open_cents", sa.BigInteger(), nullable=False),
        sa.Column("high_cents", sa.BigInteger(), nullable=False),
        sa.Column("low_cents", sa.BigInteger(), nullable=False),
        sa.Column("close_cents", sa.BigInteger(), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_prices_ticker", "prices", ["ticker"])
    op.create_index("ix_prices_as_of", "prices", ["as_of"])
    # Unique index (not UniqueConstraint) so ON CONFLICT can target it by
    # column set — same convention as 0002_signals_table.py.
    op.create_index(
        "ix_prices_ticker_as_of_uq", "prices", ["ticker", "as_of"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_prices_ticker_as_of_uq", table_name="prices")
    op.drop_index("ix_prices_as_of", table_name="prices")
    op.drop_index("ix_prices_ticker", table_name="prices")
    op.drop_table("prices")
