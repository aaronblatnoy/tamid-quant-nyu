# Research log

Append-only log of qualitative findings — things that don't fit a commit message and don't warrant a full ADR. Newest entries on top.

## Format

```
## YYYY-MM-DD — short title
**Context**: what prompted this entry
**Finding**: what we learned
**Implication**: what we're going to do (or not do) about it
**Refs**: links, commit hashes, issue numbers
```

---

<!-- entries below this line, newest first -->

## 2026-04-21 — Phase 07 IC fail-fast gate: BLOCKED on signal-history shortage (not no-edge)

**Context**: Phase 07 ships the IC fail-fast gate (compare.py + `taq signals ic` + ADR 0009). Real-data run executed against the operational Postgres at PR #12 merge (sha `a12a679`). The gate is the harness's go/no-go decision before the backtester is built — pass = continue to Phase 08, fail-fast = halt and revisit Phase 04 math.

**Finding**: Verdict is `BLOCKED_INSUFFICIENT_DATA`, not `FAIL_NO_EDGE`. The harness halts but for a data-prerequisite reason rather than a flat-signal reason. Concrete state at the time of the run:
- **Prices**: 9 348 daily rows after `taq prices backfill --since 2017-01-01` against the basket. FRO / DHT / INSW / TNK each have ~2 337 rows spanning 2017-01-03 → 2026-04-20 (NYSE close calendar). EURN delisted per ADR 0008's "Negative" section — backfill returned 0 rows for it as expected.
- **Signals**: exactly 1 persisted row (the 2026-03-15 snapshot from Phase 04). That row has `components["supply_floor_clamped"] = 1` and is filtered out by the CLI per the Phase 04 contract — so 0 usable signals reach the IC computation.
- **Walk-forward windows produced**: 0 across every (horizon, method) cell.
- **Verdict path**: `evaluate_verdict` returns `BLOCKED_INSUFFICIENT_DATA` because (a) zero viable cells after the windowing loop and (b) `cells_in_gate.empty == True`.

The shortage is structurally Phase-01-bound, not Phase-07-bound: only one month (2026-01) of GFW C4 voyages is on disk, so even if all signal computations were de-floored, we could compute at most ~30 daily signals — well below `min_history_days = 180`. The gate was designed to halt on this exact prerequisite shortage rather than silently producing garbage windows.

**Implication**:
- Phase 07's tooling is correct and the verdict is honest. Future re-runs will pick up new data automatically — no code change needed.
- The unblock path is a multi-month historical voyages backfill (more `voyages_c4_pipe_v3_<YYYYMM>.csv` runs through `taq gfw ingest-voyages`), then a batch run of `taq signals compute-tightness --as-of <date> --persist` over the resulting date range. Roughly: 2 years of voyages → ~24 months × 30 days × 1 snapshot/day = 720 signal rows, more than enough for the gate.
- Until that backfill happens, Phase 08 (backtester) is blocked behind Phase 07 by the harness. The driver will see the `blocked` handoff status and halt. The user gets a clear manual-setup-required entry pointing to the prerequisite.
- A future "Phase 07b" (or a small follow-up phase) should bundle the historical-signal compute job — running `compute-tightness` over a wide date range needs care to ensure prior_snapshots_df is loaded correctly per-iteration so each day's z-score uses only its own history.

**Refs**: `reports/ic_analysis.md` (gitignored, generated each run); ADR 0009; PR #12 (sha `a12a679`); Phase 04 handoff for the floor-clamped-filter contract; Phase 06 handoff for the price backfill capability.

---

## 2026-04-21 — Tightness snapshot tests: end-to-end drift guard

**Context**: Phase 05 adds `test_tightness_snapshot.py` — end-to-end regression tests that pin the bottom-line `TightnessSnapshot` output against a frozen on-disk fixture set (`packages/signals/tests/fixtures/snapshot/`). Phase 04's unit tests cover each component of the signal (demand, supply, dark-fleet, z-score) independently; a global regression — a rounding mode flip, a silent type coercion, a groupby-key change — can shift every component by the same factor and leave single-component assertions passing while the ratio silently drifts. The snapshot tests pin the ratio directly on six dates in March 2020 so that regime cannot merge silently.

**Finding** — The fixture set is 12 voyages (10 laden `td3c` + 2 `td3c_ballast`) + 12 registry rows + 4 distance-cache pairs + 3 dark-fleet detections, all deterministically constructed by `fixtures/snapshot/regenerate.py`. Pinned per-date outputs:

| as_of | demand (ton-miles) | supply | dark | eff | ratio | clamped |
|---|---:|---:|---:|---:|---:|---:|
| 2020-03-01 | 1,598,400,000 | 0 | 0 | 1 | 1,598,400,000 | 1 |
| 2020-03-05 | 4,929,120,000 | 0 | 0 | 1 | 4,929,120,000 | 1 |
| 2020-03-15 | 11,590,560,000 | 2 | 0 | 2 | 5,795,280,000 | 0 |
| 2020-03-18 | 11,590,560,000 | 2 | 1 | 1 | 11,590,560,000 | 0 |
| 2020-03-20 | 9,992,160,000 | 2 | 2 | 1 | 9,992,160,000 | 1 |
| 2020-03-22 | 9,992,160,000 | 2 | 3 | 1 | 9,992,160,000 | 1 |

- **The 7-day dark-fleet window is inclusive on both ends at end-of-day UTC.** Per ADR 0007 `window_start = as_of_EoD - 7d` and `kept = (detection >= window_start) AND (detection <= as_of_EoD)`. A detection at `2020-03-17 10:00 UTC` falls inside the 2020-03-18 window `[2020-03-11 23:59:59.999999, 2020-03-18 23:59:59.999999]` because 10:00 > 23:59:59.999999 of 3/11? Yes — the window-start is 3/11 EoD (23:59) so the detection at 3/17 10:00 is ≥ that. Easy to trip on this when designing a fixture; snapshot tests document the exact boundaries by pinning the counts.
- **Ballast ETA vs `trip_end`.** A ballast voyage counts toward supply iff `trip_start ≤ as_of ≤ trip_end (or null)` AND `trip_start + travel_hours ≤ as_of + 15d`. Both filters need to pass. The 2020-03-05 row in the fixture exercises the case where the ballast is in-progress but its ETA exceeds the 15-day horizon, so supply stays at 0. That regime pinning would not surface if only `trip_end` controlled the supply count.
- **v101's exclusion on 2020-03-20 pins the strict-greater-than filter.** `trip_end = 2020-03-20 00:00:00` (naive → UTC-localised) does NOT satisfy `trip_end > as_of_EoD` because `2020-03-20 00:00 < 2020-03-20 23:59:59.999999`. The laden-in-progress count drops from 7 to 6 between 3/18 and 3/20; that +/- 1 voyage is the difference between 11.59 B and 9.99 B ton-miles of demand. If the filter ever flips to `>=` these tests fail loudly.
- **`route_total_distance_nm` median is the even-n average.** For 6 voyages with distances `[5920, 5920, 5920, 6416, 6416, 6416]`, Python's `statistics.median` returns `(5920 + 6416)/2 = 6168.0`. Pinned on 2020-03-22 so a later change to the median definition (e.g., floor-of-middle, nearest-below) fails.

**Implication**:
- Any PR that changes the signal math must now choose between updating the pinned numbers (and writing an ADR explaining why) or reverting. The README in `fixtures/snapshot/` spells out the regeneration steps so the "legitimate change" path stays ergonomic — re-run `regenerate.py`, re-run the test, paste the new numbers.
- The fixture set is deliberately TD3C-only and doesn't cover live-AIS ballast or enriched `dwt`. Phase 05+ should extend the fixture set once those code paths exist; for now the snapshot tests pin the GFW-proxy era's math.
- A future `ballast_in_progress > 0 but supply == 0` regression would fire on the 2020-03-05 pin (the ETA-horizon branch).

**Refs**: `.build/handoffs/05_handoff.md`; `test/phase-05-signal-snapshots` branch; ADR 0007.

---

## 2026-04-21 — TD3C tightness signal: math pinned, first live snapshot

**Context**: Phase 04 pins the TD3C tightness math in code ([ADR 0007](adrs/0007-tightness-signal-definition.md)) and wires it into a CLI + Postgres-backed `signals` table. First live snapshot computed against real phase-01/02/03 outputs.

**Finding** — First live `compute_daily_tightness(as_of=2026-03-15, route=td3c)` on the 2026-03 smoke dataset:

- **58 VLCC candidates** in the registry; **17 in-progress laden TD3C voyages** on 2026-03-15, every one hitting the **270 000 dwt nominal fallback** (GFW identity rarely populates `dwt`; none did). 0 great-circle fallbacks — every voyage's anchorage pair hits the distance cache.
- **Forward demand = 26.47 B ton-miles** = 17 vessels × 270k dwt × median 5 894 NM (distances span 4 729–6 826 NM, consistent with the phase-02 snapshot).
- **Ballast (td3c_ballast) supply = 0** — the TD3C-filtered voyages tree holds no `td3c_ballast` partitions because phase 01 hasn't extracted them yet. This is the dominant v0 limitation: the signal's denominator is almost entirely the supply floor. **Documented as a non-goal in phase 04** — phase 05+ replaces this proxy with live-AIS ballast detection. Until then, backtests should expect the z-score distribution to shift materially when live AIS comes online.
- **Dark-fleet adjustment = 4** — 4 of the 17 phase-03 candidates fall within the 7-day window before 2026-03-15. Raw effective supply = 0 − 4 = −4 → floor clamps to 1, `supply_floor_clamped = 1` in `components`.
- Ratio stored as 26 472 488 475 ton-miles/ballast (capped by the supply floor). Unsurprising given the denominator situation; the number is not yet signal-quality until phase 05 lands.
- **The `components` dict carries 11 keys** for audit. Any backtest that trades off tightness MUST join on `supply_floor_clamped=0` before using the ratio, or report both filtered and unfiltered stats.

**Implication**:
- Signal plumbing is green end-to-end: parquet → pure function → frozen dataclass → Postgres upsert. Phase 05+ swaps the ballast source without touching `compute_daily_tightness` or the table; `components["avg_sog_fallback_used"]` will flip to reflect real live-AIS SOG once that lands.
- The 270k dwt nominal is used for every voyage today. If a vessel-registry enrichment phase adds `dwt`, per-voyage ton-miles shift by the ratio `real_dwt / 270_000` (typically ± 10 %). Backtests that want to compare "with vs without dwt" should re-run compute after registry enrichment with the same voyages.
- The supply floor + the `supply_floor_clamped` flag are the load-bearing mechanism that prevents inf/NaN propagation during the ballast-proxy era. IC / backtest phases must filter on this flag before regressing.
- The per-vessel "cargo_tons fallback" WARN log can spam at 17 lines per daily run; current behaviour matches the phase contract ("log a WARN including the vessel_id"). If log volume becomes an ops issue, the right fix is aggregated end-of-run WARN + per-vessel DEBUG, not dropping the audit.

**Refs**: ADR 0007; `feat/phase-04-tightness-signal` branch; alembic migration 0002.

---

## 2026-04-21 — GFW SAR vessel-detections: schema + dark-fleet dynamics on PG loading terminals

**Context**: Phase 03 builds a dark-fleet candidate proxy by joining Sentinel-1 SAR vessel detections spatially to PG VLCC loading terminals and temporally to our AIS-reported voyages. Ran against `data/raw/gfw/sar_vessels/sar_vessel_detections_pipev4_202603.csv` (107,257 global detections, 2026-03) as the smoke dataset. See [ADR 0006](adrs/0006-sar-dark-fleet-cross-ref.md).

**Finding** — SAR schema + coverage observed:

- CSV columns: `scene_id, timestamp, lat, lon, presence_score, length_m, mmsi, matching_score, fishing_score, matched_category`. Timestamps are `YYYY-MM-DD HH:MM:SS UTC` strings (suffix `" UTC"`, not ISO). `mmsi` is nullable `Int64`; NULL on ~27% of global detections. `length_m` is always populated; distribution is heavily right-skewed (p50 ~63 m, p75 ~146 m, max 422 m). `matched_category` has 10 levels; `unmatched` is modal (~40%).
- Header-only CSVs (the 2026-04-17 file was shipped with 0 rows) poison `polars.read_csv` dtype inference — all columns come back as `String`. Later `pl.concat` promotes numeric columns to String and downstream filters crash with "cannot compare string with numeric type". Fix: pin a `schema_overrides` dict on every SAR CSV read. Already bit this pipeline once; logging here so the next engineer doesn't re-learn it.
- After `length_m >= 200 m` and `<= 10 km of a major-loading-terminal anchorage` filters, the 107k global rows collapse to **17 detections** across 6 of the 11 major TD3C loading terminals for 2026-03. Breakdown: RAS TANURA 3, DAS ISLAND 8, KHARK 3, AL-BASRAH OIL TERMINAL 1, FUJAIRAH 1, MINA AL AHMADI 1. No hits at Ras Laffan, Juaymah, Jebel Dhanna, Jebel Ali, Assaluyeh — consistent with Sentinel-1's coverage bias (some of those are SAR-shadowed).
- Cross-reference against our current (TD3C-only) voyages parquet: **all 17 flagged as "no matching voyage in ±3 days"**. Of those, 5 have NULL mmsi (fully dark), 12 have mmsi (AIS-visible but no recorded TD3C voyage start at that anchorage within the window). The 100% dark-rate here is an over-count — our voyages are TD3C-filtered, so a Ras Tanura loading destined for Europe correctly does not appear in our TD3C manifest but incorrectly flags as "dark" in this cross-reference. Documented in ADR 0006 as a known false-positive regime; true precision requires a global voyages cross-reference source, deferred to a later phase.
- Distance-to-anchorage distribution: min 2.3 km, median 8.3 km, max 10.0 km. With the default 10-km buffer we are operating near the ceiling; loosening to 15 km might add hits but also risks pulling in through-transit vessels. No action for v0.
- Length-of-dark-candidates distribution: min 206 m, median 282 m, max 350 m. Consistent with VLCC + Suezmax populations; the 200-m Suezmax-floor filter is doing the intended work.

**Implication**:
- Dark-fleet proxy is wired and producing non-zero output on real data. Phase 04's `dark_fleet_supply_adjustment` will consume the `has_matching_voyage == False` subset.
- The "100% dark" artefact is the single biggest caveat. Spire Maritime or global-voyages ingest (both in `.build/candidate_phases.md`) would collapse it materially. Backtests in phase 08+ should report tightness with-and-without the dark adjustment so we can attribute signal quality to this component.
- SAR header-only-file footgun documented in `load_sar_csv`; schema_overrides are mandatory even though most callers will never see a 0-row file.

**Refs**: ADR 0006; `feat/phase-03-sar-dark-fleet` branch.

---

## 2026-04-21 — GFW `/v3/vessels/{id}` identity coverage on TD3C vessels

**Context**: Phase 01 batch-classifies every GFW vessel_id that appears on a TD3C voyage parquet. Ran against the 2026-03 TD3C extract (99 unique vessel_ids) to size the classifier's reliance on each data source. See [ADR 0004](adrs/0004-vessel-classifier-heuristic.md).

**Finding** — identity response quality, live sample of 99 vessel_ids:

- `registryInfo` is an empty list for ~95% of vessels. When populated, it typically carries `tonnageGt` and `flag` but not `lengthM`.
- `registry.lengthM` was `null` for 99/99 vessels in the sample. We cannot rely on GFW registry length at all on this cohort; the 320 m length threshold effectively only fires via live-AIS cross-reference.
- `registry.tonnageGt` was populated for ~55% of the sample. Of those, values ranged 23k – 164k GT. The 150k-GT strict threshold fires for roughly 20% of the 99 (e.g. SHAGRA 163,922; ZARGA 163,922; DS VENTURE 157,039; NEW VANGUARD 157,039).
- `combinedSourcesInfo.shiptypes[].name` tokens in the wild: `OTHER`, `CARGO`, `OIL_TANKER`, `NA`. Always uppercase. Many real VLCC operators (e.g. DHT CHINA) come back as `OTHER`, so shiptype alone is insufficient.
- Q-Flex / Q-Max LNG carriers (ZARGA, AL KHATTIYA, SHAGRA — all Nakilat) tonnage-match the strict VLCC rule at ~136–163k GT. These are false-positives until AIS static cross-reference overrides them (AIS ship_type = 84 for liquefied gas tanker).
- 404 is genuine on `/vessels/{id}` (unlike `/v3/events` which 200s on unknown vessels). Only 1 of 99 vessel_ids 404'd in the sample.
- Throughput: ~3 req/sec without any concurrency, no rate-limit hits observed on 99 sequential calls.

**Classifier output on the sample (no AIS cross-ref available)**:

- Total: 99 vessels
- VLCC candidates: 58 / 99 (59%)
- By source: `gfw_identity` 65 (25 True, 40 False), `duration_heuristic` 33 (all True), `none` 1 (False)

**Implication**:
- The `duration_heuristic` fallback is load-bearing: without it only 25/99 would get a True classification on this cohort. Cannot be dropped.
- Classifier precision on the `gfw_identity` tier is polluted by LNG false-positives until AIS static is online. Backtests should report metrics split by `classification_source` and additionally sanity-check on the `gfw_identity` + AIS-verified subset once available.
- No pagination on `/vessels/{id}` — the endpoint returns one identity record, which simplifies the client (no offset math like `/v3/events`).

**Refs**: ADR 0004; `feat/phase-01-vessel-classifier` branch.

---

## 2026-04-21 — GFW /v3/events API: quirks from live probing

**Context**: Phase 1b needs a freshness layer between the monthly C4 voyages CSV releases (~30 day lag) and "today". Planned to wire up GFW's `/v3/events` REST endpoint for port-visits, gaps, encounters, and loitering. Before writing models, probed the live API with one known VLCC vessel_id to learn the real response shapes. See [ADR 0003](adrs/0003-events-api-for-freshness.md).

**Finding** — behaviors that only showed up on live probing, not from public docs:

- `confidences[0]` query param is valid **only** for the port-visits dataset. Passing it on gap / encounter / loitering → 422 with the error "Property confidences filter should not exists if the request does not contain a port_visit events dataset". Had to gate this on the caller side.
- Passing `limit` without `offset` → 422. The API requires both together. Conversely, querying offsets beyond `total` returns 200 with empty `entries` — no error.
- Unknown vessel_id does **not** 404; it returns 200 with empty `entries` and `total: 0`. Good for batch scans (no special-case error handling needed) but surprising — the `/vessels/{id}` endpoint does 404 for the same input.
- Several response fields arrive as strings despite being numeric: `port_visit.confidence: "4"` (not `4`), `port_visit.startAnchorage.distanceFromShoreKm: "9"`, `gap.distanceKm: "1054.83..."`, `gap.positions12HoursBeforeSat: "22"`, and inconsistently `gap.onPosition.lat` as string while `gap.offPosition.lat` in the same response is a float. Pydantic v2's lax mode coerces all of these automatically with `float`/`int` type hints — no custom validators needed.
- Timestamps on `/v3/events` are standard ISO 8601 with `Z`. Unlike the AISStream live feed (which uses Go's default time format and needed regex normalization in `taquantgeo_ais/models.py`), events parse directly into `datetime`.
- Pagination is `offset`/`limit` with `nextOffset` in the body; `nextOffset: null` signals the end. No cursor-style `since` token.
- Multiple `vessels[0..N]` in one request interleave their entries by time in the response — useful: we batch 50 vessel_ids per HTTP call.

**Implication**:
- Events client committed in `packages/ais/src/taquantgeo_ais/gfw/events.py` with these quirks encoded (see module docstring).
- VCR cassette tests record one real response per event kind so future schema drift fails in CI before prod.
- Rate-limit behavior wasn't tested against real 429s — used `httpx.MockTransport` for that path. If we hit production 429s that don't match our backoff model we'll update here.

**Refs**: ADR 0003; commits under `feat/gfw-events` branch.

---

## 2026-04-21 — GFW C4 voyages dataset: direct CSV downloads, covers all vessels incl tankers

**Context**: Initial Phase 1b plan assumed BigQuery research-tier access was the only way to get bulk historical AIS from GFW. That requires an application with multi-week review.

**Finding**: GFW publishes "All Vessels Voyages Confidence 4 (v3)" as **direct CSV downloads** via their portal. Monthly files, ~300 MB each, January 2017–present. No BigQuery gate. Covers every vessel type including oil tankers — not fishing-only. Schema: `ssvid, vessel_id, trip_id, trip_start, trip_end, trip_start_anchorage_id, trip_end_anchorage_id, *visit_id`. Joins with the similarly-downloadable Anchorages dataset via `s2id` to get lat/lon/iso3/label.

Also re-confirmed: C4 is the **highest**-confidence tier, not the lowest. GFW's own portal text ("c4 refers to confidence 4, as we have more noisy voyages with less confidence") is awkward phrasing of a correct point — they mean C4 is the clean bucket and lower levels are the noisy ones.

**Implication**:
- Phase 1b no longer blocked on BigQuery. Use CSVs as the historical backbone.
- Phase 2 shifts from "build voyage classifier" to "enrich GFW voyages with ship type and ton-miles."
- GFW BigQuery research tier and Spire academic tier still useful upgrades when approved.

**Refs**: [ADR 0002](adrs/0002-gfw-voyages-as-historical-source.md).

## 2026-04-21 — PG loading terminal coverage audit

**Context**: April 2026 partial month showed only 28 PG→CHN voyages, with major load terminals (Ras Tanura, Kharg, Ras Laffan) missing from the top origin labels. Concern: are those terminals absent from GFW anchorages?

**Finding**: They're all present. Matched 11 of 12 known major VLCC loading terminals within <15 km of a named GFW anchorage (Ras Tanura, Juaymah, Ras Laffan, Kharg, Basrah OT, Fujairah, Das Island, Jebel Ali, Jebel Dhanna, Mina Al-Ahmadi, Assaluyeh). March 2026 full-month extraction confirms: 99 TD3C voyages with Basrah OT, Ras Tanura, Fujairah dominating origins.

**Implication**: April's apparent absence was a traffic-mix quirk for that month, not a data quality issue.

**Refs**: `packages/ais/src/taquantgeo_ais/gfw/routes.py` (MAJOR_LOADING_TERMINALS).
