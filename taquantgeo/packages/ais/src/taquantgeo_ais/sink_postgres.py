"""Vessel registry sink — upserts ShipStaticData into Postgres."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy.dialects.postgresql import insert as pg_insert

from taquantgeo_core.db import get_session_factory
from taquantgeo_core.schemas import Vessel

if TYPE_CHECKING:
    from taquantgeo_ais.models import ShipStaticData

logger = logging.getLogger(__name__)


def upsert_vessel(static: ShipStaticData) -> None:
    """Upsert a vessel by MMSI. Updates name/type/dimensions/last_seen_at on conflict."""
    if not static.UserID:
        return

    now = datetime.now(UTC)
    payload = {
        "mmsi": static.UserID,
        "imo": static.ImoNumber or None,
        "name": static.Name.strip() or None,
        "call_sign": static.CallSign.strip() or None,
        "ship_type": static.Type or None,
        "length_m": static.Dimension.length_m or None,
        "beam_m": static.Dimension.beam_m or None,
        "first_seen_at": now,
        "last_seen_at": now,
    }
    stmt = pg_insert(Vessel).values(**payload)
    stmt = stmt.on_conflict_do_update(
        index_elements=["mmsi"],
        set_={
            "imo": stmt.excluded.imo,
            "name": stmt.excluded.name,
            "call_sign": stmt.excluded.call_sign,
            "ship_type": stmt.excluded.ship_type,
            "length_m": stmt.excluded.length_m,
            "beam_m": stmt.excluded.beam_m,
            "last_seen_at": stmt.excluded.last_seen_at,
        },
    )

    factory = get_session_factory()
    with factory() as s:
        s.execute(stmt)
        s.commit()
