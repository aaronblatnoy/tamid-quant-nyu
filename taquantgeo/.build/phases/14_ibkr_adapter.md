# Phase 14 — IBKR adapter

## Metadata
- Effort: `max`
- Depends on phases: 13
- Applies security-review: `yes`
- Max phase runtime (minutes): 180
- External services:
  - `IBKR_HOST (optional)`, `IBKR_PORT (optional)`, `IBKR_CLIENT_ID
    (optional)`, `IBKR_ACCOUNT (optional)` — all optional. When unset,
    `make_broker()` returns the MockBroker from phase 13 (already the
    default). When all four are set, `make_broker()` returns
    `IbkrBroker`. No code change needed; factory-switched.

## Mission
Phase 13 defined the interface; this phase provides the real
implementation. `IbkrBroker(Broker)` wraps `ib_insync`. NO trading logic
lives here — only translation. The `Broker` protocol is the contract;
the adapter only converts between our `Order` / `Position` dataclasses
and ib_insync's objects. ib_insync famously drops connections every few
minutes during market hours; the adapter must handle that without
corrupting state. Every adapter method has a unit test against stubbed
ib_insync responses, plus exactly one `@pytest.mark.live` integration
test that round-trips against a real TWS / IB Gateway (skipped in CI
when `IBKR_HOST` unset).

## Orientation
- `.build/handoffs/13_handoff.md`
- `packages/trade/src/taquantgeo_trade/broker.py` — protocol
- `packages/trade/src/taquantgeo_trade/mock_broker.py` — behavioral
  reference (what MockBroker does, IbkrBroker must match)
- `packages/trade/src/taquantgeo_trade/factory.py` — `make_broker`
- `docs/data_model.md` — `orders`, `fills` shapes (persistence mirror)
- ib_insync docs (in-session; pinned in pyproject)

## Service preflight
- All IBKR env vars optional. When any of the four is set but not all,
  the factory treats it as misconfigured and falls back to MockBroker
  with a loud WARN log — this is defensive; all-or-nothing is the only
  valid config for IBKR.

## Acceptance criteria
- `packages/trade/src/taquantgeo_trade/ibkr.py` with
  `class IbkrBroker(Broker)`:
  - `__init__(self, host, port, client_id, account, *, ib: IB | None = None)`
  - Connects on first use, reconnects on any `ConnectionError` or
    `IBKR disconnected` event, with exponential backoff capped at 60s
  - `submit`: converts `Order` → `Contract` + `MarketOrder`/`LimitOrder`,
    submits via `ib.placeOrder`, returns `OrderResult`. Places idempotency
    tag based on `client_order_id` so a restart + retry does NOT double-
    submit (IBKR supports this via `orderRef`)
  - `cancel`: cancels by broker_order_id; no-op if already filled
  - `positions`: returns list; filters to our account when multi-account
  - `account_summary`: pulls cash, gross_exposure, pnl from IBKR's
    `accountSummary` stream
  - Type-annotated; imports guarded so `import ibkr` doesn't require
    a live TWS at module load
- Alembic migration `infra/alembic/versions/0004_orders_fills_tables.py`
  adds `orders`, `fills`, `positions_book` per `docs/data_model.md`.
- `packages/trade/src/taquantgeo_trade/persistence.py` with
  `record_order(session, order, result) -> None` and
  `record_fill(session, order_id, fill) -> None` — idempotent on
  `client_order_id` and `(order_id, filled_at, qty, price)` respectively.
- `make_broker(settings)`:
  - Returns `IbkrBroker` when all of `ibkr_host`, `ibkr_port`,
    `ibkr_client_id`, `ibkr_account` are set AND `ibkr_host` is not the
    default placeholder (`127.0.0.1` with unset account counts as unset)
  - Returns `MockBroker` otherwise
  - Emits one-time INFO log with the selected backend at startup
- Tests (unit, all against stubbed ib_insync — DO NOT hit network in CI):
  - `test_ibkr_submit_market_roundtrip` (stubbed `ib.placeOrder`)
  - `test_ibkr_submit_limit_roundtrip`
  - `test_ibkr_submit_idempotent_via_order_ref` — second submit with
    same `client_order_id` returns existing broker_order_id
  - `test_ibkr_cancel_noop_if_already_filled`
  - `test_ibkr_positions_filters_by_account`
  - `test_ibkr_reconnect_on_connection_error_with_backoff`
  - `test_ibkr_record_order_idempotent` — two record_order calls with
    same `client_order_id` don't duplicate DB rows
  - `test_factory_returns_ibkr_when_all_env_set`
  - `test_factory_falls_back_to_mock_when_only_partial_env_set` —
    defensive
- Integration test (`@pytest.mark.live`, skipped without IBKR env):
  - `test_ibkr_paper_roundtrip_integration` — connects to TWS/Gateway
    on `IBKR_HOST:IBKR_PORT`, submits a tiny MKT order for a liquid
    ticker, cancels, verifies state. Skipped in CI; runs when a local
    paper-trading Gateway is available.
- All quality gates green.

## File plan
- `packages/trade/src/taquantgeo_trade/ibkr.py` — new
- `packages/trade/src/taquantgeo_trade/persistence.py` — new
- `packages/trade/src/taquantgeo_trade/factory.py` — extend
- `packages/trade/pyproject.toml` — add `ib_insync` dep
- `packages/trade/tests/test_ibkr.py`
- `packages/trade/tests/test_persistence.py`
- `packages/trade/tests/test_factory_ibkr.py`
- `packages/trade/tests/integration/test_ibkr_live.py` —
  `pytest.mark.live`
- `infra/alembic/versions/0004_orders_fills_tables.py`
- `docs/adrs/0014-ibkr-adapter.md` — NEW ADR: idempotency via orderRef,
  reconnection strategy, all-or-nothing env enforcement
- `docs/runbook.md` — "IBKR connection lost" section: symptoms,
  auto-recovery, manual intervention
- `CLAUDE.md` — note that any `packages/trade/` change requires
  paper-trading reconciliation test (already there); highlight the IBKR
  live-mark runner

## Non-goals
- Real-money flip (live port 7496) — phase 22+ operator decision
- Order types beyond market + limit
- Multi-broker failover — candidate
- Market-data subscription (we don't need tick data for this signal)

## Quality gates
- Format + lint + typecheck clean
- ≥9 new unit tests
- `@pytest.mark.live` test runnable (but not required) in CI
- `uv run alembic upgrade head` clean
- Pre-commit meta-review + security-review mandatory (per frontmatter)
- Three-round review loop per `Effort: max`
- ADR 0014 committed

## Git workflow
1. Branch `feat/phase-14-ibkr-adapter`
2. Commits:
   - `build(trade): add ib_insync dependency`
   - `feat(trade): IbkrBroker adapter implementing Broker protocol`
   - `feat(trade): orders/fills/positions_book migration + persistence`
   - `feat(trade): factory selects IBKR when all env set`
   - `test(trade): ib_insync-stubbed unit coverage`
   - `test(trade): live paper-trading integration test`
   - `docs: ADR 0014 IBKR adapter; runbook connection-lost`
3. PR, CI green (live test will skip on CI), squash-merge

## Handoff
Whether the live integration test was run locally during this phase
(if IBKR env was set). Any ib_insync quirks observed. Note explicitly
that flipping 7497→7496 is a separate operator step, not a code change.
