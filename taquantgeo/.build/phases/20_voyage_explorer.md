# Phase 20 — Voyage explorer

## Metadata
- Effort: `standard`
- Depends on phases: 17, 01
- Applies security-review: `no`
- Max phase runtime (minutes): 120
- External services: none

## Mission
The source-of-record view for every voyage in the system. Analysts and
on-call engineers need to drill from "this signal move is off" to "why,
exactly, which voyages contributed" without SSHing into a box. A
filterable, sortable, server-paginated table with a details drawer
answers this. "Show similar voyages" supports evidence-gathering ("is
this trip unusual?"). CSV export supports ad-hoc analysis in
spreadsheets.

## Orientation
- `.build/handoffs/01_handoff.md`, `17_handoff.md`, `18_handoff.md`
- `packages/ais/src/taquantgeo_ais/gfw/voyages.py` — schema
- `packages/ais/src/taquantgeo_ais/gfw/classifier.py` — vessel registry
  join
- `web/src/components/ui/DataTable.tsx` — reuse (phase 17 shared)

## Service preflight
None new.

## Acceptance criteria
- Backend:
  - `GET /api/voyages?filter=&sort=&page=&size=&format=json|csv` —
    server-side filter (date range, origin iso3, dest iso3, duration
    band, ship class), server-side sort (multi-column), paginated.
    `format=csv` streams CSV of the current filter, no pagination (but
    capped at 100k rows with warning header).
  - `GET /api/voyages/{trip_id}` — single-voyage detail: route arc
    GeoJSON, timeline of events, computed ton-miles, vessel registry
    enrichment
  - `GET /api/voyages/{trip_id}/similar?limit=5` — top-5 matches by
    origin+dest+duration. Uses a fixed similarity metric documented in
    a short ADR.
- Frontend `/voyages` route:
  - Sticky-header `DataTable`, 50 rows/page. Column filters, multi-
    column sort.
  - URL-encoded filter state so every view is shareable
  - CSV export button uses the `format=csv` endpoint
  - Row click → drawer: left half = Maplibre map with the voyage's
    great-circle OR sea-route arc highlighted (if we have cache) +
    anchorage polygons at origin/dest; right half = timeline (draft,
    SOG, COG, nav_status) + ton-miles stat + "Show similar voyages"
    which renders 5 rows link-pointing at those drawers.
  - Link-out to `https://globalfishingwatch.org/vessel/<vessel_id>` on
    vessel click (documented in the ADR — it's a public URL, but a
    change in GFW's frontend routes would silently break us)
- Tests:
  - Playwright: filter, sort, paginate, open drawer, similar, CSV
    download, URL persistence
  - Backend unit tests (≥5): filter correctness, sort stability,
    pagination edge (empty page), similar ordering, CSV format
- Similarity metric: Euclidean distance in the 3-feature space
  (origin_centroid_lat/lon z-score, dest_centroid_lat/lon z-score,
  duration_hours z-score). Documented and tested.

## File plan
- `packages/api/src/taquantgeo_api/routers/voyages.py`
- `packages/api/src/taquantgeo_api/similarity.py`
- `packages/api/tests/test_voyages_endpoints.py`
- `web/src/app/(research)/voyages/page.tsx`
- `web/src/app/(research)/voyages/VoyagesTable.tsx`,
  `VoyageDrawer.tsx`, `VoyageMap.tsx`, `SimilarVoyages.tsx`
- `web/src/lib/csv.ts` — streaming CSV download helper (tests for
  quoting / newlines / BOM)
- `web/tests/voyages.spec.ts`
- `docs/adrs/0019-voyage-similarity-metric.md` — NEW ADR: why
  Euclidean in 3-feature z-space for v0; future candidate (DTW on SOG
  series, or voyage-vector embeddings)
- `CLAUDE.md` — register route

## Non-goals
- Global vessel search (all vessels, not just ones with voyages) —
  candidate
- Historical animations (vessel route-through-time) — candidate
- Anchorage explorer — candidate
- Analyst notes / annotations — candidate

## Quality gates
- Format + lint + typecheck clean
- Playwright + backend tests pass
- CSV streaming verified against a 10k-row fixture (no buffering-all-
  in-memory on the server)
- Lighthouse Perf ≥ 90
- Pre-commit meta-review full loop
- ADR 0019 committed

## Git workflow
1. Branch `feat/phase-20-voyage-explorer`
2. Commits:
   - `feat(api): voyages endpoints + similarity + CSV stream`
   - `feat(web): voyage explorer table + drawer + map`
   - `feat(web): similar voyages panel`
   - `test: voyages playwright + API suites`
   - `docs: ADR 0019 voyage similarity`
3. PR, CI green, squash-merge

## Handoff
Table perf at 100k-row dataset (scroll + sort). CSV export file size
for a 1-month slice.
