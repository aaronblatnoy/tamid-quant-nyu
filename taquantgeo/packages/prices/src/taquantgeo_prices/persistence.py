"""Persistence helpers for price rows.

``upsert_prices`` targets the ``(ticker, as_of)`` uniqueness constraint.
Production Postgres uses ``INSERT ... ON CONFLICT DO UPDATE`` (atomic,
race-safe) via ``sqlalchemy.dialects.postgresql.insert``. On any other
dialect (sqlite in local tests) the same function falls back to a
delete-then-insert inside the caller's transaction. Caller commits.

Mirrors the two-branch pattern in ``taquantgeo_signals.persistence`` so
the upsert idiom is consistent across the ORM layer.
"""

from __future__ import annotations

import logging
from datetime import date  # noqa: TC003
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, delete, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from taquantgeo_prices.models import Price

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    import polars as pl
    from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

_ROW_KEYS = (
    "ticker",
    "as_of",
    "open_cents",
    "high_cents",
    "low_cents",
    "close_cents",
    "volume",
)


def _coerce_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in _ROW_KEYS}


def _iter_rows(rows: pl.DataFrame | Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    # ``polars.DataFrame`` iter_rows(named=True) yields dicts keyed by column,
    # which is what we want. Anything else is assumed to be an iterable of
    # dict-like objects.
    if hasattr(rows, "iter_rows"):
        return [_coerce_row(r) for r in rows.iter_rows(named=True)]
    return [_coerce_row(r) for r in rows]


def upsert_prices(
    session: Session,
    rows: pl.DataFrame | Iterable[Mapping[str, Any]],
) -> int:
    """Upsert ``rows`` into ``prices`` on ``(ticker, as_of)``.

    ``rows`` may be a polars DataFrame (output of ``fetch_ohlcv``) or any
    iterable of mappings with the expected keys. Returns the number of
    rows touched (insert or update).

    On Postgres: single ``INSERT ... ON CONFLICT DO UPDATE`` — atomic
    and race-safe. On sqlite / other dialects: delete-then-insert in the
    caller's transaction. Caller is responsible for committing.
    """
    values = _iter_rows(rows)
    if not values:
        return 0
    # Dedup by (ticker, as_of) keeping the last row — Postgres ON CONFLICT
    # DO UPDATE cannot affect the same row twice in one statement
    # (CardinalityViolation), and the sqlite fallback would also violate
    # the unique index on flush. The "keep last" rule means a caller that
    # re-fetches a boundary day in the same batch gets the fresher bar.
    dedup: dict[tuple[str, date], dict[str, Any]] = {}
    for v in values:
        dedup[(v["ticker"], v["as_of"])] = v
    values = list(dedup.values())
    bind = session.get_bind()
    dialect = bind.dialect.name if bind is not None else None
    if dialect == "postgresql":
        stmt = pg_insert(Price).values(values)
        # Exclude the unique key from the UPDATE set — ON CONFLICT matches
        # on it already.
        update_cols = {k: stmt.excluded[k] for k in _ROW_KEYS if k not in {"ticker", "as_of"}}
        stmt = stmt.on_conflict_do_update(
            index_elements=["ticker", "as_of"],
            set_=update_cols,
        )
        session.execute(stmt)
    else:
        # Portable path: drop any colliding rows, then insert. A
        # single DELETE targeting each (ticker, as_of) pair is more
        # compact than N individual DELETEs for a small batch.
        conditions = [and_(Price.ticker == v["ticker"], Price.as_of == v["as_of"]) for v in values]
        if conditions:
            session.execute(delete(Price).where(or_(*conditions)))
        session.add_all(Price(**v) for v in values)
    return len(values)
