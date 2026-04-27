# Phase 01 handoff

## Status
`completed`

## What shipped

- `packages/ais/src/taquantgeo_ais/gfw/classifier.py` — new module.
  Module docstring documents the 8-rule cascade + observed API quirks.
  `AisStaticRef` dataclass, `VLCC_HEURISTIC` summary constant, pure
  `classify_one(identity, ais_static, *, from_td3c_route)`, orchestrator
  `classify_vessels(vessel_ids, client, *, out_path, ais_lookup,
  from_td3c_route, force)`, `load_ais_static_lookup(mmsis)` (fail-open
  Postgres read), `read_vessel_ids_from_voyages(dir)` (lazy-scan with
  empty-tree guard).
- `packages/ais/src/taquantgeo_ais/gfw/api.py` — minor extension:
  `GfwClient.__init__` gained an optional `transport` kwarg matching
  `EventsClient`, so tests no longer reach into `_client` privately.
  Callers that didn't pass `transport` are unaffected.
- `packages/cli/src/taquantgeo_cli/gfw.py` — new `classify-vessels`
  Typer command. Flags: `--voyages-dir`, `--out`, `--force`,
  `--no-ais-cross-ref`. Prints row count, VLCC-candidate count, and
  classification-source histogram on completion.
- `packages/ais/tests/test_classifier.py` — 15 new tests. Unit coverage
  for all 8 cascade rules, orchestrator behaviors (AIS cross-ref
  populates `ais_ship_type`; 404 → `classification_source="none"`;
  resume-from-cache; `--force` re-fetches; output column order matches
  `_REGISTRY_COLUMN_ORDER`; parquet dtypes match `_REGISTRY_SCHEMA`;
  `VLCC_HEURISTIC` descriptiveness). One VCR cassette round-trip
  against a real DS VENTURE identity payload.
- `packages/ais/tests/cassettes/classifier/test_cassette_identity_roundtrip_real_vessel.yaml`
  — scrubbed cassette.
- `docs/adrs/0004-vessel-classifier-heuristic.md` — new ADR with full
  threshold + cascade rationale, ITU AIS tanker-code widening
  (80-84), and alternatives-considered.
- `docs/RESEARCH_LOG.md` — new top-of-file entry: GFW
  `/v3/vessels/{id}` coverage observations on the 99-vessel TD3C
  sample.
- `CLAUDE.md` — one-line addition to the Useful commands block.
- `data/processed/vessel_registry.parquet` — generated artifact (99
  rows, gitignored per `data/processed/*`).

## PR

- URL: https://github.com/sn12-dev/taquantgeo/pull/6
- CI status at merge: green (label, lint-typecheck, test — all pass)
- Merge sha: `78457a9`

## Surprises / findings

API-shape observations from the live 99-vessel probe (full detail in
`docs/RESEARCH_LOG.md`):

- `registry.lengthM` was `null` for 100% of the sampled TD3C vessels.
  The 320 m length threshold in GFW identity is effectively dead on
  this cohort — the only way length fires a rule is via live-AIS
  ShipStaticData. Tonnage was populated ~55%.
- `combinedSourcesInfo.shiptypes[].name` tokens are always UPPERCASE
  (`OTHER`, `CARGO`, `OIL_TANKER`, `NA`). Many legitimate VLCC
  operators (DHT CHINA observed) come back as just `OTHER` — so the
  `duration_heuristic` fallback is load-bearing (33/99 vessels in the
  sample relied on it). Not droppable.
- 404 is genuine on `/vessels/{id}` (unlike `/v3/events` which 200s
  with empty `entries`). Only 1/99 vessel_ids in the sample 404'd.
- Q-Flex / Q-Max LNG carriers (ZARGA, AL KHATTIYA, SHAGRA — Nakilat
  fleet) tonnage-match the 150k GT strict VLCC rule at ~136-163k GT.
  Documented in ADR 0004 as a known false-positive that AIS static
  cross-reference (ship_type=84 for LNG) is meant to suppress — **but**
  the round-1 widening of the AIS tanker range to ITU 80-84 inclusive
  means AIS no longer contradicts for ship_type=84. Net effect: LNG
  Q-Max at ≥150k GT and ≥320m will classify as VLCC until a future
  phase adds an explicit LNG suppression (IMO registry cross-ref or
  AIS ship_type != 84 exclusion).
- Meta-review round 1 caught a real bug in the initially recorded
  cassette: the vessel_id I had used was fabricated and 404'd on GFW,
  which made the "cassette round-trips a real VLCC" test docstring
  false (it was actually exercising the 404 path). Replaced with the
  verified DS VENTURE vessel_id `2936a72a4-4a89-483e-1c6c-c97233154c78`
  and re-recorded the cassette. Lesson logged: when writing cassette
  tests against new endpoints, always verify the recorded status code
  matches the docstring's claim before shipping.
- GFW vessel_ids are **not** strict UUIDs — their first group can be
  9 hex chars, not 8 (e.g. `2936a72a4-...`). Treat them as opaque
  strings; don't regex-validate as UUIDs.

Classifier output on the 99-vessel 2026-03 TD3C extract (no AIS
cross-ref — Postgres `vessels` table empty in local env):

| classification_source | n | VLCC | not VLCC |
|---|---|---|---|
| `gfw_identity` | 65 | 25 | 40 |
| `duration_heuristic` | 33 | 33 | — |
| `none` | 1 | — | 1 |
| **total** | **99** | **58** | **41** |

## Test count delta

- Before: 53
- After: 68 (delta +15)
- New tests (by name):
  - `test_classifier_vlcc_by_tonnage`
  - `test_classifier_vlcc_by_length`
  - `test_classifier_vlcc_by_shiptype_token`
  - `test_classifier_not_vlcc_product_tanker`
  - `test_classifier_cross_ref_ais_static_contradicts`
  - `test_classifier_cross_ref_ais_static_positive`
  - `test_classifier_duration_heuristic_fallback`
  - `test_classifier_cargo_token_rules_out`
  - `test_classify_vessels_cross_refs_ais_ship_type`
  - `test_classify_vessels_unknown_vessel_id_yields_none_source`
  - `test_classify_vessels_resume_from_cache`
  - `test_classify_vessels_force_refetches`
  - `test_classify_vessels_output_schema_and_column_order`
  - `test_vlcc_heuristic_constant_is_descriptive`
  - `test_cassette_identity_roundtrip_real_vessel`
- Tests removed: none.

The phase contract asked for ≥7 new tests; +15 delivered. Driver should
update `build_state.json["test_count_baseline"]` from 53 → 68.

## Optional services not configured

- **Postgres (`DATABASE_URL`)** — the `vessels` table is the source of
  AIS static cross-reference rule (rule 1: AIS contradicts; rule 4:
  AIS positive). Local dev Postgres was not populated during this
  phase, so the CLI was exercised with `--no-ais-cross-ref`.
  `load_ais_static_lookup` degrades cleanly to `{}` on any DB failure,
  so the classifier runs without AIS cross-ref and just logs a warning.
  When Postgres becomes available and `DATABASE_URL` is set, rule 1
  and rule 4 will automatically activate on subsequent runs without
  any code change.
- No other `optional`-tier services touched.

## Deferred / open questions

- **LNG-carrier suppression.** Q-Flex / Q-Max LNG carriers
  tonnage-match the 150k-GT strict threshold and — after the round-1
  AIS range widening — will also pass AIS rule 4 when they broadcast
  ship_type=84. ADR 0004's Negative-consequences section documents
  this, but no mitigation code. Backtests should at minimum report
  results split by `classification_source` and consider an
  IMO-registry cross-reference or an `ais_ship_type != 84` carve-out.
  Not in phase scope; flag for a future candidate if the
  `ais_static` tier ever shows LNG dominance.
- **Thresholds are judgment calls.** 150k GT / 320 m / 100k GT / 280 m /
  50k GT are rooted in observed VLCC/Suezmax/MR distributions, not
  empirically optimized against a labeled validation set. Reasonable
  candidate for a future tuning phase once we have Baltic rate data to
  measure downstream signal quality.
- **Cascade precedence edge case.** Rule 4 (AIS positive with ship_type
  80-84 and length ≥ 320) can fire before rule 5 (GFW GT < 50k
  negative) even if GFW tonnage says small-tanker and AIS says big-
  tanker. Round-2 review flagged this as a minor defensible-ambiguity;
  trusting AIS dimensions over GFW tonnage is the intended behavior
  but was left undocumented beyond the module docstring's rule
  ordering. Worth a note in ADR 0004 if it ever bites.

## Ideas for future phases

Appended to `candidate_phases.md`: none.

The LNG-suppression and threshold-tuning items above are worth tracking
somewhere, but neither is concrete enough to warrant a candidate entry
yet — both depend on observations from later phases (backtest results,
live-AIS coverage). Raising them here as deferred questions instead.

Two smaller improvements were raised but rejected during review as pure
style:

- Rename CLI option `--out` → `--out-path` to disambiguate against
  sibling commands' `--out-dir`. Rejected: `out` is short and clear for
  a file path; renaming would be churn.
- Narrow `except Exception` in `load_ais_static_lookup` to
  `sqlalchemy.exc.SQLAlchemyError`. Rejected: the broad catch is
  intentional for graceful degradation across arbitrary DB driver
  failures (psycopg `OperationalError`, DNS failures, etc.).

## For the next phase

- **Registry path.** `data/processed/vessel_registry.parquet` is the
  canonical output. Schema is locked at `_REGISTRY_COLUMN_ORDER` in
  `packages/ais/src/taquantgeo_ais/gfw/classifier.py`. Downstream
  phases should join voyages ↔ registry on `vessel_id` (primary) or
  `mmsi` (fallback; nullable in registry on 404 rows).
- **Filter guidance.** For "high-precision VLCC only" downstream logic,
  filter on `is_vlcc_candidate == True AND classification_source ==
  "gfw_identity"` (25/99 of the sample). For "broadest VLCC coverage"
  use `is_vlcc_candidate == True` (58/99 in the sample). Backtest
  reports should ideally compute both and compare.
- **AIS cross-ref becomes stronger with time.** As the live-AIS
  streamer accumulates ShipStaticData, the `ais_ship_type` column
  fills in and rule 1 / rule 4 activate on more vessels. Re-running
  `taq gfw classify-vessels` with `--force` refreshes the registry.
  Without `--force`, existing rows are preserved — AIS cross-ref only
  applies to newly-fetched rows.
- **Idempotency contract.** The classifier writes the full merged
  registry (cached + newly-fetched) to `out_path` on every run. Never
  partial — safe to re-run; safe to kill mid-run (the next run
  restarts from the existing cache, only re-fetching genuinely new
  vessel_ids).
- **`GfwClient` now accepts `transport=`.** Future tests should use
  the public constructor rather than patching `_client`.
