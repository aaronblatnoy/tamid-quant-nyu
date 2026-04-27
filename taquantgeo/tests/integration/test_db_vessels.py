"""Integration test: vessel upsert via Postgres.

Skipped automatically when no DATABASE_URL is set (so plain `uv run pytest`
without docker-compose passes locally).
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select

from taquantgeo_ais.models import AisDimension, ShipStaticData
from taquantgeo_ais.sink_postgres import upsert_vessel
from taquantgeo_core.db import session_scope
from taquantgeo_core.schemas import Vessel

pytestmark = pytest.mark.integration

if "DATABASE_URL" not in os.environ:
    pytest.skip("DATABASE_URL not set", allow_module_level=True)


def test_upsert_vessel_round_trip() -> None:
    static = ShipStaticData(
        UserID=999_888_777,
        Type=80,
        Name="TEST VLCC",
        CallSign="TEST",
        ImoNumber=9999999,
        Dimension=AisDimension(A=200, B=130, C=30, D=30),
    )
    upsert_vessel(static)

    updated = static.model_copy(update={"Name": "TEST VLCC RENAMED"})
    upsert_vessel(updated)

    with session_scope() as s:
        v = s.execute(select(Vessel).where(Vessel.mmsi == 999_888_777)).scalar_one()
        assert v.name == "TEST VLCC RENAMED"
        assert v.ship_type == 80
        assert v.length_m == 330
        s.delete(v)
