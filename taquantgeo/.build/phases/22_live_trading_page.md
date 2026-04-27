# Phase 22 — Live trading page

## Metadata
- Effort: `max`
- Depends on phases: 17, 14, 15
- Applies security-review: `yes`
- Max phase runtime (minutes): 180
- External services:
  - IBKR env vars — optional. Page functions with MockBroker for demos
    when IBKR is not configured; a top-of-page badge makes the backend
    explicit ("Backend: Mock" vs "Backend: IBKR paper / live").

## Mission
Operator cockpit. When live trades are firing, the operator needs one
page that answers: am I making money today? what am I holding? is the
system healthy? how close am I to my risk limits? can I kill it from
here? The kill switch is visible, prominent, and gated by re-auth — a
misclick can't halt the system, but a deliberate click can within
seconds. No direct order entry: trades come only from signals. This is
a RESPONSE surface, not a DISCRETIONARY trading surface.

## Orientation
- `.build/handoffs/13_handoff.md`, `14_handoff.md`, `15_handoff.md`,
  `17_handoff.md`
- `packages/trade/src/taquantgeo_trade/{broker,risk,reconciliation,
  kill_switch}.py`
- `packages/trade/src/taquantgeo_trade/factory.py`
- `docs/runbook.md` — kill switch + recon procedure

## Service preflight
- IBKR env optional (mock fallback makes page demoable).

## Acceptance criteria
- Backend:
  - `GET /api/trade/summary` — {pnl_today, pnl_mtd, pnl_all_time,
    positions[], orders_recent[], risk_usage{}, recon_status,
    broker_backend: "mock" | "ibkr_paper" | "ibkr_live"}
  - `POST /api/trade/kill-switch` — engages kill switch. Requires
    re-auth: request body includes a fresh GitHub OAuth challenge
    token; without it, 401. Idempotent.
  - `GET /api/trade/audit?since=` — recent audit-log entries (for
    breadcrumb context at bottom of page)
- Frontend `/trading` route (within `(trading)` group, admin-only):
  - Env badge (dev/paper/live) — red for live
  - Backend badge ("Backend: Mock" / "Backend: IBKR paper" / "IBKR live")
  - Top row: big P&L numbers (today / MTD / all-time) in JetBrains Mono
  - Positions table: ticker, qty, avg entry, mark, unrealized P&L,
    risk-headroom bar per position (colored by % consumed)
  - Orders log (last 20 orders; status chips)
  - Risk dashboard card: `max_position_usd` / `max_gross_exposure_usd`
    / `daily_loss_limit_usd` — each as a consumed/available bar.
    Crosses 90% → card border turns amber; 100% → red.
  - Reconciliation status indicator: 🟢 matched / 🟡 drift / 🔴 mismatch.
    Click opens the latest diff (from `/api/trade/audit` filtered)
  - Full-width red kill switch button. Click → modal:
    - Lists affected positions
    - Text input "type KILL to confirm"
    - Requires GitHub re-auth via Auth.js step-up (user is sent through
      GitHub OAuth again to prove presence; stored challenge token
      posted with the POST request)
    - Engages via the `/api/trade/kill-switch` endpoint
  - No order entry UI anywhere
- Tests:
  - Playwright: page renders, positions populate, risk bars reflect,
    kill-switch modal requires step-up, mock kill-switch engages,
    reconciliation widget reflects status
  - Backend unit tests: summary shape, kill-switch requires step-up
    token (rejects without, accepts with), audit endpoint filters by
    date, 401 without admin session
- Security:
  - The kill-switch endpoint requires a step-up OAuth challenge token
    less than 120 seconds old. Tests verify rejection of expired,
    replay, and missing tokens.
  - No plaintext API keys/tokens render anywhere in the page HTML
    (Playwright assertion: page source does not contain common secret
    patterns)

## File plan
- `packages/api/src/taquantgeo_api/routers/trade.py`
- `packages/api/src/taquantgeo_api/auth_stepup.py` — step-up challenge
  validator
- `packages/api/tests/test_trade_endpoints.py`
- `web/src/app/(trading)/trading/page.tsx`
- `web/src/app/(trading)/trading/{PnlHeader,PositionsTable,OrdersLog,
  RiskCard,ReconStatus,KillSwitchButton,KillSwitchModal}.tsx`
- `web/src/auth/stepup.ts` — client step-up helper
- `web/tests/trading.spec.ts`
- `docs/adrs/0020-live-trading-surface.md` — NEW ADR: no-direct-order-
  entry rule, step-up auth for kill switch, role model (admin only)
- `docs/runbook.md` — "Using the live trading page" section

## Non-goals
- Order entry — explicit non-goal (system-only trading)
- Position management (manual close, partial close) — candidate,
  risk-reviewed separately
- P&L attribution (to specific voyages / signal inputs) — candidate

## Quality gates
- Format + lint + typecheck clean
- Playwright ≥6 tests green
- Backend ≥6 tests green, including step-up auth edge cases
- Pre-commit meta-review + **security-review subagent mandatory**
- Three-round review loop per `Effort: max`
- ADR 0020 committed
- Runbook updated

## Git workflow
1. Branch `feat/phase-22-live-trading-page`
2. Commits:
   - `feat(api): trade summary + kill-switch + audit endpoints`
   - `feat(api): step-up OAuth challenge validator`
   - `feat(web): live trading page - P&L + positions + risk + recon`
   - `feat(web): kill-switch modal with step-up re-auth`
   - `test: trading playwright + API step-up coverage`
   - `docs: ADR 0020 live trading surface; runbook`
3. PR, CI green, squash-merge

## Handoff
Screenshot of kill-switch modal, risk bars at different saturations.
Security-review subagent output (or its summary + resolution trail).
Note whether the page was tested against MockBroker only or also real
paper IBKR.
