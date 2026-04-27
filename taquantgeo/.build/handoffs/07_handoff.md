# Phase 07 handoff

## Status
`blocked`

The Phase 07 tooling shipped cleanly (PR #12 merged green on first CI
attempt at sha `a12a679`; follow-up RESEARCH_LOG entry merged in PR
#13 at sha `5bdfc16`). The real-data run of `taq signals ic` returns
verdict `BLOCKED_INSUFFICIENT_DATA` — the gate did its job, the
harness should halt here, and the user must complete a Phase-01-bound
prerequisite (multi-month historical voyages backfill + batch
historical-signal compute) before re-running.

This is **NOT** a `FAIL_NO_EDGE` verdict — the signal math has not
been falsified; we simply don't have enough signal × price overlap
to compute one walk-forward window. The harness should pause here
rather than build Phase 08 onward against a 30-day signal history.

## What shipped

- `packages/signals/src/taquantgeo_signals/compare.py` — new module
  (~870 lines). Public surface:
  - `compute_ic(signal_series, return_series, *, method)` — single-
    sample IC. Returns `ICResult` with fields `ic` (per chosen method),
    `rank_ic` (always Spearman), `t_stat`, `hit_rate`, `n_obs`. Drops
    paired NaN, returns NaNs on constant or near-empty inputs.
  - `walk_forward_ic(signals_df, prices_df, *, horizons_days,
     min_history_days, step_days, tickers, weights)` — produces a
    long-form DataFrame with one row per (window_end, horizon, method).
    Look-ahead-free: at decision date T only signals with as_of ≤ T
    are visible; forward returns require `t + h ≤ max(price.as_of)`,
    enforced by `basket_return`'s `shift(-h)` + drop_nulls.
  - `basket_return(prices_df, *, tickers, weights, horizon_days)` —
    forward log-return for the basket, equal-weighted by default.
    Per-row weight renormalisation so a missing-ticker day contributes
    only the tickers actually present.
  - `ic_summary(wf_df) -> pandas.DataFrame` — pivots to per-(horizon,
    method) summary with mean/median IC, mean |t-stat|, mean hit-rate,
    IR, n_windows.
  - `regime_breakdown(wf_df) -> pandas.DataFrame` — slices windows
    by REGIMES (covid_2020, russia_2022, red_sea_2023). Open-ended
    end-dates handled.
  - `evaluate_verdict(summary_df, wf_df) -> ICVerdict` — applies the
    fail-fast gate. PASS if any viable cell clears thresholds;
    FAIL_NO_EDGE if every gate cell is covered + viable + flat;
    BLOCKED_INSUFFICIENT_DATA otherwise.
  - `generate_report(signals_df, prices_df, *, out_path, …)` — full
    end-to-end. Writes a markdown report with the verdict banner,
    data summary, IC table, ASCII sparkline, regime breakdown,
    appendix. Returns `(verdict, walk_forward_df)`.
  - Constants: `IC_FAIL_FAST_MEAN_ABS = 0.02`,
    `IC_FAIL_FAST_T_STAT = 1.5`,
    `FAIL_FAST_HORIZONS_DAYS = (5, 10, 20)`,
    `FAIL_FAST_METHODS = ("pearson", "spearman")`,
    `MIN_VIABLE_WINDOWS = 3`,
    `DEFAULT_HORIZONS_DAYS = (5, 10, 20)`,
    `DEFAULT_MIN_HISTORY_DAYS = 180`,
    `DEFAULT_STEP_DAYS = 20`. All `Final`.
  - Module docstring is the executable summary of ADR 0009.
- `packages/signals/src/taquantgeo_signals/__init__.py` — re-exports
  the new IC surface and pointers to ADR 0009.
- `packages/signals/pyproject.toml` — adds `scipy>=1.13`,
  `pandas>=2.2`, `tabulate>=0.9` (the last for pandas `to_markdown`).
- `packages/cli/src/taquantgeo_cli/signals.py` — new `taq signals ic`
  subcommand. Pulls signals (filtered to `supply_floor_clamped == 0`
  per Phase 04 invariant) and prices from Postgres; calls
  `generate_report`; writes the markdown to `--out`
  (default `reports/ic_analysis.md`); exits 10 with a manual-setup-
  required append on FAIL_NO_EDGE / BLOCKED_INSUFFICIENT_DATA. PASS
  path exits 0 silently. Prices NOT filtered by `--until` — forward
  returns at the upper signal cutoff need horizon-days of buffer.
- `packages/signals/tests/test_compare.py` — 20 unit tests pinning
  every contract bullet:
  - Pearson / Spearman match scipy reference at 1e-12 abs_tol
  - NaN-pair drop + n<3 short-circuit + constant-input safety
  - Basket return equal-weight log math (hand-derived) +
    forward-history drop on the right edge
  - Walk-forward look-ahead invariant (perfect predictor IC ~ 1;
    +1-day-shifted predictor IC drops sharply)
  - Calendar mismatch (signal on every calendar day, prices weekday
    only) survives the as-of join
  - Step-days controls window cadence (halving step roughly doubles
    window count)
  - IC summary markdown table shape (4 rows for 2×2 horizons×methods)
  - Regime breakdown slices into COVID/Russia/Red-Sea correctly
    (with the 2024-04-01 row landing in both russia and red-sea)
  - Constants pinned to documented values
  - Fail-fast trigger fires on the synthetic random-signal fixture
    against a 3-ticker uncorrelated basket
  - Mixed-result summary (5d Spearman strong, others flat) → PASS
  - Empty walk-forward → BLOCKED_INSUFFICIENT_DATA
  - generate_report writes correct banner per verdict (NO EDGE on
    flat synthetic; EDGE DETECTED on perfect-predictor)
  - ICResult is frozen
- `packages/signals/tests/test_signals_ic_cli.py` — 4 CLI tests:
  - End-to-end: seed 1461 random signals + 2700 random prices →
    exit 10 + report banner contains "NO EDGE DETECTED" + manual-
    setup file written with the Phase 07 marker
  - `supply_floor_clamped == 1` filter: seed 5 floored + 4 clean →
    stdout contains "signal observations: 4" (proves selectivity)
  - PASS path: seed perfect signals (= 5d forward log-returns of
    the basket) + same prices → exit 0 + no manual-setup-required
    file + report contains "EDGE DETECTED"
  - `taq signals --help` lists the `ic` subcommand
- `packages/signals/tests/fixtures/regenerate_ic_synthetic_signal.py`
  + `ic_synthetic_signal.parquet` — 1 461-row deterministic random
  signal (4 years × 365.25 days, seed 20260421, gaussian N(0,1))
  used by the no-edge tests. Re-running the regenerator produces a
  byte-identical parquet (verified).
- `docs/adrs/0009-ic-fail-fast-gate.md` — new ADR pinning the gate
  thresholds (0.02 mean |IC|, 1.5 mean |t-stat|, ≥ 3 windows per
  cell, full coverage of horizons × methods) and rationale; eight
  alternatives considered (no gate, stricter / looser thresholds,
  single-horizon gate, OOS-only IC, bootstrap CIs, simple returns,
  Postgres-side aggregations).
- `docs/RESEARCH_LOG.md` — top entry recording the real-run verdict
  (BLOCKED_INSUFFICIENT_DATA, root cause, unblock path).
- `CLAUDE.md` — registers the new `taq signals ic` example in the
  useful-commands block.
- `uv.lock` — regenerated for `scipy`, `pandas`, `tabulate` adds.
- `reports/ic_analysis.md` — generated artifact (gitignored). Banner
  reproduced verbatim under "Real run verdict" below.

## PR

- Primary: https://github.com/sn12-dev/taquantgeo/pull/12
  - CI: **green** on first attempt (label 7s, lint-typecheck 24s,
    test 1m4s)
  - Merge sha: `a12a679`
- Follow-up (RESEARCH_LOG only):
  https://github.com/sn12-dev/taquantgeo/pull/13
  - CI: green
  - Merge sha: `5bdfc16`

## Real run verdict

```
================================================================
==                                                            ==
==        INSUFFICIENT DATA — HARNESS BLOCKED                 ==
==                                                            ==
==  Not enough overlap between signal history and price       ==
==  history to compute IC across the required horizons.       ==
==                                                            ==
==  Backfill prices (taq prices backfill) and / or compute    ==
==  historical signals (taq signals compute-tightness over    ==
==  a date range) before re-running the IC analysis.          ==
==                                                            ==
================================================================
```

Concrete state at run time:
- `signal observations: 0 (post-floor filter)`
- `price observations: 9348` (4 tickers × ~2 337 daily bars
  spanning 2017-01-03 → 2026-04-20; EURN delisted, returned 0 rows
  per ADR 0008)
- `verdict: blocked_insufficient_data`
- `exit=10`

Manual-setup-required entry (idempotent — safe to re-run):

```
## Phase 07 — IC fail-fast gate (2026-04-21)

- Verdict: **blocked_insufficient_data**
- Diagnostic: see `reports/ic_analysis.md`
- Action: backfill prices (`taq prices backfill --since 2017-01-01`)
  and compute historical signals
  (`taq signals compute-tightness --as-of <date> --persist` over a
  date range) before re-running.
```

The price backfill *was* completed during the phase run (9 348 rows
landed). The remaining gap is **historical signals**: only 1 signal
row (2026-03-15) is persisted, and it is `supply_floor_clamped = 1`
so the CLI filters it out. Without a multi-month batch run of
`compute-tightness`, the IC gate cannot fire.

## Surprises / findings

**The gate works as designed; the data isn't there yet.** The
verdict is `BLOCKED_INSUFFICIENT_DATA`, not `FAIL_NO_EDGE`. The two
paths look superficially similar (both exit 10, both append to
manual_setup_required.md, both halt the harness) but the user-action
prescription differs sharply: `FAIL_NO_EDGE` says "re-think the math
in ADR 0007"; `BLOCKED` says "backfill prerequisites". Splitting
them was load-bearing — round-1 review almost flagged this as a
"why not just one path" concern but the rationale held.

**The supply_floor_clamped filter is the load-bearing contract.**
Phase 04's handoff documented that pre-Phase-05 snapshots are mostly
just the supply floor talking, and IC code MUST filter on
`components["supply_floor_clamped"] == 0`. We honor that contract
honestly — the result is 0 usable signals today (1 persisted row,
all flagged). A version of this code that ignored the filter would
have been able to produce SOME walk-forward windows, but they would
have been noise, and the verdict would have been a false-positive
PASS or FAIL. Phase-04 invariant earned its keep here.

**Price backfill is fast and clean.** `taq prices backfill --since
2017-01-01` against the 5-ticker basket completed in ~2 seconds for
2 337 rows × 4 active tickers (EURN delisted, returned 0 rows
gracefully per Phase 06's WARN-don't-crash contract). That ADR-0008
contract held in production on first try. The ~9 K rows are now
available for any future re-run of the IC gate without further
backfill.

**EURN is dead in our universe.** Yfinance returns "possibly
delisted; no timezone found" on every backfill attempt. Phase 06's
ADR 0008 anticipated this — the basket effectively drops to 4 names
(FRO / DHT / INSW / TNK). Future signal IC work should either
(a) live with the 4-name basket, (b) add CMB.TECH (CMB.BR on
Euronext Brussels — different instrument), or (c) add Polygon.io as
a backup. None of these are blockers for Phase 08; they're
calibration concerns for whichever variant the backtester picks.

**`pandas.to_markdown` needs `tabulate`.** Phase 04 / 05 didn't
exercise the to_markdown path so this dep was latent. Phase 07's
`ic_summary` returns a pandas DataFrame deliberately (so the report
gets a proper markdown table), forcing `tabulate>=0.9` into
`packages/signals/pyproject.toml`. Caught by the first test run —
the import error message names the package by name, so the fix was
~5 minutes.

**Tests-dir basename collision (re-occurrence)**. Same lesson as
phase 06: `test_compare.py` exists in `packages/signals/tests/` and
nowhere else, but the CLI test `test_signals_ic_cli.py` was named
defensively from the start to dodge any collision with sibling
packages. No issue this round, but the convention "every new test
file gets a unique-by-suffix basename" should keep getting honored
until someone bothers to add `__init__.py` to every package's tests/
dir.

**`np.sign(0) == np.sign(0)` is True.** The original `compute_ic`
hit-rate code `np.mean(np.sign(s) == np.sign(r))` was tagged as
"zero-sign pairs count as misses" in the docstring but actually
counted (0, 0) as a hit. Round-1 code-review caught the discrepancy
between doc and behavior; fix is `np.mean((sign_s != 0) & (sign_s
== sign_r))`. In practice exact-zero returns are rare so the bug
was nearly inert, but on a sparse signal where many rows happened
to round to zero the hit rate was inflated. Doc now matches code.

**The `_ALL_IC_METHODS` extraction matters for future flexibility.**
First draft had `walk_forward_ic` iterate over `FAIL_FAST_METHODS`
directly. Style-review flagged the conflation: a future variant
(e.g., adding Kendall-Tau as a diagnostic-only method) needs the
walk-forward to compute it without changing the gate's coverage
semantics. Refactor to a separate `_ALL_IC_METHODS` constant kept
the two concerns orthogonal; one-line change with a useful comment.

**Round-1 surfaced 19 findings; rounds 2 and 3 returned empty
critical/major.** The `Effort: max` 3-round review caught:
- Hit-rate semantics bug (above)
- Redundant ternary in ICResult construction (`pearson if method ==
  "pearson" else rank_ic` after computing the same in `chosen`)
- Conflation of gate methods with walk-forward methods (above)
- Emoji in user-facing report (`❌ ⏸ ✅` → `[FAIL] [BLOCKED]
  [PASS]` per CLAUDE.md "no emojis" rule)
- `datetime.now()` without UTC in the report header
- Dead `if until_d: pass` branch in the CLI
- Path string-slicing in the CLI test (`__file__.rsplit("/", 1)`
  vs `Path(__file__).resolve().parent`)
- Loose CLI banner assertion (`"NO EDGE" or "INSUFFICIENT DATA"` —
  could mask a verdict-classification regression; tightened)
- Missing PASS-path CLI test (added `_seed_perfect_signals_and_
  prices` helper + new test)
- Floor-clamp filter test that asserted only `signal observations:
  0` — strengthened to seed 5 floored + 4 clean and assert `4`
- `evaluate_verdict` docstring didn't mention the
  `--horizon`-subset interaction with full gate coverage

Rounds 2 and 3 returned empty — the loop converged.

**The `as_of <= T` strict-vs-non-strict question.** A code-review
finding (deferred for now) raised whether `join_asof(strategy=
"backward")` could in principle pair a Sunday signal with a Monday
forward-return where the signal was *actually computed* during US
market hours but stamped with Sunday's UTC date. Today this isn't
exploitable: `compute_daily_tightness` is deterministic on
historical inputs and `as_of` is the calendar date, not a timestamp.
But if a future scheduler ever stamps `as_of` based on
"compute_started_at.date()" rather than "intended_target_date", the
join could leak future close info. Worth pinning in a future ADR
when we wire APScheduler in Phase 11.

## Test count delta

- Before: 182 (per Phase 06 handoff's recorded after-count;
  build_state.json's baseline of 53 is still stale — driver hasn't
  updated it since Phase 00)
- After: 206 (delta **+24**, +20 in `test_compare.py`, +4 in
  `test_signals_ic_cli.py`)
- New tests:
  - `test_compute_ic_pearson_matches_scipy_reference`
  - `test_compute_ic_spearman_matches_scipy_reference`
  - `test_compute_ic_returns_nan_on_constant_input`
  - `test_compute_ic_drops_nan_pairs`
  - `test_basket_return_equal_weight_log_returns`
  - `test_basket_return_drops_rows_without_enough_forward_history`
  - `test_walk_forward_ic_no_lookahead`
  - `test_walk_forward_ic_handles_missing_days_gracefully`
  - `test_walk_forward_ic_steps_correctly`
  - `test_ic_summary_formats_markdown`
  - `test_regime_breakdown_slices_correctly`
  - `test_regimes_constant_matches_documented_dates`
  - `test_fail_fast_trigger_when_all_horizons_flat`
  - `test_fail_fast_does_not_trigger_on_mixed_result`
  - `test_fail_fast_blocked_on_insufficient_data`
  - `test_thresholds_match_documented_values`
  - `test_generate_report_writes_markdown_with_banner_on_no_edge`
  - `test_generate_report_pass_path_writes_no_banner`
  - `test_ic_result_is_frozen`
  - `test_default_horizons_match_phase_contract`
  - `test_ic_cli_writes_report_and_exits_10_on_no_edge`
  - `test_ic_cli_pass_path_exits_zero_and_no_manual_setup_append`
  - `test_ic_cli_filters_out_floor_clamped_signals`
  - `test_ic_cli_help_lists_subcommand`
- Tests removed: none.

Phase contract required ≥ 8 new tests. Delivered **+24**. Driver
should bump `build_state.json.test_count_baseline` from 182 to 206.

## Optional services not configured

None. Phase 07 needed only `DATABASE_URL` (already configured for
all prior phases) and read access to the `signals` and `prices`
tables. No external API calls.

## Deferred / open questions

- **Historical-signal compute job is the next prerequisite.** The
  unblock for the BLOCKED verdict is `taq signals compute-tightness
  --as-of <date> --persist` over a multi-month date range. There is
  no batch-mode of this command today — each invocation persists one
  day. A small follow-up phase ("Phase 07b" — historical signal
  backfill) should wrap that in a date-range CLI that loads
  prior_snapshots_df incrementally. Posted as a candidate-phase
  entry below.
- **Voyages history is the deeper prerequisite.** Even with a batch
  signal compute, only ~30 daily snapshots can land today (Jan 2026
  voyages are the only on-disk data). The harness needs ≥ 6 months
  of voyages for the gate's 180-day min_history_days to fire.
  `taq gfw ingest-voyages` against more `voyages_c4_pipe_v3_<YYYYMM>
  .csv` files is the unblock. Tracked in
  `.build/manual_setup_required.md`.
- **Sub-set --horizon flag interaction.** Round-1 review noted that
  passing `taq signals ic --horizon 5` (a single horizon) cannot
  produce `FAIL_NO_EDGE` — the gate requires full coverage of
  (5, 10, 20). The verdict will be either PASS or BLOCKED. ADR 0009
  documents this; CLI behavior is intentional. A future operator-UX
  improvement could log a one-line warning on subset invocations.
- **Banner f-string is module-load-time.** `_NO_EDGE_BANNER` is
  built once with the constants' values at import. If a future ADR
  amendment changes the thresholds, the constants update but a
  cached banner could be stale. Constants are `Final` and tests pin
  them; mutation in practice would require a rebuild. Not worth
  fixing today.
- **Per-name attribution.** Phase 07 reports basket-level IC only.
  A future analytical phase could break IC down per ticker (FRO vs
  DHT vs INSW vs TNK) to identify which equity carries the
  TD3C-correlated signal vs which is noise. Not blocking the
  trading thesis; in scope for a "post-validation diagnostics" phase.
- **OOS / walk-forward CV.** Phase 07's IC is in-sample (windows
  overlap heavily). Phase 09 is the proper out-of-sample test;
  ADR 0009 explicitly defers to it. If Phase 09 ever lands before
  signal-history is sufficient, it will hit the same prerequisite
  shortage.
- **Bootstrap confidence intervals on IC.** ADR 0009 considered and
  rejected for v0; a future amendment could add bootstrap if the
  gate's t-stat approximation produces ambiguous decisions on real
  data.

## Ideas for future phases

Appended to `.build/candidate_phases.md` (below this handoff if not
already present):

- **Candidate: Historical signal batch compute.** A new
  `taq signals backfill-tightness --since YYYY-MM-DD --until
  YYYY-MM-DD --route td3c [--persist]` that iterates
  `compute-tightness` over the date range with shared
  prior_snapshots_df load (one read at the start, then in-memory
  accumulate). Direct unblock for the Phase 07 BLOCKED verdict.
  Effort: standard. Acceptance: backfilling a 6-month range
  produces ≥ 120 signal rows in Postgres and a re-run of
  `taq signals ic` fires either PASS or FAIL_NO_EDGE (no longer
  BLOCKED).
- **Candidate: Per-name IC attribution.** Break basket IC into
  per-ticker IC; surface in the report's Appendix and (later) on
  the dashboard. Effort: standard. Acceptance: report contains a
  per-ticker IC table; new test pins per-ticker IC matches manual
  computation against a 2-ticker fixture.
- **Candidate: Bootstrap CIs on IC.** Replace the t-stat
  approximation with K-fold bootstrap intervals; surface in report
  + integrate into the gate. Effort: max (changes the gate
  semantics; needs ADR 0009 amendment). Acceptance: bootstrap CI
  width < 0.03 on test fixtures; gate fires only if CI excludes
  zero.

Two informal ideas raised in review but not promoted as candidates:

- **Decouple banner construction from constant values via a
  pure-function builder.** Eliminates the module-load-time staleness
  concern; sub-second compute cost. Trivial change, deferred
  because the constants are `Final` and tests pin them.
- **Log a one-line WARN when the CLI is invoked with a `--horizon`
  subset.** Operator-UX improvement; helps a confused user
  understand why their subset run can never FAIL. Defer until the
  CLI sees enough use to justify it.

## For the next phase

- **The harness is blocked.** Driver should detect the `blocked`
  status here and halt. Phase 08 (backtester core) cannot begin
  until the prerequisite is resolved.
- **Unblock path is unambiguous.** Backfill historical voyages
  (`taq gfw ingest-voyages` for `voyages_c4_pipe_v3_<YYYYMM>.csv`
  files spanning at least 6 months prior to today), then either
  (a) implement the historical-signal batch CLI from candidates
  above and run it, OR (b) loop `taq signals compute-tightness
  --as-of <date> --persist` over each date in shell. Re-run
  `taq signals ic` after, and the verdict will resolve to PASS,
  FAIL_NO_EDGE, or remain BLOCKED with a clearer reason.
- **Persisted prices are now ready.** 9 348 rows across FRO /
  DHT / INSW / TNK (2017-01-03 → 2026-04-20). EURN dropped (per
  ADR 0008). No further price work needed before Phase 08 unless
  Phase 07b changes the basket.
- **Signals filter contract is load-bearing.** Any IC / backtest /
  trade-signal consumer in Phase 08+ MUST filter on
  `components["supply_floor_clamped"] == 0`. The Phase 04 handoff
  spelled this out; the Phase 07 CLI honors it; Phase 08 should
  inherit the same idiom.
- **Gate thresholds are pinned in ADR 0009.** A future ADR
  amendment is the right channel for changing them. Tests
  (`test_thresholds_match_documented_values`) will fail loudly on
  any drift.
- **Reports/ is gitignored.** The `reports/ic_analysis.md` artifact
  is regenerated each run; only the CLI invocation + the
  `manual_setup_required.md` append are persistent across runs.
  The handoff captures the verdict banner verbatim above.
- **Manual-setup file appends, doesn't dedupe.** Re-running the
  CLI N times appends N entries to `.build/manual_setup_required
  .md`. A future polish pass could dedupe by phase number; for now,
  the multiplicity is the audit trail.
- **PR #12 + PR #13 merged on first CI attempt** — no flake
  retries, no follow-up fixes needed in CI.
