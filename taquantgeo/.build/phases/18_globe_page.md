# Phase 18 — Globe page

## Metadata
- Effort: `max`
- Depends on phases: 17
- Applies security-review: `no`
- Max phase runtime (minutes): 180
- External services: none (uses our own FastAPI backend and local
  parquet / Postgres — backend routes added in this phase)

## Mission
The hero feature. A full-viewport WebGL globe rendered with DeckGL on
Maplibre showing every vessel we're tracking in real time, their active
voyages as animated arcs, and demand density as a toggled heatmap. The
signal is spatial — being able to SEE a wave of laden VLCCs departing
Basrah is more compelling than any chart. Time-scrub rewinds the globe.
Vessel click drills into a voyage-history drawer. Performance target:
60fps with 1,000 vessels. This is the page that sells the product.

## Orientation
- `.build/handoffs/17_handoff.md`
- `packages/api/src/taquantgeo_api/` — FastAPI scaffold (may need
  bootstrapping if not yet scaffolded; check first)
- `packages/ais/` — live position source
- `packages/signals/` — signal overlay
- `docs/adrs/0001-stack-decisions.md` — Maplibre choice

## Service preflight
- If `packages/api/` has no app yet, bootstrap a minimal FastAPI in this
  phase. Document as a side-effect in the handoff.
- `DATABASE_URL` required (read vessels + voyages).

## Acceptance criteria
- Backend:
  - `packages/api/src/taquantgeo_api/routers/vessels.py` exposes:
    - `GET /api/vessels/positions?since=&until=&bbox=` — latest
      position per vessel in the window, compact JSON
    - `GET /api/vessels/{vessel_id}/voyages` — voyage history
    - `GET /api/vessels/{vessel_id}/events?type=` — recent events
      (from phase 1b Events API parquet)
  - Responses use cursor pagination; time bucketing server-side to cap
    payload at ≤500kb per request
  - Typed Pydantic response models documented in OpenAPI
- Frontend:
  - `/globe` route. Full viewport.
  - DeckGL + Maplibre tiles (OSS tile server — pin the tile URL in an
    env var; default to a free OSM-based tile provider). No Mapbox key
    required.
  - ScatterplotLayer for vessels, colored by state (orange = laden,
    blue = ballast, grey = unknown). Smooth interpolated positions
    between samples. Rotation from heading. Size scales with vessel
    length.
  - ArcLayer for active voyages (origin anchorage → current position →
    destination anchorage).
  - HeatmapLayer aggregating forward-demand per H3 cell (toggleable).
  - Left rail (collapsible): filters for vessel class, route, date
    range. Filter changes trigger instant layer re-render (no round-
    trip if data is already client-side).
  - Right rail: timeline scrubber — drag rewinds the globe to that
    moment. Keyboard arrows scrub by 1 day.
  - Vessel click → right drawer (`Sheet` component) with voyage history
    timeline (stacked bar laden/ballast legs), recent events, raw AIS
    stats (current speed, heading, draft).
  - Keyboard shortcuts: `F` fit-to-bounds of visible vessels, `L`
    laden-only filter toggle, `/` focus search.
  - Debounced search by vessel name / MMSI in the top bar.
  - Performance target: 60fps with 1,000 concurrent vessels (measured
    via Chrome DevTools Performance tab in the phase — handoff
    includes trace summary).
- Tests:
  - Playwright: `test_globe_page_renders`, `test_vessel_click_opens_drawer`,
    `test_timeline_scrub_rewinds_state`, `test_filter_state_persists_in_url`,
    `test_heatmap_toggle`, `test_fit_bounds_shortcut`
  - Backend unit tests for `/api/vessels/*` endpoints (≥4 tests:
    positions window, voyages history, events filter, bbox filter)
  - Lighthouse budget: Perf ≥ 85 (lower than scaffold because of heavy
    WebGL canvas; document the tradeoff)
- All quality gates green.

## File plan
- `packages/api/` — scaffold if absent (`pyproject.toml`,
  `src/taquantgeo_api/main.py` with FastAPI app, `routers/`, uvicorn
  entry)
- `packages/api/src/taquantgeo_api/routers/vessels.py` — new
- `packages/api/tests/test_vessels_endpoints.py` — new
- `web/src/app/(live)/globe/page.tsx`
- `web/src/app/(live)/globe/GlobeScene.tsx` — DeckGL layers
- `web/src/app/(live)/globe/FilterRail.tsx`, `TimelineScrubber.tsx`,
  `VesselDrawer.tsx`
- `web/src/lib/h3.ts` — H3 helpers (client-side aggregation for
  heatmap)
- `web/src/lib/maplibre.ts` — Maplibre init wrapper
- `web/tests/globe.spec.ts` — Playwright suite
- `docs/adrs/0018-globe-stack.md` — NEW ADR: DeckGL over Leaflet, OSS
  Maplibre tiles, H3 over regular grid for heatmap, perf target
- `CLAUDE.md` — register /globe route in the web section

## Non-goals
- Historical playback beyond last 30 days (candidate for a "time
  machine" page)
- Multi-route overlays (other than TD3C) — candidate
- Vessel ownership / sanctions metadata — out of scope for v0
- Mobile-optimized view — candidate (low priority; the dashboard is
  desktop-first)

## Quality gates
- Format + lint + typecheck clean (backend + frontend)
- Playwright tests green
- Backend unit tests ≥4
- Lighthouse Perf ≥ 85 recorded
- 60fps @ 1,000 vessels verified with trace (record metric in handoff)
- Pre-commit meta-review full loop
- Three-round review loop per `Effort: max`
- ADR 0018 committed

## Git workflow
1. Branch `feat/phase-18-globe`
2. Commits:
   - `feat(api): scaffold FastAPI app (if not present)`
   - `feat(api): /api/vessels/* routers`
   - `feat(web): globe page with DeckGL + Maplibre`
   - `feat(web): filter rail + timeline scrubber + vessel drawer`
   - `feat(web): keyboard shortcuts + search`
   - `test(web): Playwright globe suite`
   - `test(api): vessels endpoints`
   - `docs: ADR 0018 globe stack`
3. PR, CI green, squash-merge

## Handoff
Screenshot paths from Playwright. Lighthouse + Chrome DevTools perf
trace summary. Any DeckGL + Next.js hydration quirks (DeckGL is
WebGL-only; SSR gotchas).
