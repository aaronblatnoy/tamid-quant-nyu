"""signals table

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-21

Adds the daily tightness snapshot per route. Matches
``taquantgeo_signals.models.Signal`` exactly; any drift would cause
ORM / DDL mismatch errors at first upsert. Schema contract defined in
``docs/adrs/0007-tightness-signal-definition.md``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signals",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("route", sa.String(32), nullable=False),
        sa.Column("forward_demand_ton_miles", sa.BigInteger(), nullable=False),
        sa.Column("forward_supply_count", sa.Integer(), nullable=False),
        sa.Column("dark_fleet_supply_adjustment", sa.Integer(), nullable=False),
        sa.Column("tightness", sa.Float(), nullable=False),
        sa.Column("tightness_z", sa.Float(), nullable=True),
        sa.Column("components", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_signals_as_of", "signals", ["as_of"])
    op.create_index("ix_signals_route", "signals", ["route"])
    op.create_index("ix_signals_route_as_of", "signals", ["route", "as_of"])
    # Unique index (not UniqueConstraint) so ON CONFLICT can target it by
    # column set; ``uq_`` prefix is reserved for true UniqueConstraints
    # (see 0001). ``_uq`` suffix marks the unique-index variant.
    op.create_index("ix_signals_as_of_route_uq", "signals", ["as_of", "route"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_signals_as_of_route_uq", table_name="signals")
    op.drop_index("ix_signals_route_as_of", table_name="signals")
    op.drop_index("ix_signals_route", table_name="signals")
    op.drop_index("ix_signals_as_of", table_name="signals")
    op.drop_table("signals")
