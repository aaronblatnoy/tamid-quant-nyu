# Phase 19 — Signal dashboard

## Metadata
- Effort: `standard`
- Depends on phases: 17, 04
- Applies security-review: `no`
- Max phase runtime (minutes): 150
- External services: none

## Mission
The analyst's command center. Where the globe shows spatial state, the
signal dashboard shows the temporal signal: today's tightness, where it
sits in history, and how the equity basket has moved alongside it.
TradingView Lightweight Charts provides the familiar finance look; the
panels below explain what drove today's move. Deep-linkable so the
backtest viewer (phase 21) can jump straight to a specific window from
a chart-brush selection.

## Orientation
- `.build/handoffs/04_handoff.md`, `17_handoff.md`
- `packages/signals/src/taquantgeo_signals/tightness.py`
- `packages/signals/src/taquantgeo_signals/persistence.py` — read path
- `web/src/components/ui/Sparkline.tsx` — reuse
- `docs/adrs/0007-tightness-signal-definition.md` — what the numbers
  actually mean
- `docs/adrs/0001-stack-decisions.md` — TradingView Lightweight Charts
  choice

## Service preflight
None new.

## Acceptance criteria
- Backend:
  - `GET /api/signals/tightness?route=td3c&since=&until=` — rows from
    `signals` table
  - `GET /api/signals/decomposition?as_of=YYYY-MM-DD&route=td3c` — per-
    voyage contribution list for that date (which laden voyages
    contributed ton-miles, which ballast vessels counted as supply)
  - `GET /api/prices/basket?tickers=&since=&until=` — OHLCV rows
- Frontend `/signal` route:
  - Header: big 6xl tightness number (monospace, JetBrains Mono); delta
    vs 7/30/90-day prior; a muted 90-day sparkline beneath
  - 4 KPI cards in a row: forward_demand_ton_miles, forward_supply_count,
    ratio, z_score_90d — each with its own 30-day sparkline
  - Main chart: `lightweight-charts` with two series on independent
    axes (tightness on one, equity basket return on other). Crosshair,
    timeframe buttons (1M / 3M / 6M / 1Y / All), drawing tools. Brush-
    select on the chart URL-encodes `?from=…&to=…` — the backtest
    viewer (phase 21) reads it for deep-link slicing.
  - Below the chart: brushable mini-map timeframe selector
  - Right panel "Explain this move": date picker (syncs with chart
    crosshair). Shows the decomposition: top N contributing voyages
    that loaded/arrived that week.
- Tests:
  - Playwright: `test_signal_dashboard_loads`, `test_deeplink_brush_encodes_url`,
    `test_decomposition_panel_updates_on_date_change`,
    `test_timeframe_buttons_adjust_chart`
  - Backend unit tests for each new endpoint (≥3)
- Number formatting utility: all big numbers use thousands separators
  and appropriate units (e.g., 2.45B ton-miles). Unit rule locked into
  a small shared `format.ts` tested for edge cases (0, negative, NaN,
  Infinity from the signal code).

## File plan
- `packages/api/src/taquantgeo_api/routers/signals.py`
- `packages/api/src/taquantgeo_api/routers/prices.py`
- `packages/api/tests/test_signals_endpoints.py`
- `packages/api/tests/test_prices_endpoints.py`
- `web/src/app/(research)/signal/page.tsx`
- `web/src/app/(research)/signal/KpiRow.tsx`, `TightnessChart.tsx`,
  `DecompositionPanel.tsx`, `TimeframeSelector.tsx`
- `web/src/lib/format.ts` — new number formatter util + tests
- `web/tests/signal.spec.ts`
- `docs/RESEARCH_LOG.md` — append note on first live render

## Non-goals
- Comparing multiple routes side-by-side (only td3c route) —
  multi-route panel candidate entry
- Saving user-drawn annotations — candidate
- Exporting chart as PNG/CSV — candidate

## Quality gates
- Format + lint + typecheck clean
- Playwright ≥4 tests green
- Backend ≥6 new tests (split across signals + prices + format util)
- `format.ts` tests cover Infinity/NaN (the signal can return
  `float("inf")` per phase 04 — must render as something sensible,
  e.g., `"∞"` glyph, not "Infinity")
- Lighthouse Perf ≥ 90
- Pre-commit meta-review full loop
- Single-round review acceptable for `Effort: standard`

## Git workflow
1. Branch `feat/phase-19-signal-dashboard`
2. Commits:
   - `feat(api): signals + prices + decomposition endpoints`
   - `feat(web): signal dashboard page + charts + KPI row`
   - `feat(web): shared number formatter + Infinity handling`
   - `feat(web): deep-link brush → query string`
   - `test: playwright signal suite + backend endpoint tests`
3. PR, CI green, squash-merge

## Handoff
Screenshot paths. Lighthouse result. Which deep-link query shape was
chosen — phase 21 must read this, so it needs to be documented
verbatim.
