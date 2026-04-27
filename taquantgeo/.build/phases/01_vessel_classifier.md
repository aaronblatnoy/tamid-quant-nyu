# Phase 01 — Vessel class batch classifier

## Metadata
- Effort: `max`
- Depends on phases: 00
- Applies security-review: `no`
- Max phase runtime (minutes): 180
- External services:
  - `GFW_API_TOKEN (required)` — already set in environment. If unset at
    start, append manual-setup entry (where: https://globalfishingwatch.org/
    our-apis/, how: request free API key; ~1 day for approval) and block.

## Mission
Phase 1b's remaining "who IS this vessel?" question. TD3C voyages CSVs
give us `ssvid` (MMSI) and `vessel_id` (GFW internal UUID) per voyage, but
no ship type, dwt, or length — so we can't tell a VLCC voyage from a
Suezmax or product-tanker voyage without enrichment. This phase batches
GFW REST `/v3/vessels/{id}` for every vessel that appears in our
TD3C-filtered parquet, writes a cached vessel registry, cross-references
with our live-AIS `vessels` table where MMSI matches, and classifies each
vessel as VLCC-candidate using a published heuristic (tonnage OR length OR
tanker-type). The registry is what every downstream phase joins against
when it needs to ask "is this vessel a VLCC?".

## Orientation (read before writing)
- `.build/handoffs/00_handoff.md` — confirm baseline
- `packages/ais/src/taquantgeo_ais/gfw/api.py` — existing `GfwClient` +
  `VesselIdentity` dataclass. Do not rewrite. Extend.
- `packages/ais/src/taquantgeo_ais/gfw/events.py` — quality bar: module
  docstring documenting discovered quirks, defensive pagination, typed
  models, `extra="ignore"` on Pydantic configs, VCR cassettes in tests.
- `packages/ais/tests/test_gfw_events.py` — pytest-recording cassette
  pattern. Replicate it.
- `packages/core/src/taquantgeo_core/schemas.py` — `Vessel` ORM. Cross-ref
  target.
- `docs/adrs/0002-gfw-voyages-as-historical-source.md` — Gap 1 explicitly
  calls for this work.
- `docs/data_model.md` — `vessels` table fields

## Service preflight
- `GFW_API_TOKEN` required. If unset: manual-setup entry + block.
- All others not used here.

## Acceptance criteria
- File exists: `packages/ais/src/taquantgeo_ais/gfw/classifier.py`
  exporting `classify_vessels(vessel_ids, client, *, out_path) ->
  polars.DataFrame` and `VLCC_HEURISTIC` documentation constant.
- File exists: `data/processed/vessel_registry.parquet` after running
  `uv run taq gfw classify-vessels --voyages-dir data/processed/voyages
  --out data/processed/vessel_registry.parquet` against the test fixture.
- Parquet schema (columns, in order): `mmsi` (int64 nullable),
  `vessel_id` (str), `imo` (str nullable), `name` (str nullable),
  `flag` (str nullable), `gfw_shiptypes` (list[str]),
  `gross_tonnage` (float64 nullable),
  `registered_length_m` (float64 nullable),
  `is_vlcc_candidate` (bool), `classification_source` (str — one of
  `gfw_identity`, `ais_static`, `duration_heuristic`, `none`),
  `ais_ship_type` (int nullable — join from live-AIS `vessels` table when
  MMSI matches), `fetched_at` (timestamp[us, UTC]).
- CLI command `taq gfw classify-vessels --help` exits 0.
- Idempotent: running the command twice with the same inputs does not
  re-hit GFW for vessel_ids already in the cache unless `--force` passed.
- Tests (pytest-recording cassettes; `@pytest.mark.live` for any that
  exercise real HTTP; default run uses cassettes):
  - `test_classifier_vlcc_by_tonnage` — gross_tonnage ≥ 150000 → True
  - `test_classifier_vlcc_by_length` — length ≥ 320m → True
  - `test_classifier_vlcc_by_shiptype_token` — "oil_tanker" in
    gfw_shiptypes AND (tonnage or length near threshold) → True
  - `test_classifier_not_vlcc_product_tanker` — small tanker → False
  - `test_classifier_cross_ref_ais_static` — MMSI present in live-AIS
    `vessels` table → `ais_ship_type` populated
  - `test_classifier_unknown_vessel_id` — GFW returns 404 → row written
    with `classification_source="none"`, `is_vlcc_candidate=False`
  - `test_classifier_resume_from_cache` — second run with existing parquet
    skips previously-seen vessel_ids
- All quality gates green (format, lint, typecheck, tests).

## File plan
- `packages/ais/src/taquantgeo_ais/gfw/classifier.py` — new. Module
  docstring documenting heuristic + sources. Pure functions + a
  `classify_vessels` orchestrator. Consumes `GfwClient` from `api.py`.
- `packages/ais/src/taquantgeo_ais/gfw/api.py` — minor extension only if
  needed (e.g., a batched wrapper around `iter_vessel_identities`).
  Preserve existing signatures; do NOT break callers.
- `packages/cli/src/taquantgeo_cli/gfw.py` — add `classify_vessels`
  command. Typer-Annotated pattern matching `ingest_voyages`.
- `packages/ais/tests/test_classifier.py` — new. VCR cassettes under
  `packages/ais/tests/cassettes/classifier/`.
- `packages/ais/tests/conftest.py` — extend only if a fixture needs
  sharing with existing tests; otherwise add locally.
- `docs/adrs/0004-vessel-classifier-heuristic.md` — NEW ADR: document
  why the tonnage-OR-length-OR-shiptype heuristic (vs strict AND),
  threshold choices (150k GT, 320m), and the fallback cascade (GFW →
  AIS static → duration).
- `docs/RESEARCH_LOG.md` — append entry with any API quirks discovered
  (e.g., tonnageGt inconsistency, shiptype naming).
- `CLAUDE.md` — update "Useful commands" section with the new CLI

## Non-goals
- Dark-fleet detection — that's phase 03.
- Sea-route distance enrichment — phase 02.
- Ton-mile computation per voyage — phase 04 uses the registry to compute
  this.
- Populating the live-AIS `vessels` table from GFW data — the live
  streamer owns that path; we only READ from it.
- Persisting to Postgres — vessel registry is a parquet cache, not an
  operational table. (If future phases need it in Postgres, a candidate
  entry is appropriate.)

## Quality gates
- Format + lint + typecheck clean
- Full unit test suite green; cassette tests green without network
- Test count increases by at least 7 (listed above)
- Pre-commit meta-review: scope gate met (multi-file). Run code-review +
  style-review + test-review subagents. No security-review (no money,
  no auth).
- Docs: new ADR 0004 written; RESEARCH_LOG appended if quirks found;
  CLAUDE.md updated with new CLI
- When `Effort: max` — iterate review loop to 3 rounds even on minor-only
  round 1

## Git workflow
1. `git checkout main && git pull`
2. `git checkout -b feat/phase-01-vessel-classifier`
3. Commits (atomic):
   - `feat(gfw): batch vessel classifier + registry parquet`
   - `test(gfw): classifier cassette coverage`
   - `docs: ADR 0004 vessel-classifier heuristic; research log notes`
4. `gh pr create --title "feat(gfw): batch vessel classifier"` with body
   explaining the heuristic + threshold rationale + links to ADR
5. `gh pr checks --watch`; on green, `gh pr merge --squash --delete-branch`
6. Handoff written before exit

## Handoff
Confirm registry parquet row count, distribution of
`classification_source`, fraction of vessels flagged VLCC-candidate,
any API quirks, and new tests. Record all of these in
`.build/handoffs/01_handoff.md`.
