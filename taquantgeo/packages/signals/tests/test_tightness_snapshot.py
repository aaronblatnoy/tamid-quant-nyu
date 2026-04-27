"""End-to-end snapshot tests for ``compute_daily_tightness``.

Phase 04's unit tests in ``test_tightness.py`` pin each *component* of
the signal math (demand, supply, dark-fleet adjustment, z-score) against
programmatically constructed in-memory DataFrames. Those tests catch
regressions in one term at a time, but they do NOT catch a regression
where every term changes by the same factor (e.g. a global rounding-mode
change, a groupby-key flip, a silent type coercion) — the ratio then
shifts but no single component assertion fires.

This module loads a fixed-on-disk fixture set (``fixtures/snapshot/*``)
and pins the *end-to-end output* of the signal against specific
``as_of`` dates. If any part of the signal math changes in a way that
shifts the final ratio, these tests fail and force the author to either
(a) regenerate the fixtures and update the pinned numbers with an
ADR-worthy justification, or (b) revert. Without this layer, a
signal-drift could merge silently and corrupt a month of backtests
before the IC phase notices.

**Fixture derivation**

Every pinned number below is the output of
``compute_daily_tightness(as_of=<date>, route="td3c")`` against the
parquet files in ``fixtures/snapshot/``. Those parquets are generated
deterministically by ``fixtures/snapshot/regenerate.py`` — see the
README there for the per-row rationale.

The four cached anchorage distances that drive the demand side:

- Ras Tanura → Ningbo : 5920 NM
- Basrah → Qingdao    : 6416 NM
- Ningbo → Ras Tanura : 5920 NM (ballast)
- Qingdao → Basrah    : 6416 NM (ballast)

Every laden voyage uses the 270,000 DWT route nominal because the
fixture registry omits ``dwt``. Per-date in-progress counts (laden →
RT-NB + BR-QD):

- 2020-03-01: 1 laden (v101 RT→NB) → demand = 270k * 5920 = 1.5984 B
- 2020-03-05: 3 laden (v101, v103 RT→NB + v102 BR→QD)
               → 270k * (2*5920 + 6416) = 4.92912 B
- 2020-03-15: 7 laden (v101..v107; 4 RT→NB + 3 BR→QD)
               → 270k * (4*5920 + 3*6416) = 11.59056 B
- 2020-03-18: 7 laden (same as 3/15; v101 ends 3/20, still in-progress)
               → 11.59056 B
- 2020-03-20: 6 laden (v101 ends 3/20 00:00 UTC, excluded;
                        3 RT→NB + 3 BR→QD) → 270k * (3*5920 + 3*6416)
               = 9.99216 B
- 2020-03-22: 6 laden (same as 3/20) → 9.99216 B

Ballast supply: b201 NB→RT starts 3/05, b202 QD→BR starts 3/08. At 13 kn
ballast nominal both arrive inside the 15-day supply horizon for any
``as_of`` from 2020-03-15 onward → supply = 2 on 3/15, 3/18, 3/20, 3/22
and 0 on 3/01, 3/05 (either not started, or ETA outside the 15-day
window for 3/05).

Dark-fleet detections (all unmatched, at PG terminals):

- D1 @ 2020-03-17 10:00 UTC
- D2 @ 2020-03-19 12:00 UTC
- D3 @ 2020-03-21 12:00 UTC

The 7-day dark-fleet window is inclusive on both ends at end-of-day UTC,
giving per-date adjustment counts: 0 on 3/01 / 3/05 / 3/15, 1 on 3/18
(D1), 2 on 3/20 (D1+D2), 3 on 3/22 (D1+D2+D3).

Floor behaviour: ``effective_supply_raw = supply - dark``, clamped to 1
when ≤ 0. ``components["supply_floor_clamped"]`` is 1 when raw ≤ 0.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl
import pytest

from taquantgeo_signals.tightness import compute_daily_tightness

if TYPE_CHECKING:
    from taquantgeo_signals.tightness import TightnessSnapshot

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "snapshot"


def _load_fixtures() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Load all four snapshot parquets; split voyages by route."""
    voyages_all = pl.read_parquet(FIXTURE_DIR / "voyages.parquet")
    voyages_td3c = voyages_all.filter(pl.col("route") == "td3c")
    voyages_ballast = voyages_all.filter(pl.col("route") == "td3c_ballast")
    registry = pl.read_parquet(FIXTURE_DIR / "vessel_registry.parquet")
    distances = pl.read_parquet(FIXTURE_DIR / "distance_cache.parquet")
    dark_fleet = pl.read_parquet(FIXTURE_DIR / "dark_fleet.parquet")
    return voyages_td3c, voyages_ballast, registry, distances, dark_fleet


@pytest.fixture(scope="module")
def snapshot_fixtures() -> tuple[
    pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame
]:
    return _load_fixtures()


def _run(
    fixtures: tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame],
    as_of: date,
    *,
    prior_snapshots_df: pl.DataFrame | None = None,
) -> TightnessSnapshot:
    voyages, ballast, registry, distances, dark = fixtures
    return compute_daily_tightness(
        as_of,
        voyages_df=voyages,
        vessel_registry_df=registry,
        distance_cache_df=distances,
        dark_fleet_df=dark,
        ballast_voyages_df=ballast,
        prior_snapshots_df=prior_snapshots_df,
    )


# ---------------------------------------------------------------------------
# Pinned expected values — derived from the fixture set via the math above.
# Any change that shifts these is a signal-math change and requires ADR
# justification before regenerating the fixtures.
# ---------------------------------------------------------------------------

DEMAND_1_LADEN_RT_NB: int = 1_598_400_000  # 270_000 * 5920
DEMAND_3_LADEN: int = 4_929_120_000  # 270_000 * (2*5920 + 6416)
DEMAND_7_LADEN: int = 11_590_560_000  # 270_000 * (4*5920 + 3*6416)
DEMAND_6_LADEN: int = 9_992_160_000  # 270_000 * (3*5920 + 3*6416)


def test_snapshot_busy_loading_day_matches_frozen_ratio(
    snapshot_fixtures: tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame],
) -> None:
    """2020-03-15 — 7 laden voyages in progress, 2 ballast inside the
    15-day horizon, 0 dark-fleet detections in the trailing 7-day window.

    The "clean" busy-day case: effective_supply = raw supply, no floor
    clamp. Pins demand, supply, dark-adjustment, and the bottom-line
    ratio. A silent regression that changes any single term changes
    at least one of these assertions.
    """
    snap = _run(snapshot_fixtures, date(2020, 3, 15))

    assert snap.forward_demand_ton_miles == pytest.approx(DEMAND_7_LADEN, abs=1)
    assert snap.forward_supply_count == 2
    assert snap.dark_fleet_supply_adjustment == 0
    # ratio = demand / effective_supply = demand / (supply - dark) = DEMAND/2
    assert snap.ratio == pytest.approx(DEMAND_7_LADEN / 2, abs=1)
    assert snap.components["supply_floor_clamped"] == 0
    assert snap.components["in_progress_laden_voyages"] == 7
    assert snap.components["ballast_in_progress"] == 2


def test_snapshot_quiet_day_matches_frozen_ratio(
    snapshot_fixtures: tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame],
) -> None:
    """2020-03-01 — only v101 is in progress; no ballast yet started;
    no dark detections.

    Thin-data behaviour: demand is modest (one RT→NB voyage worth of
    ton-miles), supply is zero → floor clamps to 1 and ratio equals
    demand. The test documents that on a quiet day the signal does not
    spuriously spike — the ratio is proportional to the single voyage's
    remaining ton-miles, not divided by a collapsing denominator.
    Downstream IC code must filter on ``supply_floor_clamped`` before
    trading on a quiet-day ratio.
    """
    snap = _run(snapshot_fixtures, date(2020, 3, 1))

    assert snap.forward_demand_ton_miles == pytest.approx(DEMAND_1_LADEN_RT_NB, abs=1)
    assert snap.forward_supply_count == 0
    assert snap.dark_fleet_supply_adjustment == 0
    assert snap.components["supply_floor_clamped"] == 1
    # Clamped ratio = demand / 1 = demand
    assert snap.ratio == pytest.approx(DEMAND_1_LADEN_RT_NB, abs=1)
    assert snap.components["in_progress_laden_voyages"] == 1
    assert snap.components["ballast_in_progress"] == 0


def test_snapshot_dark_fleet_adjustment_lowers_supply(
    snapshot_fixtures: tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame],
) -> None:
    """2020-03-18 — 7 laden in-progress, supply = 2, one dark detection
    (D1 @ 2020-03-17 10:00 UTC) falls inside the 7-day dark window.

    Dark-fleet adjustment = 1 reduces effective_supply from 2 to 1
    WITHOUT triggering the floor clamp (raw = 2-1 = 1 > 0). The ratio
    therefore doubles relative to the 2020-03-15 "no dark" baseline
    even though demand is identical. This pins the adjustment path
    distinct from the floor-clamp path.
    """
    snap = _run(snapshot_fixtures, date(2020, 3, 18))

    assert snap.forward_demand_ton_miles == pytest.approx(DEMAND_7_LADEN, abs=1)
    assert snap.forward_supply_count == 2
    assert snap.dark_fleet_supply_adjustment == 1
    assert snap.components["effective_supply_raw"] == 1
    assert snap.components["supply_floor_clamped"] == 0
    # Effective supply = max(2 - 1, 1) = 1 → ratio = demand / 1 = demand
    assert snap.ratio == pytest.approx(DEMAND_7_LADEN, abs=1)
    assert snap.components["dark_fleet_candidates_used"] == 1


def test_snapshot_ratio_infinity_when_effective_supply_zero(
    snapshot_fixtures: tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame],
) -> None:
    """2020-03-20 — 6 laden in-progress (v101 ended 3/20 00:00),
    supply = 2, dark adjustment = 2 (D1 + D2) → raw effective = 0.

    ADR 0007 requires the ratio be finite in every realistic regime.
    The floor clamps effective_supply to 1 when raw ≤ 0, so the ratio
    is demand (not inf, not NaN) and ``components["supply_floor_clamped"]``
    is 1. Downstream IC code filters on the flag.
    """
    snap = _run(snapshot_fixtures, date(2020, 3, 20))

    assert snap.forward_demand_ton_miles == pytest.approx(DEMAND_6_LADEN, abs=1)
    assert snap.forward_supply_count == 2
    assert snap.dark_fleet_supply_adjustment == 2
    assert snap.components["effective_supply_raw"] == 0
    assert snap.components["supply_floor_clamped"] == 1
    # Finite — never inf, never NaN.
    assert math.isfinite(snap.ratio)
    assert not math.isnan(snap.ratio)
    # Clamped: ratio = demand / 1 = demand
    assert snap.ratio == pytest.approx(DEMAND_6_LADEN, abs=1)


def test_snapshot_zscore_none_when_history_insufficient(
    snapshot_fixtures: tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame],
) -> None:
    """2020-03-05 — 10 prior snapshots supplied (below the 30-sample
    threshold); z-score must be None.

    Pins the warmup behaviour end-to-end: a backtest run inside the
    first month of history emits ratios but null z-scores, matching
    ADR 0007's ``MIN_Z_SCORE_SAMPLE`` floor. This guards against a
    regression where an eager implementation would emit a z-score on
    any non-empty window, or would raise on insufficient data.

    The assertions below also pin the supply-horizon branch for this
    date: ``ballast_in_progress`` = 1 with ``forward_supply_count`` = 0
    because b201's ETA (2020-03-23) exceeds the 15-day cutoff
    (2020-03-20), distinct from 2020-03-01 where no ballast has started.
    """
    as_of = date(2020, 3, 5)
    # 10 prior days of synthetic ratios — strictly fewer than
    # MIN_Z_SCORE_SAMPLE=30, so the z-score should be None even though
    # some history exists.
    prior = pl.DataFrame(
        {
            "as_of": [as_of - timedelta(days=i) for i in range(1, 11)],
            "ratio": [1.0e9 + i * 1.0e7 for i in range(10)],
        }
    )
    snap = _run(snapshot_fixtures, as_of, prior_snapshots_df=prior)

    assert snap.forward_supply_count == 0
    assert snap.components["ballast_in_progress"] == 1
    assert snap.components["avg_sog_fallback_used"] == 1

    assert snap.z_score_90d is None
    assert snap.components["z_score_sample_size"] == 10
    # Sanity-pin the demand so we catch a regression where the fixture
    # is mis-loaded on this date and the test silently passes.
    assert snap.forward_demand_ton_miles == pytest.approx(DEMAND_3_LADEN, abs=1)


def test_snapshot_regression_for_march_2020_covid_window(
    snapshot_fixtures: tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame],
) -> None:
    """2020-03-22 — comprehensive end-to-end regression pin.

    This is the test phase-04's unit tests could not have caught: it
    combines all four parts of the signal (laden demand, ballast supply,
    dark-fleet adjustment larger than raw supply → floor clamp) against
    a single fixture input and asserts every publicly-visible field.
    If *anything* drifts in the math — a rounding mode, a filter
    operator, a column-order assumption downstream — at least one
    assertion below fires.

    Derivation (spelled out so a reader can re-derive it by hand):

    - In-progress laden on 2020-03-22 = {v102, v103, v104, v105, v106,
      v107} = 6 voyages (v101 ends 2020-03-20, excluded).
    - Origin-dest split: 3 RT→NB (5920 NM) + 3 BR→QD (6416 NM).
    - Every voyage hits the 270,000 DWT nominal (registry has no
      ``dwt`` column).
    - Forward demand = 270,000 * (3*5920 + 3*6416) = 9,992,160,000
      ton-miles.
    - Ballast in progress on 2020-03-22: both b201 (ends 3/24) and b202
      (ends 3/28), both arriving inside the cutoff 2020-04-06 (as_of +
      15d). Supply = 2.
    - Dark-fleet window [2020-03-15 23:59:59.999999, 2020-03-22
      23:59:59.999999] UTC contains all three detections (D1 @ 3/17,
      D2 @ 3/19, D3 @ 3/21). Adjustment = 3.
    - effective_supply_raw = 2 - 3 = -1 → floor clamps to 1,
      ``supply_floor_clamped`` = 1.
    - ratio = 9,992,160,000 / 1 = 9,992,160,000.
    """
    snap = _run(snapshot_fixtures, date(2020, 3, 22))

    assert snap.as_of == date(2020, 3, 22)
    assert snap.route == "td3c"
    assert snap.forward_demand_ton_miles == pytest.approx(DEMAND_6_LADEN, abs=1)
    assert snap.forward_supply_count == 2
    assert snap.dark_fleet_supply_adjustment == 3
    assert snap.ratio == pytest.approx(DEMAND_6_LADEN, abs=1)
    assert snap.z_score_90d is None

    c = snap.components
    assert c["in_progress_laden_voyages"] == 6
    assert c["vlcc_vessels_considered"] == 11
    assert c["cargo_tons_fallback_used"] == 6
    assert c["great_circle_fallbacks"] == 0
    assert c["ballast_in_progress"] == 2
    assert c["avg_sog_fallback_used"] == 2
    assert c["dark_fleet_candidates_used"] == 3
    assert c["effective_supply_raw"] == -1
    assert c["supply_floor_clamped"] == 1
    # Median of [5920, 5920, 5920, 6416, 6416, 6416] = (5920 + 6416) / 2
    assert c["route_total_distance_nm"] == pytest.approx(6168.0, abs=1e-6)
