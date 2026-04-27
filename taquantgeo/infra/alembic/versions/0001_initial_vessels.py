"""initial: vessels table

Revision ID: 0001
Revises:
Create Date: 2026-04-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vessels",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("mmsi", sa.BigInteger(), nullable=False),
        sa.Column("imo", sa.BigInteger(), nullable=True),
        sa.Column("name", sa.String(64), nullable=True),
        sa.Column("call_sign", sa.String(16), nullable=True),
        sa.Column("ship_type", sa.Integer(), nullable=True),
        sa.Column("length_m", sa.Integer(), nullable=True),
        sa.Column("beam_m", sa.Integer(), nullable=True),
        sa.Column("flag", sa.String(2), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("mmsi", name="uq_vessels_mmsi"),
        sa.UniqueConstraint("imo", name="uq_vessels_imo"),
    )
    op.create_index("ix_vessels_mmsi", "vessels", ["mmsi"])
    op.create_index("ix_vessels_imo", "vessels", ["imo"])
    op.create_index("ix_vessels_ship_type", "vessels", ["ship_type"])
    op.create_index("ix_vessels_ship_type_length", "vessels", ["ship_type", "length_m"])


def downgrade() -> None:
    op.drop_index("ix_vessels_ship_type_length", table_name="vessels")
    op.drop_index("ix_vessels_ship_type", table_name="vessels")
    op.drop_index("ix_vessels_imo", table_name="vessels")
    op.drop_index("ix_vessels_mmsi", table_name="vessels")
    op.drop_table("vessels")
