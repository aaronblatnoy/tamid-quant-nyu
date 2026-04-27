"""Persistence helpers for ``TightnessSnapshot``.

``upsert_snapshot`` targets the ``(as_of, route)`` uniqueness constraint.
Production Postgres uses ``INSERT ... ON CONFLICT DO UPDATE`` (atomic,
race-safe) via ``sqlalchemy.dialects.postgresql.insert``. On any other
dialect (sqlite in local tests) the same function falls back to a
delete-then-insert inside the caller's transaction — coarser but
dialect-agnostic. Caller is responsible for commit.

The two branches are behind a single function on purpose: callers see
one ``upsert_snapshot`` entry point and do not need to know which
dialect is in play.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from taquantgeo_signals.models import Signal

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from taquantgeo_signals.tightness import TightnessSnapshot

log = logging.getLogger(__name__)


def _snapshot_to_values(snapshot: TightnessSnapshot) -> dict[str, object]:
    return {
        "as_of": snapshot.as_of,
        "route": snapshot.route,
        "forward_demand_ton_miles": snapshot.forward_demand_ton_miles,
        "forward_supply_count": snapshot.forward_supply_count,
        "dark_fleet_supply_adjustment": snapshot.dark_fleet_supply_adjustment,
        "tightness": snapshot.ratio,
        "tightness_z": snapshot.z_score_90d,
        # dict must be JSON-serialisable; TightnessSnapshot.components is
        # already int|float so json.dumps handles it.
        "components": dict(snapshot.components),
    }


def upsert_snapshot(session: Session, snapshot: TightnessSnapshot) -> None:
    """Upsert ``snapshot`` into ``signals`` on ``(as_of, route)``.

    On Postgres uses ON CONFLICT DO UPDATE; on any other dialect falls
    back to delete-then-insert within the same session (caller's
    transaction boundary). Caller is responsible for committing.
    """
    bind = session.get_bind()
    dialect = bind.dialect.name if bind is not None else None
    values = _snapshot_to_values(snapshot)
    if dialect == "postgresql":
        stmt = pg_insert(Signal).values(**values)
        update_cols = {
            k: stmt.excluded[k]
            for k in values
            if k not in {"as_of", "route"}  # leave the unique key alone
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["as_of", "route"],
            set_=update_cols,
        )
        session.execute(stmt)
    else:
        # Portable path for tests on sqlite etc. — delete any existing
        # row for the key, then insert the new one. Both operations
        # share the session's transaction; caller commits.
        session.execute(
            delete(Signal).where(Signal.as_of == snapshot.as_of, Signal.route == snapshot.route)
        )
        session.add(Signal(**values))
