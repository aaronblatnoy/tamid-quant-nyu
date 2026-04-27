# Phase 13 — Broker interface + MockBroker + Risk gate

## Metadata
- Effort: `max`
- Depends on phases: 01
- Applies security-review: `yes`
- Max phase runtime (minutes): 180
- External services: none (no live IBKR yet — phase 14)

## Mission
The firewall between signal and dollar. Even with a successful backtest
and green IC, code bugs will try to submit the wrong size, wrong ticker,
wrong direction, or submit repeatedly after a restart. The risk gate is
the single point that every order transits; it rejects before the broker
sees the submission. This phase defines the `Broker` protocol so later
phases (14, 22) never mention IBKR directly, ships a `MockBroker` for
tests and local dev, and ships a `RiskGate` that enforces per-name size,
gross exposure, daily-loss, and kill-switch flags. Every rejection
appends an audit entry (audit log is phase 16; for now a jsonl stub is
fine). There is NO bypass flag anywhere in this module, ever — a bypass
flag would defeat the point.

## Orientation
- `.build/handoffs/01_handoff.md`
- `packages/core/src/taquantgeo_core/config.py` — risk-limit fields
  (`max_position_usd`, `max_gross_exposure_usd`, `daily_loss_limit_usd`,
  `kill_switch`)
- `docs/data_model.md` — `orders`, `fills`, `audit_log`, `positions_book`
- `docs/runbook.md` — kill-switch ops
- `packages/ais/src/taquantgeo_ais/gfw/events.py` — quality bar

## Service preflight
None for this phase (no external services). Risk env vars have defaults
in `Settings`; absent env just means the default values apply.

## Acceptance criteria
- NEW package `packages/trade/` registered.
- `packages/trade/src/taquantgeo_trade/models.py` with frozen
  dataclasses:
  - `Order(client_order_id, ticker, side: Literal["buy","sell"], qty,
     order_type: Literal["market","limit"], limit_cents, reason)`
  - `OrderResult(client_order_id, broker_order_id, status, submitted_at,
     message)`
  - `Position(ticker, qty, avg_price_cents, last_updated_at)`
  - `AccountSummary(cash_cents, gross_exposure_cents, realized_pnl_cents,
     unrealized_pnl_cents)`
- `packages/trade/src/taquantgeo_trade/broker.py` with:
  ```python
  class Broker(Protocol):
      def submit(self, order: Order) -> OrderResult: ...
      def cancel(self, order_id: str) -> None: ...
      def positions(self) -> list[Position]: ...
      def account_summary(self) -> AccountSummary: ...
  ```
- `packages/trade/src/taquantgeo_trade/mock_broker.py` with
  `class MockBroker(Broker)`:
  - Deterministic fills at a configurable price (defaults to the
    `limit_cents` if set, else a configured `market_fill_cents`)
  - In-memory `positions` dict, `orders` list
  - `fast_forward_ticks(n)` helper for tests (deterministic time)
  - Configurable to force rejections for edge-case tests
- `packages/trade/src/taquantgeo_trade/risk.py`:
  - `@dataclass(frozen=True) class RiskContext: max_position_usd: int;
     max_gross_exposure_usd: int; daily_loss_limit_usd: int;
     kill_switch: bool`
  - `class RiskGate:`
    - `__init__(self, ctx, broker: Broker, audit_sink: Callable[[dict], None])`
    - `def pre_trade_check(self, order: Order) -> RiskDecision:` — returns
      `Ok()` or `Rejected(reason, context)`
    - Rejection paths:
      - Kill switch engaged
      - New order would push position in `order.ticker` beyond
        `max_position_usd` after fill at quoted price
      - New order would push gross exposure (sum of |position_cents|
        across all tickers) beyond `max_gross_exposure_usd`
      - Today's realized loss ≥ `daily_loss_limit_usd`
      - Missing ticker price (can't size without mark)
      - Order quantity ≤ 0, or side not in {"buy","sell"}, or ticker
        not in allowed universe list
    - Every rejection records an audit entry via `audit_sink`
- `packages/trade/src/taquantgeo_trade/audit.py` — TEMPORARY jsonl stub
  (replaced by phase 16 with hash-chained Postgres table):
  - `class JsonlAuditSink:` appends to `data/audit_log.jsonl`
  - Records: ts, event_type, payload
- `make_broker(settings) -> Broker` factory returns `MockBroker()` in
  phase 13. Phase 14 edits this function to return `IbkrBroker` when
  IBKR env set — factory call sites are unchanged.
- Tests — adversarial + defensive:
  - `test_risk_rejects_when_kill_switch_engaged`
  - `test_risk_rejects_when_position_would_exceed_max_usd`
  - `test_risk_rejects_when_gross_would_exceed_max_usd`
  - `test_risk_rejects_when_daily_loss_breached`
  - `test_risk_rejects_when_qty_zero_or_negative`
  - `test_risk_rejects_when_ticker_not_in_allowed_universe`
  - `test_risk_rejects_when_price_missing_cannot_size`
  - `test_risk_records_audit_entry_on_every_rejection`
  - `test_risk_records_audit_entry_on_approval_too` — approvals are
    audited, not just rejections
  - `test_risk_rejection_is_idempotent_does_not_corrupt_broker_state`
  - `test_risk_cannot_be_bypassed_via_monkeypatching_kill_switch_flag`
    — asserts the RiskGate reads kill_switch fresh from its ctx at
    call time, not a cached value
  - `test_mock_broker_submit_deterministic`
  - `test_mock_broker_positions_roundtrip`
  - `test_mock_broker_fast_forward_reproducible`
  - `test_factory_returns_mock_broker_when_ibkr_host_unset`
- All quality gates green.

## File plan
- `packages/trade/pyproject.toml` — new
- `packages/trade/src/taquantgeo_trade/__init__.py`
- `packages/trade/src/taquantgeo_trade/models.py`
- `packages/trade/src/taquantgeo_trade/broker.py`
- `packages/trade/src/taquantgeo_trade/mock_broker.py`
- `packages/trade/src/taquantgeo_trade/risk.py`
- `packages/trade/src/taquantgeo_trade/audit.py` (jsonl stub)
- `packages/trade/src/taquantgeo_trade/factory.py` — `make_broker`
- `packages/trade/tests/test_risk.py`
- `packages/trade/tests/test_mock_broker.py`
- `packages/trade/tests/test_factory.py`
- `pyproject.toml` (root) — workspace member
- `docs/adrs/0013-broker-protocol-and-risk-gate.md` — NEW ADR: why a
  Protocol, why no bypass flag, why RiskGate is a discrete class
  (testable in isolation without any broker)
- `CLAUDE.md` — register package + its real-money-discipline note
  (every change to `packages/trade/` triggers security-review subagent)

## Non-goals
- IBKR wiring — phase 14
- Reconciliation — phase 15
- Audit log Postgres table + hash chain — phase 16
- Live trading UI — phase 22
- Order types beyond market + limit (OCO, brackets, trailing stops) —
  candidate entries
- Live position seeding from broker on startup — handled in phase 15
  reconciliation

## Quality gates
- Format + lint + typecheck clean
- ≥14 new tests
- Pre-commit meta-review: scope gate met (real-money surface)
- **Security-review subagent MANDATORY** per frontmatter. Must check:
  - Can kill switch be disabled at runtime? (Should only be via env +
    restart)
  - Can any caller bypass `pre_trade_check`? Reachability analysis.
  - Are Settings fields mutable post-startup?
  - Does audit sink capture enough to reconstruct a rejection later?
  - Arithmetic overflow paths on very large order qty
- Three-round review loop per `Effort: max`
- ADR 0013 committed

## Git workflow
1. Branch `feat/phase-13-broker-risk`
2. Commits:
   - `build(trade): new workspace package`
   - `feat(trade): Broker protocol + models + MockBroker`
   - `feat(trade): RiskGate with no-bypass invariant`
   - `feat(trade): jsonl audit sink stub`
   - `feat(trade): make_broker factory`
   - `test(trade): adversarial risk + broker coverage`
   - `docs: ADR 0013 broker protocol and risk gate`
3. PR, CI green, squash-merge

## Handoff
Summary of enforced invariants. Any security-review findings and how
they were resolved. Note that phase 14 will replace `make_broker`'s
return with IBKR when env set; the interface must remain unchanged.
