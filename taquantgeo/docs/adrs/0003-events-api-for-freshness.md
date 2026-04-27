# ADR 0003: GFW Events API as the near-real-time freshness layer

- **Status**: accepted
- **Date**: 2026-04-21
- **Deciders**: Sean Parnell
- **Related**: [ADR 0002](0002-gfw-voyages-as-historical-source.md)

## Context

ADR 0002 decided the C4 voyages CSVs are the historical backbone. That solves
backtest but leaves a freshness gap: the monthly voyages file for month M is
published in the first days of month M+1, meaning we can be up to ~30 days
behind "now" on voyage start / end events. For a signal that wants to react to
a wave of laden PG→CHN departures or a jump in Chinese port congestion, 30
days late is useless.

GFW's `/v3/events` REST endpoint addresses exactly that gap. It exposes four
event datasets, each updated on a ~3-day lag instead of monthly. Events are
queryable per vessel_id over arbitrary date windows. We already have the REST
token (same one used for vessel identity in ADR 0002).

## Decision

Use **GFW `/v3/events` as the near-real-time layer**, layered on top of the
voyages CSVs. The client lives in
`packages/ais/src/taquantgeo_ais/gfw/events.py` (see `EventsClient`,
`iter_events`). Two CLI commands — `taq gfw sample-events` (probe) and
`taq gfw fetch-events` (batch write to parquet) — land alongside.

### Which event kinds and why

Priority order, matching the signal pipeline we need:

1. **`port_visit`** — the primary signal. Confirms a voyage has actually
   started / ended by observing the anchorage entry + exit. Filterable by
   `confidences=[4]` — the same C4 tier the voyages CSV uses — so the two
   sources stay apples-to-apples. Closes the "did the vessel actually arrive
   in China last week?" question without waiting for the monthly CSV.
2. **`gap`** — AIS-off events. Flags potential dark-fleet behavior (ADR 0002
   Gap 2). `intentional_disabling: true` is GFW's own classification — we
   don't have to build that detector ourselves. Useful both as a
   dark-fleet-proxy signal and as a data-quality warning ("this voyage's
   timestamps are probably biased").
3. **`encounter`** — ship-to-ship transfers. Directly addresses Gap 5 in
   ADR 0002 (co-loading / STS sanctions evasion). Lower priority because the
   trading signal impact is second-order, but useful context for a voyage.
4. **`loitering`** — vessels sitting still mid-ocean. Nice to have for
   detecting floating storage or congestion queues near load/discharge
   anchorages, but lowest priority for the v0 signal.

Phase 1b ships the client for all four kinds and the CLI for port-visit.
Batched-write CLI support for gap / encounter / loitering is intentionally
simple-because-identical (same `iter_events` surface, different `event_kind`
argument) and lands in the same PR.

### Rate-limit reality

GFW publishes no explicit rate limits on the public tier, but 429s happen
under bulk scan load. Our client:

- Batches up to 50 vessel_ids per HTTP call (`vessels[0..49]` indexed params)
  — keeps URL length safe and cuts request count ~50×.
- Exponential backoff on 429 starting at 5s, doubling up to a 300s cap, for
  up to 8 attempts (~8.5 min worst case). On exhaustion the client **logs and
  returns** rather than raising, so a nightly batch doesn't crash because one
  vessel triggered throttling near the end of the run. The next run will pick
  the batch back up.
- No retry on other error statuses. 4xx that isn't 429 means the caller
  constructed a bad request and should see the exception.

### Storage layout

`data/processed/events/type=<kind>/year=YYYY/month=MM/events_<since>_<until>.parquet`

One parquet file per run (batched), year/month partition derived from the
`--since` date. Per-vessel-per-run files were considered and rejected: the
TD3C fleet is ~700 vessels, so per-vessel output would create thousands of
tiny files that slow DuckDB scans. The downstream consumer cares about "all
events in window [a, b]", and a single row-oriented parquet with a `type`
column answers that more cheaply.

### Why not BigQuery research tier instead

Same reason as ADR 0002: research-tier access needs multi-week application
review; the public events API is already authenticated and working. If the
research tier lands later, events remain useful for the freshness gap at the
head of the timeline — BigQuery research snapshots have their own lag.

## API quirks worth noting

Discovered during live probing 2026-04-21, codified in `events.py` docstring
and tests:

- `confidences[0]` is valid **only** on the port-visits dataset; passing it on
  gap / encounter / loitering triggers a 422. `iter_events` gates this on the
  caller side — port-visits passes confidences through, other kinds suppress.
- `limit` without `offset` → 422. We always send both.
- Unknown vessel_id → **200 with empty entries**, not 404. The client passes
  that through as zero events.
- Pagination is `offset` / `limit`. `nextOffset: null` terminates. Total
  count in `total` for sanity-checking but not required for pagination.
- Several nominally-numeric fields arrive as strings (`confidence: "4"`,
  `distanceKm: "1054.83"`, `onPosition.lat: "-5.09"`). Pydantic's lax coercion
  handles these; no custom validators needed beyond `extra="ignore"`.
- Timestamps are ISO 8601 with `Z`. No Go-time weirdness here (unlike the
  AISStream live feed — see `taquantgeo_ais/models.py`).

## Consequences

**Positive**

- Signal freshness cut from ~30 days (monthly CSV) to ~3 days (events API).
- Dark-fleet proxy (intentional AIS disabling) becomes available without
  building our own gap detector.
- Same auth token as vessel-identity; no new credential plumbing.
- Reusable beyond TD3C — any future route only needs a different vessel_id
  list.

**Negative**

- Rate-limited tier; bulk historical re-scans (years × thousands of vessels)
  are slow. We mitigate by keeping history in the voyages CSV path and using
  events only for recent windows.
- Schema coupling: GFW has shipped breaking changes before. Mitigation:
  `extra="ignore"` on Pydantic models plus a VCR cassette test per event
  kind, so a schema drift fails in CI before hitting production.
- Four event kinds × four subfield shapes = more surface to maintain. Kept to
  one module.

## Alternatives considered

- **Build our own port-visit detector on top of AISStream live positions.**
  Takes weeks of accumulation to get useful coverage, and we'd have to ship a
  voyage state machine with edge cases GFW has already solved. Deferred.
- **Use voyages CSVs with a weekly roll-forward hack** — re-download the
  partial current-month CSV every few days and diff. GFW doesn't publish
  partial-month files reliably; and even when they do, voyages-in-progress
  aren't included until the end-anchorage is observed. Doesn't actually
  close the gap.
- **Polling individual `/vessels/{id}` endpoints for current state.** No
  port-visit semantics — only identity. Wrong tool.
- **Spire Maritime live tier.** Application pending. If approved, events API
  becomes a secondary source; keep both for cross-validation.
