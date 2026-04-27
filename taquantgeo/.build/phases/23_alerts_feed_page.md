# Phase 23 — Alerts feed page

## Metadata
- Effort: `standard`
- Depends on phases: 17, 12
- Applies security-review: `no`
- Max phase runtime (minutes): 120
- External services: none

## Mission
When phase 12 fires an alert to Discord, the operator can acknowledge on
the phone. But investigating *why* requires the full context — the
original event, nearby alerts, links into the underlying voyage / signal
snapshot / recon diff. This page is that investigation surface. Live
updates via server-sent events so the operator watching the page sees
alerts land in real time, not via polling.

## Orientation
- `.build/handoffs/12_handoff.md`, `17_handoff.md`
- `packages/core/src/taquantgeo_core/alerting.py` — sinks
- `data/alerts.log` path and JSON schema
- `packages/trade/src/taquantgeo_trade/audit.py` — for deep-links into
  audit entries referenced by an alert

## Service preflight
None.

## Acceptance criteria
- Backend:
  - `class SseAlertTap(AlertSink)` in `packages/core/...alerting.py` —
    broadcasts to an in-memory asyncio queue; FastAPI streams it
  - `GET /api/alerts` — paginated history with filters (severity,
    component, time range, free-text search)
  - `GET /api/alerts/stream` — SSE endpoint; replays last 50 on connect
    then streams new events as they arrive
  - `POST /api/alerts/{id}/acknowledge` — mark acknowledged (stored in
    Postgres `alert_ack` table; alembic migration in this phase)
  - `POST /api/alerts/{id}/archive` — mark archived
- Frontend `/alerts` route:
  - Timeline (most recent first), severity-colored left border
    (info grey, warn amber, critical red)
  - Filter bar: severity, component, time range, free-text search
  - Click alert → side drawer with:
    - Full body + context JSON pretty-printed
    - Links to referenced entities (voyage trip_id → /voyages/{id};
      signal as_of → /signal?as_of=…; recon diff → /trading with
      recon drawer)
    - Acknowledge / archive buttons
  - Live SSE updates: new alerts animate in at top
  - Offline indicator: if SSE connection drops, header shows "Live
    updates paused — polling every 30s" and falls back to polling
- Tests:
  - Playwright: `test_alerts_page_loads`, `test_filter_by_severity`,
    `test_click_opens_drawer`, `test_acknowledge_persists`,
    `test_sse_updates_prepend_new_alert`,
    `test_sse_drops_triggers_polling_fallback`
  - Backend unit: `SseAlertTap` broadcasts to multiple subscribers,
    disconnect is clean, `/acknowledge` idempotent, search query
    parses correctly

## File plan
- `packages/core/src/taquantgeo_core/alerting.py` — add `SseAlertTap`
- `packages/core/tests/test_alerting.py` — extend
- `packages/api/src/taquantgeo_api/routers/alerts.py`
- `packages/api/tests/test_alerts_endpoints.py`
- `infra/alembic/versions/0007_alerts_ack_table.py`
- `web/src/app/(ops)/alerts/page.tsx`
- `web/src/app/(ops)/alerts/{Timeline,Filters,AlertDrawer,LiveStatus}.tsx`
- `web/src/lib/sse.ts` — reusable SSE wrapper with reconnect
- `web/tests/alerts.spec.ts`
- `CLAUDE.md` — register route

## Non-goals
- Alert aggregation / grouping — candidate (currently every alert is a
  separate row)
- Mobile push notifications — candidate
- Alert escalation rules UI — candidate

## Quality gates
- Format + lint + typecheck clean
- Playwright + backend tests green
- SSE fallback to polling tested
- Pre-commit meta-review full loop
- `uv run alembic upgrade head` clean

## Git workflow
1. Branch `feat/phase-23-alerts-feed`
2. Commits:
   - `feat(core): SseAlertTap broadcast sink`
   - `feat(api): /api/alerts + SSE stream + ack + archive`
   - `feat(api): alert_ack migration`
   - `feat(web): alerts timeline + drawer + live SSE`
   - `feat(web): SSE reconnect + polling fallback`
   - `test: alerts playwright + API`
3. PR, CI green, squash-merge

## Handoff
SSE latency observed locally (synthetic alert → page update). Note any
browser-side SSE quirks (reconnection storms, buffering).
