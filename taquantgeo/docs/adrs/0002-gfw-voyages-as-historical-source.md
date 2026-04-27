# ADR 0002: GFW voyages CSVs as the historical-AIS backbone

- **Status**: accepted
- **Date**: 2026-04-21
- **Deciders**: Sean Parnell

## Context

Phase 1a gives us a live AIS stream that accumulates forward. For the signal to be backtestable we also need years of historical data. The original plan assumed we would pull from Global Fishing Watch's research-tier BigQuery dataset, but that access requires a research application that takes weeks.

While exploring alternatives, we discovered GFW publishes **direct CSV downloads** for their "All Vessels Voyages Confidence 4 (v3)" dataset — anchorage-to-anchorage voyages for every vessel they track, going back to January 2017, updated daily. No BigQuery, no research gate. Monthly files are ~300 MB each; ~22 GB buys us the full history.

## Decision

Use **GFW's All Vessels Voyages C4 (v3) CSVs** as the historical backbone for TD3C backtesting. Live AISStream data (Phase 1a) covers forward data. The two sources join on `mmsi` / `ssvid`.

## Why "Confidence 4" and not a lower tier

GFW publishes the data on a 1-to-4 scale where **higher is more confident**. This was initially confusing because GFW's own dataset page says: "c4 refers to confidence 4, as we have more noisy voyages with less confidence" — which reads as if c4 is noisy. It isn't. The phrase is awkward GFW wording; cross-reference with GFW's FAQ clarifies:

> "Confidence level 4, where we see the full activity of a vessel entering and exiting the port, shown in the public Global Fishing Watch platform and through the Global Fishing Watch data download portal."

So C4 = **fully observed port visit** (entry + exit + stop or gap). C1–C3 are progressively more partial/noisy (one or more of the observable pieces missing — usually because AIS was switched off or coverage was patchy). The public download portal gives us C4. That is the strictest filter and the one we want.

The Oceana "Never-Ending Voyages" report paraphrases the C4 definition with a specific 0.1-knot speed threshold, but GFW's own documentation publishes **0.2 knots** as the port-stop threshold (and 0.5 knots as the exit threshold). We cite the GFW-direct numbers in `docs/signals.md` when we get there; we do not rely on Oceana's paraphrase.

## Schema we consume (voyages)

| Column | Type | Use |
|---|---|---|
| `ssvid` | int (MMSI) | join to AIS; vessel identity |
| `vessel_id` | string | GFW internal ID; lookup via REST API for ship type / tonnage |
| `trip_id` | string | uniqueness |
| `trip_start`, `trip_end` | timestamp (UTC) | voyage window |
| `trip_start_anchorage_id` | string (s2id) | join to Anchorages → lat/lon/iso3/label |
| `trip_end_anchorage_id` | string (s2id) | same |
| `trip_start_visit_id`, `trip_end_visit_id` | string | port-visit references (not used yet) |

## Known gaps and how we close them

### Gap 1 — No ship-type in voyages CSV (how do we know it's a VLCC?)

The voyages file has `ssvid` but not ship type. A PG→China voyage could be VLCC, Suezmax, or even a container ship. We close this three ways:

1. **GFW REST API** (`/v3/vessels/{vessel_id}` with token) — tested and works for tankers. Returns `gfw_shiptypes`, IMO, gross tonnage. Code lives in `packages/ais/src/taquantgeo_ais/gfw/api.py`. See follow-on work for batched classification.
2. **Our own live AIS ShipStaticData** — collected by Phase 1a's streamer. Gives us AIS ship type code (80 = oil tanker) plus dimension (length ≥ 320 m = VLCC).
3. **Route + duration heuristic** — the `filter_by_route` function in `voyages.py` narrows to the route's typical transit-day band (18–35 days for TD3C). 70–80% of vessels in that band on PG→China are VLCCs.

We layer all three. Primary: GFW identity (coverage). Backup: AIS static (ground truth). Sanity: duration filter.

### Gap 2 — Dark fleet (AIS-off VLCCs)

Iranian crude flows use AIS spoofing or blackouts extensively. GFW's C4 filter is strict ("fully observed") so **these voyages are systematically excluded**. We track the gap but don't try to model it in v0.

Partial mitigation: GFW's **Sentinel-1 SAR vessel detections** dataset spots vessels via satellite radar independent of AIS. We've downloaded three monthly snapshots (202510–202512). Future work will cross-reference SAR detections at known VLCC loading anchorages against AIS-reported voyages; SAR hits without a corresponding AIS-reported voyage are dark-fleet candidates.

### Gap 3 — Reception-quality bias

GFW's coverage is denser near shore (coastal AIS base stations) than mid-ocean (satellite only). The mid-Indian Ocean segment of TD3C has patchy satellite AIS reception. Some voyages' timestamps will be biased late/early because the entry/exit observation was delayed.

We don't correct for this in v0 but note it in the backtest report. We requested GFW's "Reception Quality Data v3" dataset but it wasn't in the current public catalog.

### Gap 4 — TD3C freight rate history (not a GFW gap, but a real one)

Baltic Exchange publishes the TD3C spot rate and FFA settlements daily. Historical data is subscription-only (~$5k/year retail). We don't have access in v0. Mitigation: backtest against shipping-equity proxies (FRO, DHT, INSW, EURN, TNK) which correlate ~70–80% with TD3C at weekly horizon. Applied for Baltic academic access; upgrade if approved.

### Gap 5 — Co-loading, STS transfers, part-cargo deals

The voyages dataset reports anchorage-to-anchorage transits; it does not know about mid-ocean ship-to-ship transfers. Iranian crude often hops via STS to avoid port-call sanctions. GFW's `Transshipment Behavior` dataset (Miller et al. 2018) covers the older snapshot; we haven't wired it in v0.

## Consequences

**Positive**

- Backtest is no longer blocked on research-tier access.
- GFW has already done the voyage classification work for us — Phase 2 (voyage classifier) becomes "enrich GFW voyages with ship type and ton-miles" instead of "build a state machine from raw positions."
- ~9 years of history available immediately.

**Negative**

- Bound to GFW's schema changes (they have bumped pipeline versions — `pipe_v3` for files after Jan 2026, different naming pre-2026). Our extractor handles both naming conventions.
- Dark-fleet undercoverage is systematic and known-unfixable with this source alone.
- No intra-voyage position data (only start and end). Phase 2 can't reconstruct a vessel's current location on an in-progress trip from these files; that's what the live AIS stream is for.

## Alternatives considered

- **GFW BigQuery research tier** — applied, waiting on approval. If approved, we'd use `gfw_research.pipe_v3.research_messages` for per-message AIS and could build our own voyages rather than trusting GFW's C4 pipeline. Kept as upgrade path.
- **Spire Maritime academic tier** — applied. If approved, gives us live + historical tanker AIS at higher granularity than AISStream's free tier.
- **Datalastic one-time purchase** (~$150-300) — not taken since GFW CSVs cover our immediate need.
- **Build voyages from raw AISStream accumulation** — we'd need to wait 30–60 days; we still can as a validation layer later, comparing GFW C4 voyages against our own derived ones for the same vessels.
