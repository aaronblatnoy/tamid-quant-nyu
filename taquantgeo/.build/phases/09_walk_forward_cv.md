# Phase 09 — Walk-forward CV + report

## Metadata
- Effort: `max`
- Depends on phases: 08
- Applies security-review: `no`
- Max phase runtime (minutes): 120
- External services: none

## Mission
The v0 evidence bundle. A single in-sample backtest is insufficient
evidence to risk real capital. Walk-forward cross-validation splits
history into rolling train/test windows: threshold / config tuned on
train, evaluated out-of-sample on test, results concatenated. This phase
runs the WFCV across 2017–present, attributes performance to three named
regimes (COVID March–May 2020; Russia invasion Feb 2022+; Red Sea / Houthi
Nov 2023+), and writes `reports/backtest_v1.md` — the artifact that,
combined with `reports/ic_analysis.md`, gates any decision to trade real
capital. No human review pause in the harness flow; the report is the
decision-support doc, not a gate.

## Orientation
- `.build/handoffs/07_handoff.md`, `08_handoff.md`
- `reports/ic_analysis.md`
- `packages/backtest/src/taquantgeo_backtest/engine.py`
- `packages/signals/src/taquantgeo_signals/compare.py`
- `docs/adrs/0010-backtester-architecture.md`

## Service preflight
None (reads previously populated Postgres + any fixtures).

## Acceptance criteria
- `packages/backtest/src/taquantgeo_backtest/walkforward.py` with:
  - `@dataclass(frozen=True) class WFCVConfig: train_window_days;
     test_window_days; step_days; param_grid: dict[str, list[object]]`
  - `run_wfcv(signals_df, prices_df, base_config, wfcv_config) ->
     WFCVResult` where `WFCVResult` holds per-window config choice,
    per-window OOS stats, stitched OOS trades, stitched OOS equity curve
- `packages/backtest/src/taquantgeo_backtest/regimes.py` with:
  - `REGIMES: tuple[tuple[str, date, date], ...]` — named (regime_name,
    start, end)
  - `slice_by_regime(trades_df or equity_curve_df, regime_name) ->
    DataFrame`
- `packages/backtest/src/taquantgeo_backtest/report.py` —
  `write_backtest_report(result, ic_artifacts, out_path) -> Path`
- Report `reports/backtest_v1.md` contains:
  - Header: data range, N windows, param grid, base config
  - Top-line KPIs (Sharpe, Sortino, Calmar, max DD, hit rate, total
    return, CAGR, turnover, avg hold) for the stitched OOS series
  - Equity curve (ASCII monospace sparkline)
  - Drawdown series (ASCII)
  - Per-regime stats table
  - Trade log summary (count, winners, losers, avg winner, avg loser,
    worst single trade, best single trade)
  - Walk-forward config-selection history (which config won each window)
  - Limitations section citing ADR 0002 Gap 4 (equity-proxy vs TD3C spot),
    Gap 2 (dark-fleet undercoverage), and the IC analysis verdict
- CLI: `taq backtest wfcv [--config backtest/v1_config.json]
  [--out reports/backtest_v1.md]`. Writes report + side-by-side
  trades/equity parquets under `backtest_results/v1/`.
- Tests:
  - `test_wfcv_no_train_test_leakage` — training window never includes
    test window observations
  - `test_wfcv_param_grid_coverage` — each window evaluates every point
    in the grid
  - `test_regime_slice_matches_date_bounds`
  - `test_report_lists_all_regimes_even_when_empty`
  - `test_report_cites_limitations_section` — asserts the boilerplate
    limitations section is present (prevents silent removal)
  - `test_wfcv_stitched_equity_continuous` — no gaps at window seams
- All quality gates green.

## File plan
- `packages/backtest/src/taquantgeo_backtest/walkforward.py`
- `packages/backtest/src/taquantgeo_backtest/regimes.py`
- `packages/backtest/src/taquantgeo_backtest/report.py`
- `packages/backtest/tests/test_walkforward.py`
- `packages/backtest/tests/test_regimes.py`
- `packages/backtest/tests/test_report.py`
- `packages/cli/src/taquantgeo_cli/backtest.py` — add `wfcv` subcommand
- `docs/adrs/0011-walkforward-cv.md` — NEW ADR: train/test sizes,
  stepping choice, regime selection rationale, why v1 limitations (no
  transaction costs beyond slippage, equity proxies) are accepted for
  a v0 evidence bundle
- `reports/backtest_v1.md` — generated artifact (not committed)
- `CLAUDE.md` — CLI registration

## Non-goals
- Parameter optimization beyond the declared grid (no continuous
  tuning). Candidate entry.
- Monte Carlo uncertainty bands — mentioned as a known-improvement in the
  report's limitations. Candidate entry.
- Changing the signal definition to improve OOS — that requires a new
  ADR amending 0007 and phase 04 revisions, not a walk-forward tweak.

## Quality gates
- Format + lint + typecheck clean
- ≥6 new tests
- Pre-commit meta-review full loop
- ADR 0011 committed
- Three-round review loop per `Effort: max`

## Git workflow
1. Branch `feat/phase-09-walkforward-cv`
2. Commits:
   - `feat(backtest): walk-forward cross-validation`
   - `feat(backtest): regime slicing (COVID, Russia, Red Sea)`
   - `feat(backtest): report generator + limitations section`
   - `feat(cli): taq backtest wfcv`
   - `test(backtest): WFCV + regime + report coverage`
   - `docs: ADR 0011 walk-forward CV`
3. PR, CI green, squash-merge
4. Run WFCV against real data; commit the report path reference
   only (report itself is gitignored under reports/)

## Handoff
The top-line KPIs as they came out of the real run. Per-regime stats.
Any regime where OOS looks materially worse than in-sample (overfit
signal). A note flagging whether the report meets the "defensible v0
evidence bundle" bar subjectively — if not, the user may still proceed
but future phase work should flag the limitations prominently.
