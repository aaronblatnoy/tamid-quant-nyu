"""Daily tightness-signal job.

Reads the canonical on-disk parquet artefacts (voyages tree, vessel
registry, distance cache, dark-fleet candidates), runs
``compute_daily_tightness``, and optionally upserts the snapshot into
Postgres.

APScheduler wiring happens in phase 11. For v0 we expose only a plain
``run_once(as_of)`` function so:

- the CLI (``taq signals compute-tightness``) can call it,
- tests can exercise it without mocking a scheduler,
- phase 11 can schedule it with minimal glue.

Path defaults mirror phase 01/02/03: all artefacts live under
``data/processed/`` and the defaults are the canonical paths those
phases write. Callers can override any path (for example, to run the
job against a fixture tree in a test) without touching env state.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final

import polars as pl
import sqlalchemy.exc

from taquantgeo_core.db import session_scope
from taquantgeo_signals.models import Signal
from taquantgeo_signals.persistence import upsert_snapshot
from taquantgeo_signals.tightness import (
    TightnessSnapshot,
    compute_daily_tightness,
)

log = logging.getLogger(__name__)

DEFAULT_VOYAGES_DIR: Final[Path] = Path("data/processed/voyages")
DEFAULT_REGISTRY_PATH: Final[Path] = Path("data/processed/vessel_registry.parquet")
DEFAULT_DISTANCE_CACHE_PATH: Final[Path] = Path("data/processed/distance_cache.parquet")
DEFAULT_DARK_FLEET_PATH: Final[Path] = Path("data/processed/dark_fleet_candidates.parquet")
DEFAULT_BALLAST_VOYAGES_DIR: Final[Path] = Path("data/processed/voyages")
"""td3c_ballast partitions live in the same tree as td3c; ``_load_voyages``
scopes by ``route=<value>`` path prefix, not by root directory."""


def _load_voyages(voyages_dir: Path, route: str) -> pl.DataFrame:
    """Load all voyages for the requested route from the canonical tree.

    Returns an empty frame when the tree is absent or no route partition
    exists. The route partition root (``voyages_dir/route=<route>/``) is
    the scan root — using that prefix instead of a substring match
    correctly distinguishes ``route=td3c`` from ``route=td3c_ballast``
    (a substring of the former is a prefix of the latter).
    """
    if not voyages_dir.exists():
        log.warning("voyages dir %s does not exist; returning empty frame", voyages_dir)
        return pl.DataFrame()
    route_root = voyages_dir / f"route={route}"
    if not route_root.exists():
        return pl.DataFrame()
    paths = sorted(route_root.rglob("*.parquet"))
    if not paths:
        return pl.DataFrame()
    return pl.read_parquet(paths)


def _load_parquet_or_empty(path: Path) -> pl.DataFrame:
    if not path.exists():
        log.warning("parquet %s not found; using empty frame", path)
        return pl.DataFrame()
    return pl.read_parquet(path)


def _load_prior_snapshots_df(route: str) -> pl.DataFrame | None:
    """Load ``(as_of, ratio)`` for prior snapshots of ``route``.

    Returns None if the table is unreachable (DB down, not yet migrated)
    so the z-score falls back to None rather than crashing the job. DB
    connectivity and schema-mapping errors both raise
    ``sqlalchemy.exc.SQLAlchemyError``; anything else is programmer
    error and should surface.
    """
    try:
        with session_scope() as sess:
            rows = sess.query(Signal.as_of, Signal.tightness).filter(Signal.route == route).all()
    except sqlalchemy.exc.SQLAlchemyError as exc:
        log.warning("prior-snapshot query failed (%s); z-score will be None", exc)
        return None
    if not rows:
        return pl.DataFrame(schema={"as_of": pl.Date, "ratio": pl.Float64})
    return pl.DataFrame(
        {
            "as_of": [r[0] for r in rows],
            "ratio": [float(r[1]) for r in rows],
        }
    )


def run_once(
    as_of: date | None = None,
    *,
    route: str = "td3c",
    voyages_dir: Path = DEFAULT_VOYAGES_DIR,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    distance_cache_path: Path = DEFAULT_DISTANCE_CACHE_PATH,
    dark_fleet_path: Path = DEFAULT_DARK_FLEET_PATH,
    ballast_voyages_dir: Path = DEFAULT_BALLAST_VOYAGES_DIR,
    persist: bool = False,
) -> TightnessSnapshot:
    """Compute one tightness snapshot using on-disk artefacts.

    ``as_of`` defaults to today's UTC date. ``persist=True`` upserts the
    snapshot into Postgres (requires ``DATABASE_URL`` reachable and
    migration 0002 applied). Returns the snapshot regardless.
    """
    if as_of is None:
        as_of = datetime.now(tz=UTC).date()

    voyages_df = _load_voyages(voyages_dir, route)
    ballast_voyages_df = _load_voyages(ballast_voyages_dir, f"{route}_ballast")
    registry_df = _load_parquet_or_empty(registry_path)
    distance_cache_df = _load_parquet_or_empty(distance_cache_path)
    dark_fleet_df = _load_parquet_or_empty(dark_fleet_path)

    prior_df = _load_prior_snapshots_df(route) if persist else None

    snapshot = compute_daily_tightness(
        as_of,
        voyages_df=voyages_df,
        vessel_registry_df=registry_df,
        distance_cache_df=distance_cache_df,
        dark_fleet_df=dark_fleet_df,
        ballast_voyages_df=ballast_voyages_df,
        prior_snapshots_df=prior_df,
        route=route,
    )

    if persist:
        with session_scope() as sess:
            upsert_snapshot(sess, snapshot)
        log.info(
            "upserted signal as_of=%s route=%s ratio=%.2f",
            as_of,
            route,
            snapshot.ratio,
        )

    return snapshot
