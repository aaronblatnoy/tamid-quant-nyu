# Phase 21 — Backtest viewer

## Metadata
- Effort: `standard`
- Depends on phases: 17, 09
- Applies security-review: `no`
- Max phase runtime (minutes): 150
- External services: none

## Mission
The evidence artifact page. Phase 09 wrote `reports/backtest_v1.md`;
phase 21 renders the same data as a polished, interactive web page that
can be shared with collaborators / investors / the user's future self
when deciding whether to deploy real capital. Regime shading is the
signature visual — "this strategy performed in COVID, in Russia, in Red
Sea". Compare-two-backtests supports A/B evaluation of config tweaks.
Export-as-PDF-ready-HTML supports offline review.

## Orientation
- `.build/handoffs/09_handoff.md`, `17_handoff.md`, `19_handoff.md`
- `packages/backtest/src/taquantgeo_backtest/{walkforward,regimes,report}.py`
- `reports/backtest_v1.md` — the reference content
- `docs/adrs/0011-walkforward-cv.md`

## Service preflight
None.

## Acceptance criteria
- Backend:
  - `GET /api/backtests` — list of stored backtest runs (metadata)
  - `GET /api/backtests/{run_id}` — stats, trades, equity curve, config
  - `GET /api/backtests/{run_id}/vs/{run_id_b}` — side-by-side diff
  - Backend reads from `backtest_results/<run_id>/` parquets written
    by phase 08/09 (shared convention)
- Frontend `/backtests` route:
  - List of runs (table) with CAGR, Sharpe, max DD at a glance
  - Detail page `/backtests/[run_id]` with:
    - Full-height equity curve (lightweight-charts), drawdown as muted
      red area overlay
    - Regime shading: vertical bands for COVID / Russia / Red Sea with
      labels on hover, configurable (reads regimes from backend)
    - KPI tile grid: Sharpe, Sortino, Calmar, max DD, hit rate, avg
      holding period, turnover, vol, CAGR, n trades
    - Trade table: per-trade pnl, entry/exit signal z, duration; click
      row → drawer with the voyage-explorer-style detail
    - `?from=&to=` query params read from deep-link brushes (phase 19
      signal dashboard → phase 21) and slice the view client-side
    - "Compare to…" dropdown → side-by-side diff view
    - "Export as HTML" button — generates a static HTML bundle
      (embedded CSS, no JS) that prints cleanly
- Tests:
  - Playwright: render list, open run, regime shading visible,
    compare-view, deep-link slice, export button produces a file
  - Backend unit tests (≥4): list, detail, compare diff shape, export
    handles missing run gracefully
  - PDF-ready export snapshot test: render to HTML, ensure no absolute
    URLs leak, all styles inline, prints fit US Letter

## File plan
- `packages/api/src/taquantgeo_api/routers/backtests.py`
- `packages/api/tests/test_backtests_endpoints.py`
- `web/src/app/(research)/backtests/page.tsx` — list
- `web/src/app/(research)/backtests/[run_id]/page.tsx` — detail
- `web/src/app/(research)/backtests/[run_id]/ExportButton.tsx` —
  client-side HTML snapshot
- `web/src/app/(research)/backtests/compare/page.tsx` — compare view
- `web/src/components/charts/EquityCurve.tsx`, `DrawdownArea.tsx`,
  `RegimeShade.tsx`
- `web/tests/backtests.spec.ts`
- `CLAUDE.md` — register route + export artifact convention
- `docs/adrs/NNNN-backtest-viewer-export-format.md` — NEW ADR (the phase agent picks the next available ADR number at runtime): rationale for the print-ready HTML export format (inline CSS, zero JS, disclaimer text embedded verbatim, US Letter fit, absolute-URL avoidance) so the export is portable and printable offline. Justifies why we do NOT use Puppeteer-to-PDF (extra dependency, headless Chrome security surface) and why we do NOT use a React Print library (opinionated CSS assumptions).

## Non-goals
- Running a backtest from the UI (the CLI path remains canonical).
  Candidate for a "Run new backtest" button later.
- Live paper-trading comparison — candidate (when paper trading
  produces results vs backtest)

## Quality gates
- Format + lint + typecheck clean
- Playwright tests green
- Lighthouse Perf ≥ 90 for list page, ≥ 85 for detail (allows heavier
  chart canvas)
- Pre-commit meta-review full loop
- New ADR committed (`docs/adrs/NNNN-backtest-viewer-export-format.md`) — export format is a non-obvious, forward-load-bearing decision (the evidence-bundle HTML is what the user will send to collaborators / investors) and deserves a written rationale.

## Git workflow
1. Branch `feat/phase-21-backtest-viewer`
2. Commits:
   - `feat(api): backtests endpoints (list, detail, compare)`
   - `feat(web): backtest list + detail + regime shading`
   - `feat(web): compare-two-backtests view`
   - `feat(web): export as print-ready HTML`
   - `test: backtests playwright + API`
3. PR, CI green, squash-merge

## Handoff
Screenshot paths for list + detail + compare. Exported HTML sample
commented in the handoff (path; not committed).
