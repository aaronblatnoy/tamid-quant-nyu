# Phase 05 handoff

## Status
`completed`

## What shipped

- `packages/signals/tests/test_tightness_snapshot.py` — new, ~330
  lines. Six snapshot tests pinning `compute_daily_tightness` output
  against the frozen fixture set:
  - `test_snapshot_busy_loading_day_matches_frozen_ratio` (2020-03-15
    — 7 laden, supply=2, dark=0, ratio = 5,795,280,000, unclamped).
  - `test_snapshot_quiet_day_matches_frozen_ratio` (2020-03-01 — 1
    laden v101, supply=0 → floor clamps, ratio = 1,598,400,000).
  - `test_snapshot_dark_fleet_adjustment_lowers_supply` (2020-03-18 —
    7 laden, supply=2, dark=1 reduces effective to 1 WITHOUT clamping,
    ratio = 11,590,560,000). Distinct from the clamp-via-zero test.
  - `test_snapshot_ratio_infinity_when_effective_supply_zero`
    (2020-03-20 — 6 laden, supply=2, dark=2, raw=0 → clamp fires,
    ratio finite at 9,992,160,000).
  - `test_snapshot_zscore_none_when_history_insufficient` (2020-03-05
    — 10 prior ratios provided, below 30-sample threshold, z=None;
    also pins ballast-in-progress=1 with supply=0 because b201's ETA
    sits outside the 15-day horizon — the horizon-filter branch).
  - `test_snapshot_regression_for_march_2020_covid_window`
    (2020-03-22 — comprehensive pin of every public field plus 11
    `components` keys; the one test that would fail for the widest
    range of signal-math regressions).
  Every pinned number is derived in the module docstring so a reader
  can re-compute them by hand from the fixture rows.
- `packages/signals/tests/fixtures/snapshot/regenerate.py` — new
  (~260 lines). Hand-constructed 12-row voyages frame (10 laden `td3c`
  + 2 `td3c_ballast`), 12-row registry (11 VLCC candidates + 1
  non-VLCC), 4-pair distance cache, 3-row unmatched SAR dark-fleet
  table. Deterministic: re-running the script produces byte-identical
  parquets (md5 verified).
- `packages/signals/tests/fixtures/snapshot/{voyages,vessel_registry,
  distance_cache,dark_fleet}.parquet` — new, generated. Total weight
  ~15 KB. Committed under the extended `.gitignore` un-ignore pattern.
- `packages/signals/tests/fixtures/snapshot/README.md` — new. Per-row
  rationale, per-date expected-value table, and instructions for
  regenerating the fixtures after an intentional math change.
- `.gitignore` — extended the test-fixture un-ignore pattern from
  `!packages/**/tests/fixtures/*.parquet` to
  `!packages/**/tests/fixtures/**/*.parquet` so nested subdirectories
  (like `snapshot/`) are checked in alongside the flat-layout fixtures
  used by `packages/ais/tests/fixtures/`. Matches the un-ignore for
  top-level `tests/fixtures/` too.
- `docs/RESEARCH_LOG.md` — appended the phase-05 entry with the
  pinned per-date values and three boundary-case findings (7-day
  dark-window inclusivity, ballast ETA vs trip_end double-filter,
  strict `trip_end > as_of_EoD` comparison).

## PR

- URL: https://github.com/sn12-dev/taquantgeo/pull/10
- CI status at merge: **green** (label, lint-typecheck, test all
  passing on the first attempt)
- Merge sha: `2ba17ca`

## Surprises / findings

**The 7-day dark-fleet window semantics are subtler than the ADR
reads.** ADR 0007 specifies
`(as_of - 7d) ≤ detection_timestamp ≤ as_of` but the code computes
`window_start = _as_of_to_ts_utc(as_of) - timedelta(days=7)`
— i.e. it subtracts 7 days from *end-of-day UTC*, not from the start
of the calendar day. So for `as_of=2020-03-18`, the window is
`[2020-03-11 23:59:59.999999, 2020-03-18 23:59:59.999999]` UTC, and a
detection at `2020-03-11 12:00 UTC` is NOT in the window (it's before
`03-11 EoD`). That's well-defined but easy to mis-design a fixture
for. Designing the dark-fleet detection timestamps took two passes
before the per-date counts came out as intended; I positioned them at
10:00/12:00 UTC on specific days to avoid living near the EoD
boundary. Documenting here so the next engineer who adds a fixture
doesn't re-learn it.

**`trip_end > as_of_EoD` is a strict inequality at end-of-day
microsecond resolution.** v101's `trip_end` is `2020-03-20 00:00:00`
(tz-naive → localised as UTC). On `as_of=2020-03-20`, `as_of_ts =
2020-03-20 23:59:59.999999 UTC`. The filter asks `trip_end >
as_of_ts` — `03-20 00:00 < 03-20 23:59:59.999999`, so v101 is
EXCLUDED. That's the intended behaviour (it arrived on 2020-03-20, so
it's not in-progress AS OF end of 2020-03-20), but the boundary is
unobvious. The +/-1 voyage on this date is the difference between the
11.59 B and 9.99 B ton-miles demand tiers in the pinned tables. A
regression flipping the operator to `>=` would fail the `6 laden` pin
on 3/20 and 3/22 loudly.

**The ballast-supply filter is a *double* filter: in-progress AND
ETA within horizon.** The 2020-03-05 case is the only date in the
fixture where a ballast voyage is in-progress (b201 started 3/05,
ends 3/24, so it IS in-progress on 3/05) but its ETA (2020-03-23 23:23
UTC) exceeds the 15-day cutoff (2020-03-20 23:59:59.999999 UTC), so
`forward_supply_count = 0`. Distinct from 2020-03-01, where NO ballast
is in-progress. I originally assumed those two cases would behave
identically and was planning a single "zero-supply" test; the review
caught that they're different branches and I added the explicit pin
to `test_snapshot_zscore_none_when_history_insufficient` (which
happens to already run on 3/05).

**Test-fixture un-ignore needs `**` not `*` for subdirectories.** The
existing `.gitignore` un-ignore was
`!packages/**/tests/fixtures/*.parquet` — a single `*` glob, only
matching files directly under `fixtures/`. My first commit silently
excluded the four snapshot parquets (they matched the `*.parquet`
exclude but not the un-ignore). Had to reset the commit, extend the
pattern to `!packages/**/tests/fixtures/**/*.parquet`, and re-add.
Sibling `packages/ais/tests/fixtures/` has a flat layout so the bug
was latent until someone (me) added a subdirectory. `git check-ignore
-v` was the tool that surfaced the pattern mismatch.

**Polars cache-key determinism is fine across runs.** Concerned that
`pl.DataFrame.write_parquet` might vary bit-for-bit across
invocations (dictionary encoding ordering, internal scratch buffers,
non-deterministic metadata timestamps). MD5-comparing consecutive
runs of `regenerate.py` showed identical bytes. No action needed —
the script IS the source of truth.

**`pytest.approx(..., rel=1e-3)` is far too loose for integer math.**
Test-review round flagged that on values of order 10^10, `rel=1e-3`
permits ~10 million-unit drift, which would swallow many plausible
rounding-mode regressions. `compute_daily_tightness` returns
`round(total_ton_miles)` — an exact integer — so the regression pin
should be exact. Tightened every assertion to `abs=1` (or `abs=1e-6`
for the float `route_total_distance_nm` median). The snapshots still
pass, but any one-unit drift now fails loudly.

**Round-1 review caught three real quality issues.** Round-1
meta-review surfaced (a) the loose-tolerance issue above, (b) the
2020-03-05 supply-horizon branch not being distinctly pinned from
"no ballast in progress", and (c) `regenerate.py`'s module docstring
claiming the registry had 10 rows when it actually writes 12 (the
README had the correct count; the docstring was stale from an earlier
design iteration). All three were fixed via `cursor_apply` before
commit. The tolerance issue would have let a rounding-mode change
merge silently — exactly the class of regression the snapshot tests
are supposed to catch — so this was a load-bearing fix.

**`detection_timestamp` on rows where every `mmsi` is None needs an
explicit Int64 schema.** Polars would otherwise infer `Null` dtype
and break downstream `mmsi.is_not_null()` filters. The fixture pins
`schema={"mmsi": pl.Int64, ...}` explicitly; same footgun the phase
03 work already documented for the SAR CSV loader, but it re-surfaced
here in a different context.

## Test count delta

- Before: 161 (per phase 04 handoff; build_state.json's baseline of
  53 is stale — the driver hasn't updated it since phase 00)
- After: 167 (+6)
- New tests:
  - `test_snapshot_busy_loading_day_matches_frozen_ratio`
  - `test_snapshot_quiet_day_matches_frozen_ratio`
  - `test_snapshot_dark_fleet_adjustment_lowers_supply`
  - `test_snapshot_ratio_infinity_when_effective_supply_zero`
  - `test_snapshot_zscore_none_when_history_insufficient`
  - `test_snapshot_regression_for_march_2020_covid_window`
- Tests removed: none.

Phase contract required ≥5 new tests. Delivered 6 (phase spec
required exactly 6 named tests; we hit the contract). Driver should
update `build_state.json.test_count_baseline` from 161 → 167.

## Optional services not configured

None. This phase is pure-fixture, pure-function; no env vars required,
no network calls, no DB writes.

## Deferred / open questions

- **Populated-dwt snapshot test.** Round-1 review flagged that no
  snapshot exercises the `registry.dwt` populated path — every voyage
  hits the 270,000 nominal. Phase 04's unit tests cover this
  (`test_dwt_present_uses_per_vessel_dwt`,
  `test_dwt_zero_is_treated_as_fallback`), so the snapshot coverage
  gap is not an uncovered regression — it's a missing end-to-end pin.
  Adding a dwt-populated voyage would require touching the fixture
  (breaking existing pins) and is deferred until phase 10's
  registry-enrichment work lands.
- **Populated z-score snapshot test.** Same story — phase 04's
  `test_z_score_90d_uses_rolling_history` pins the rolling math
  against a 60-sample window; a snapshot test with ≥30 samples
  producing a pinned float would close the gap at the snapshot layer
  too. Out of scope for this phase ("zscore_none" was the spec'd
  test). Candidate for a future test-hygiene phase.
- **Great-circle fallback snapshot test.** Every fixture voyage hits
  the distance cache → `great_circle_fallbacks == 0` on every date.
  A bug that silently re-routed every voyage to great-circle would
  shift ton-miles by ~1% (great-circle ~5856 NM vs cached 5920 NM
  for RT→NB) which is within the now-tight `abs=1` pin for the
  largest demand value (11.59 B). A synthetic voyage with an
  anchorage pair deliberately missing from the cache would close this
  gap; not implemented because it requires extending the fixture
  (more rows = more to audit when a regression fires). Candidate for
  a follow-up test-coverage phase.
- **`trip_end == as_of_EoD` boundary.** No fixture pin for the edge
  case `trip_end = 2020-03-15 23:59:59.999999 UTC` (right at the
  boundary). The strict `>` side is pinned (v101 excluded on 3/20);
  the `>=` side is not. A subtle operator flip at the second boundary
  (null-end case) would go undetected. Not a priority — Polars
  comparisons on microsecond-precision datetimes are well-defined
  and the two pinned dates (3/15, 3/18) exercise the far-from-boundary
  case.
- **Fixture layout divergence from siblings.** `packages/ais/tests/
  fixtures/` is flat (`sar_sample.csv`, `sar_voyages_sample.parquet`,
  …); `packages/signals/tests/fixtures/snapshot/` is nested. Chose
  nested because the snapshot set comes with a README and a
  regeneration script that belong inside the same directory. The
  `.gitignore` un-ignore was updated to cover both layouts. Noted in
  style review; judged acceptable given the four-file grouping.
- **Multi-route snapshots.** `route=td3c` only today; a future
  multi-route signal would need parallel fixture sets or a
  parametrised test. Deliberately scoped out per phase non-goals.

## Ideas for future phases

Nothing appended to `.build/candidate_phases.md` this run. The deferred
items above are test-hygiene refinements rather than new product work;
they belong in a future test-coverage sweep (implicit candidate) but
are not blockers for the v0 trading-signal thesis.

Two informal ideas surfaced during review but not promoted:

- **Parametrise the snapshot tests.** Several of the six tests share
  the same `_run(snapshot_fixtures, <date>)` shape with a handful of
  assertions per date. A parametrised table-driven test
  (`@pytest.mark.parametrize`) would be more compact. Rejected: the
  per-test docstrings document the specific regime and failure-mode
  that each date exercises, which is load-bearing context a table
  obscures. The distinct-test-per-scenario shape also produces more
  legible failure output ("quiet day regressed" vs "row 5 of params
  regressed").
- **Snapshot over a full month's history.** The current set spans six
  dates in March 2020. A future snapshot layer could compute ratios
  for every day in a month, save them to an on-disk parquet, and fail
  if a re-run diff-differs. Similar to a golden-output test. Rejected
  for v0: six hand-picked dates + explicit component-level pins give
  better diagnostic power per test-case than 30 opaque numbers. But
  worth revisiting once we have a few months of real history to
  backtest against.

## For the next phase

- **Canonical fixture paths** (stable after this phase): everything
  under `packages/signals/tests/fixtures/snapshot/`. Files committed;
  `regenerate.py` is the regeneration command; `README.md` documents
  per-row rationale and expected values.
- **Pinned expected values are load-bearing — do NOT change them
  without an ADR.** The module docstring in
  `test_tightness_snapshot.py` derives every number from the fixture
  rows plus the signal math; any PR that changes these pins must
  either (a) be accompanied by an ADR amendment explaining the math
  change, or (b) explain in the PR why the fixture itself changed.
- **`abs=1` tolerance is intentional.** If a future change introduces
  legitimate FP sum-of-products noise that drifts by > 1 unit, the
  tests will fail. The correct response is usually to tighten the
  math (e.g. sum-of-integers before rounding), not to loosen the
  tolerance. If tolerance loosening really is the right call, do it
  in-line with a comment explaining why.
- **`.gitignore` un-ignore covers nested subdirectories now.** Future
  packages can commit parquet fixtures under
  `packages/<pkg>/tests/fixtures/<anything>/*.parquet` without
  further gitignore changes.
- **The dark-fleet window is inclusive on both ends at end-of-day
  UTC.** Write out this spec explicitly in any future ADR that
  touches the window semantics; the pinned 2020-03-18 count depends
  on it.
- **Ballast-supply double-filter semantics matter.** An in-progress
  ballast whose ETA exceeds the horizon does NOT contribute to
  `forward_supply_count` but DOES contribute to
  `components["ballast_in_progress"]`. Phase 07's IC code should
  exclude rows where `ballast_in_progress > 0 and
  forward_supply_count == 0` from the "data is fresh" cohort —
  that's the boundary case where supply is "about to matter" but not
  yet counted.
- **Phase 05 intentionally did NOT test populated-dwt or populated
  z-score at the snapshot layer.** Phase 04 unit tests cover those
  equations. If a future ADR changes either, extend the fixture
  deliberately rather than discovering the gap post-merge.
