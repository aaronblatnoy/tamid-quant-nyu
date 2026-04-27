# ADR 0005: Sea-route distance for ton-mile calculation

- **Status**: accepted
- **Date**: 2026-04-21
- **Deciders**: Sean Parnell
- **Related**: [ADR 0002](0002-gfw-voyages-as-historical-source.md)

## Context

The TD3C tightness signal is ton-miles forward: each VLCC voyage
contributes `dwt × distance_remaining` to the outstanding demand stock.
"Distance" is defined by shipping markets as the actual sailing distance
through navigable water, not great-circle — both Baltic Exchange
Worldscale flat rates and commercial passage-planning tools quote
sea-miles, not geodesic miles.

Using great-circle for PG → China specifically breaks in two ways:

1. **Undercounts by ~8–10% on the mainline leg.** The geodesic arc from
   Ras Tanura (26.7°N, 50.2°E) to Ningbo (29.9°N, 121.6°E) passes across
   the Arabian Peninsula and northern India; a VLCC must instead round
   the tip of India and pass through Malacca, Lombok, or Sunda. For
   TD3C the real sea-route is ~5,900 NM; great-circle is ~3,700 NM. A
   lower-bias approximation (haversine from an intermediate waypoint)
   still misses the chokepoint.
2. **Becomes asymmetric under congestion.** When Malacca congests and
   operators divert south via Sunda or Lombok, the additional ~600 NM
   (about 10% of the laden leg) is exactly the signal we want — a
   diversion that silently tightens the VLCC supply for 2–3 days.
   Great-circle has no notion of chokepoints, so the diversion doesn't
   register in a ton-mile calculation based on it.

We need a library-backed way to compute sea-route distances for every
unique anchorage pair observed in the GFW voyages CSVs, cached so we
don't re-query it per-voyage on every backtest run.

## Decision

- **Library: `searoute-py` 1.5.x** — MIT-licensed, pure Python, distributed
  with a pre-built waypoint graph derived from the SeaRoutes industry
  dataset. We pin the minor version (`searoute>=1.5,<2.0`) because
  waypoint-graph updates are the primary source of distance drift and
  should be deliberate adopt-new-version events, not automatic.
- **Malacca is the default passage** for PG → China. The
  `compute_route_distance(..., prefer_malacca=True)` default runs
  searoute with its standard restrictions (the Northwest Passage is
  always disallowed — seasonal, irrelevant for TD3C). Switching to
  `prefer_malacca=False` appends `"malacca"` to the restriction list,
  forcing the router to find a Sunda / Lombok path. This is a
  coarse-grained knob; finer-grained congestion-dependent routing is
  deferred to a future phase.
- **Cache shape: one parquet row per directed `(origin_s2id, dest_s2id)`
  pair.** Anchorage s2id is the natural key (already present in GFW's
  voyages CSV). We store directed pairs rather than symmetric ones
  because the searoute graph is symmetric up to waypoint-choice
  variation — we want the row to reflect exactly what the router
  returned for the direction the voyage actually sailed.
- **Columns** (locked; downstream joins depend on both order and types):
  `origin_s2id, dest_s2id, origin_lat, origin_lon, dest_lat, dest_lon,
  nautical_miles (f64), is_great_circle_fallback (bool), computed_at
  (timestamp[us, UTC])`.
- **Great-circle fallback on disconnected inputs.** If searoute raises
  (invalid coordinates), or returns a sub-epsilon length for points
  that are plainly non-coincident (the landlocked-graph-node case),
  we return the haversine great-circle distance and set
  `is_great_circle_fallback=True`. The operator sees a WARN log; the
  downstream signal pipeline can choose to drop fallback rows or accept
  them as approximate.
- **Idempotent CLI.** `taq gfw compute-distances` reads the existing
  cache, computes only the missing pairs, and merges. `--force`
  recomputes every pair (used when pinning a new `searoute` version
  or re-baselining after a graph update).

## Consequences

**Positive**

- Ton-mile demand is measured on the metric the shipping market actually
  uses (sea-miles), aligning our backtest with how FFAs settle and how
  equity analysts model tanker demand.
- Malacca-vs-Sunda diversion signal is captured for free — the moment
  we can observe Malacca congestion (AIS gap cluster, GFW loitering
  events near Singapore), we can re-cost routes with `prefer_malacca=False`
  and see the tightness delta.
- Caching keeps the backtest fast: ~99 unique pairs in the one-month
  TD3C sample → one second of compute vs minutes if we re-routed every
  voyage per backtest.

**Negative**

- **Bound to searoute-py's waypoint graph.** Graph updates shift
  individual pair distances by 1–3% on average. Our snapshot tests
  (±3% tolerance on three pinned pairs) catch regressions but also
  trip on legitimate upstream improvements — each bump is a manual
  review-and-rebaseline step. Documented in
  `packages/ais/tests/test_distance.py`.
- **No intra-voyage dynamic routing.** A vessel that starts down the
  Malacca-preferred path and then diverts mid-voyage gets the full
  Malacca-preferred distance. For in-flight voyages the phase-04
  ton-mile-remaining calculation will use current AIS position ±
  sea-route distance to destination; that captures the diversion once
  the vessel is past the divergence point but not before.
- **Static graph means no seasonal closures or weather routing.**
  Irrelevant for TD3C (no sea-ice; monsoon routing differences are
  minor) but would matter for Atlantic or Russian-Pacific routes.
- **Fallback emits a numerically plausible but incorrect distance for a
  truly disconnected pair.** We log a WARN and mark the row, but a
  naive consumer that ignores the flag would use the great-circle
  value. Downstream signal code must check the flag.

## Alternatives considered

- **Great-circle only.** Cheapest and reproducible, but systematically
  undercounts ton-miles and misses chokepoint diversions — rejected
  for the reasons in Context.
- **`seaport-graph` / custom A* on a manually-curated waypoint graph.**
  We could build our own graph from GFW anchorages + a hand-drawn set
  of mid-ocean waypoints. Rejected: not moat-worthy work for v0, and
  searoute-py already has a battle-tested graph. Reconsider if
  searoute-py becomes unmaintained or the graph turns out to be
  materially wrong for a route we care about.
- **SeaRoutes.com commercial API.** Authoritative but paid (~€0.01
  per routing request, minimum monthly). Over 10 years of historical
  voyages that's a five-figure spend. Searoute-py is the same graph
  exposed offline for free.
- **VesselsValue / Clarksons passage-planning data.** High-quality,
  pre-computed industry distances — available via enterprise
  subscription (~$30k+/year). Not justifiable for v0; upgrade path if
  Vertical 1 proves out.
- **Include ports / docks via `include_ports=True`** in searoute. The
  parameter adds port-of-loading and port-of-discharge as explicit
  graph nodes, which bumps distance by ~10–30 NM per voyage. Rejected
  for v0: our anchorage lat/lons from GFW already sit at the port
  approach, so double-counting dock-approach distance would bias up.
  Reconsider once we have a terminal-precise dataset.
