# ADR 0007: Tightness signal definition (TD3C)

- **Status**: accepted
- **Date**: 2026-04-21
- **Deciders**: Sean Parnell

## Context

Phases 01–03 produced the three ingredients the signal needs:

1. a VLCC registry flagging which GFW `vessel_id`s are VLCC-class
   (`packages/ais/.../classifier.py`, phase 01)
2. a sea-route distance cache keyed by
   `(origin_s2id, dest_s2id)` (phase 02)
3. a SAR-derived dark-fleet candidate table covering PG loading
   terminals (phase 03)

This phase (04) defines the one equation the whole product stands on:
**what is the TD3C tightness signal, mathematically, in terms of these
inputs?** Every downstream phase — IC analysis, backtester, live
pipeline, dashboards, alerts — reads the snapshot this math produces.
Changing the signal later without a new ADR would silently invalidate
backtests and trading history. Pin it now.

The signal intuition is standard freight tightness: **forward cargo
demand divided by forward ballast supply**. When loaded ton-miles
outrun ballast arrivals, rates rise; when ballast arrivals outrun
loaded ton-miles, rates fall. The implementation choices worth pinning
are the exact definitions of each term.

## Decision

**Ratio** — the tightness signal, in ton-miles per ballast vessel:

```
tightness(as_of) = forward_demand_ton_miles(as_of) / effective_supply(as_of)
```

### Forward demand (ton-miles)

```
forward_demand_ton_miles(as_of) =
  Σ { cargo_tons(v) × remaining_distance_nm(v, as_of)
      : v ∈ in_progress_laden_TD3C_voyages(as_of) }
```

- **`in_progress_laden_TD3C_voyages(as_of)`** = voyages with
  `route=td3c`, `trip_start ≤ as_of`, `trip_end` null or
  `> as_of`, and whose vessel is VLCC-candidate in the registry
  (`is_vlcc_candidate=true`). The GFW C4 voyages manifest is the
  laden PG→China leg by construction, so "laden" is implied by
  `route=td3c` — we do not reapply a draft heuristic here.
- **`cargo_tons(v)`** = `vessel_registry.dwt` if present; otherwise
  the TD3C route nominal `270_000` dwt (VLCC standard). The GFW
  identity payload rarely fills `dwt` for our vessels, so the
  nominal is the common path; the field is recorded so future
  enrichment (IMO/registry) drops in without schema change.
- **`remaining_distance_nm(v, as_of)`** = sea-route distance from
  the voyage's current position (or its `orig_s2id` if we have no
  live position yet) to its `dest_s2id`. Phase 02's
  `distance_cache.parquet` serves static anchorage pairs; for
  in-flight positions (future phases 05/06) the distance is
  recomputed per call. Cache miss → great-circle fallback, counted
  in `components["great_circle_fallbacks"]`. In this phase we use
  the static (origin_anchorage → dest_anchorage) pair; position
  interpolation is phase 05+.

### Forward supply (count)

```
forward_supply_count(as_of) =
  |{ v : v ∈ ballast_VLCCs(as_of)
       AND estimated_arrival_pg(v, as_of) ≤ as_of + supply_horizon_days }|
```

with `supply_horizon_days=15` (half a typical TD3C transit — the
window inside which a ballast VLCC can credibly commit to a PG lift).
`estimated_arrival_pg(v, as_of) = as_of + distance_to_pg_nm(v, as_of)
/ (24 × avg_sog_knots(v))`. Missing AIS history → fall back to the
VLCC-ballast nominal 13 knots; record the fallback count in
`components["avg_sog_fallback_used"]`.

Without a live AIS stream yet (phase 05+), "ballast VLCCs" is proxied
by `route=td3c_ballast` voyages in the GFW C4 manifest that are
in progress as of `as_of`. This is a weak proxy — GFW's `td3c_ballast`
set is small and lags ~30 days — but it slots into the same plumbing
and lets the backtester run. Phase 05 will replace it with the live
AIS-derived ballast set.

### Dark-fleet supply adjustment

```
dark_fleet_supply_adjustment(as_of) =
  |{ d ∈ dark_fleet_df :
       d.nearest_anchorage in MAJOR_LOADING_TERMINALS
       AND d.has_matching_voyage = false
       AND (as_of - 7d) ≤ d.detection_timestamp ≤ as_of }|
```

A 7-day window catches a SAR detection within the typical loading
window of a lift we may not see for another 3 days in GFW's Events
API latency. All hits from phase 03 are already filtered to major
loading terminals; the repetition here is defensive — if a caller
hands us a broader SAR table, the adjustment still only counts
terminal-proximate hits.

### Effective supply (floor)

```
effective_supply = max(forward_supply_count − dark_fleet_supply_adjustment, 1)
```

The floor of 1 serves two purposes: (a) it makes the ratio a finite
number in every realistic regime; (b) it signals "we're guessing" to
the IC / backtest phases, who must filter on
`components["supply_floor_clamped"]` to decide whether to trade on
that snapshot.

If raw (non-floored) effective supply is strictly ≤ 0 we set
`components["supply_floor_clamped"] = true`. Phase 07 will probably
drop floored days from the IC sample.

### Z-score (90-day, lookahead-free)

```
window   = { ratio(as_of') : as_of' ∈ prior 90 calendar days before as_of,
                             as_of' < as_of }
mean_90d = mean(window)
std_90d  = stdev(window, sample, ddof=1)
z_score_90d(as_of) = (ratio(as_of) − mean_90d) / std_90d   if |window| ≥ 30
                   = None                                  otherwise
```

Strictly `as_of' < as_of` — the signal of a day does not include
itself in its own baseline. The `|window| ≥ 30` floor prevents a
z-score fired from a 3-day warmup. `std_90d == 0` → None and a WARN.

### Reproducibility contract

`compute_daily_tightness` is **deterministic for fixed inputs**. There
is no random sampling. The `components` dict records every fallback
and clamp so any snapshot can be replayed and any anomaly traced to
its input.

### Snapshot shape

```python
@dataclass(frozen=True)
class TightnessSnapshot:
    as_of: date
    route: str
    forward_demand_ton_miles: int
    forward_supply_count: int
    dark_fleet_supply_adjustment: int
    ratio: float
    z_score_90d: float | None
    components: dict[str, int | float]
```

`components` includes at minimum:

- `vlcc_vessels_considered` — registry rows with
  `is_vlcc_candidate=true`
- `in_progress_laden_voyages` — count of voyages summed into demand
- `cargo_tons_fallback_used` — voyages that used the 270,000 dwt
  nominal
- `great_circle_fallbacks` — distance-cache misses
- `avg_sog_fallback_used` — ballast vessels using the 13-knot nominal
- `route_total_distance_nm` — median TD3C distance for the voyages
  used (diagnostic)
- `supply_floor_clamped` — bool 0/1 flag
- `effective_supply_raw` — pre-floor supply count for audit

### Persistence

`signals` table (alembic 0002) stores one row per `(as_of, route)`:

| column | type | notes |
|---|---|---|
| id | bigserial PK | |
| as_of | date | trading day |
| route | text | `td3c` for v0 |
| forward_demand_ton_miles | bigint | |
| forward_supply_count | int | |
| dark_fleet_supply_adjustment | int | new vs data_model draft |
| tightness | double | the ratio |
| tightness_z | double NULL | 90-day z, null on warmup |
| components | jsonb | audit trail |
| created_at | timestamptz | |
| UNIQUE (as_of, route) | | upsert key |

The `ratio` column is stored as `double precision`, not integer cents,
because the ratio is an indicator not a money value. Money rule
(`integer cents`) does not apply here.

## Consequences

**Positive**

- Math is pinned: backtests, IC analysis, and live trading share one
  deterministic definition. Any change requires a new ADR and a new
  signal version.
- The `components` dict makes every snapshot reproducible and every
  fallback auditable. Backtests over months with no fallbacks differ
  meaningfully from backtests where the distance cache was empty
  and everything fell back to great-circle — `components` makes that
  visible.
- Supply floor prevents inf/NaN poisoning in the time series. Flag in
  `components` lets IC phase cleanly drop anomalous days.
- 90-day z-score provides a dimensionless signal suitable for
  regression against equity returns without scale normalisation.

**Negative**

- Supply proxy via `td3c_ballast` voyages is weak pre-phase-05;
  expect the z-score distribution to shift when live-AIS ballast
  replaces it. Deliberate: we need the plumbing now; the fidelity
  upgrade is a later phase and intentionally does not require an
  ADR amendment (the *definition* of "ballast VLCC" has not
  changed; the data source has).
- `cargo_tons` defaults to the route nominal for virtually all
  voyages because GFW rarely fills dwt. Per-vessel dwt variance for
  VLCCs is ~10% (270k ± 30k), so the aggregated error over dozens of
  voyages is small but not zero. Accept for v0; phase 10+ can pull
  IMO-registry dwt.
- 7-day dark-fleet window is a judgment call — we have no labelled
  dark-loading data to calibrate it. Sensitivity analysis is a later
  phase.
- `as_of' < as_of` strictness in the z-score window excludes the
  current day's ratio, which is correct for lookahead avoidance but
  means a Sunday-evening snapshot has one less data point in its
  z-score than an identical snapshot taken Monday morning. This is
  the right tradeoff but is worth flagging to future implementers.

## Alternatives considered

- **Great-circle for all distances.** Rejected in phase 02 ADR 0005.
  Consistency matters: the signal and the backtest must agree on what
  "ton-miles" means, so sea-route is used everywhere.
- **Rolling-mean baseline instead of z-score.** Z-score is
  scale-invariant and signed; mean-only baselines need additional
  scaling before feeding a linear regression against equity returns.
  We'd have to compute the z-score anyway downstream, so compute it
  once at source.
- **Trade-day calendar instead of calendar day.** TD3C activity
  does not sleep on weekends — vessels sail Saturday and Sunday.
  Using calendar days preserves the weekend tightness evolution and
  matches the daily IBKR trade cadence (we trade Monday morning on
  Sunday-evening signal). The T+1 lag invariant in CLAUDE.md is
  preserved: signal computed at Sunday 22:00 UTC, trade executed at
  Monday NY open.
- **Storing ratio as integer "tenths-of-a-ton-mile-per-vessel".**
  Rejected: ratio is an indicator, not money. Float is fine.
- **Tightening the supply horizon to 10 days.** The data_model draft
  had 15. Tightening would under-count genuine committable ballast
  (a vessel 14 days out is still a plausible PG lift). Loosening to
  20 would over-count (vessels that deep in ballast can easily divert
  to another region). 15 is the midpoint of the typical TD3C ballast
  round-trip half; keep.
- **Passing individual frames into `compute_daily_tightness` vs
  discovering them from canonical paths.** Passing them in keeps the
  function pure (no IO), which is the phase-02 / phase-03 convention
  and makes unit testing straightforward. The CLI and the job do the
  IO.
