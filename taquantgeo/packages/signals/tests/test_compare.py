"""Tests for ``taquantgeo_signals.compare`` — IC analysis & fail-fast gate.

Each test pins one piece of the contract:

- ``compute_ic`` matches scipy reference for both methods, drops NaN, and
  returns NaN safely on degenerate inputs.
- ``walk_forward_ic`` is look-ahead-free (perfect predictor → IC ≈ 1;
  shifted by +1 day → IC drops sharply) and tolerates calendar mismatch.
- ``ic_summary`` produces a clean markdown table.
- ``evaluate_verdict`` fires the fail-fast trigger only when **every**
  cell in the gate's coverage is flat; mixed-result histories pass.
- ``regime_breakdown`` slices windows correctly into COVID / Russia /
  Red-Sea regimes.
- ``generate_report`` writes a markdown file and returns the verdict.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from scipy import stats as sp_stats

from taquantgeo_signals.compare import (
    DEFAULT_HORIZONS_DAYS,
    DEFAULT_MIN_HISTORY_DAYS,
    FAIL_FAST_HORIZONS_DAYS,
    FAIL_FAST_METHODS,
    IC_FAIL_FAST_MEAN_ABS,
    IC_FAIL_FAST_T_STAT,
    REGIMES,
    ICResult,
    ICVerdict,
    basket_return,
    compute_ic,
    evaluate_verdict,
    generate_report,
    ic_summary,
    regime_breakdown,
    walk_forward_ic,
)

SYNTHETIC_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ic_synthetic_signal.parquet"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_basket_prices(
    *,
    start: date,
    n_trading_days: int,
    tickers: list[str],
    base: float = 100.0,
    seed: int = 0,
    drift: float = 0.0,
    sigma: float = 0.01,
) -> pl.DataFrame:
    """Synthetic equity-basket prices on weekday calendar.

    Each ticker walks geometric-Brownian-style with given drift/sigma. We
    iterate ourselves rather than calling pandas' bdate_range because the
    test stays self-contained.
    """
    rng = np.random.default_rng(seed)
    dates: list[date] = []
    d = start
    while len(dates) < n_trading_days:
        if d.weekday() < 5:  # Mon-Fri
            dates.append(d)
        d += timedelta(days=1)
    rows: list[dict[str, object]] = []
    for ti, t in enumerate(tickers):
        log_p = math.log(base + ti)
        for d_i in dates:
            log_p += drift + sigma * float(rng.standard_normal())
            close = math.exp(log_p)
            rows.append(
                {
                    "ticker": t,
                    "as_of": d_i,
                    "close_cents": round(close * 100),
                }
            )
    return pl.DataFrame(rows)


def _make_signal_from_basket(
    prices_df: pl.DataFrame,
    *,
    horizon_days: int,
    tickers: list[str],
    shift_days: int = 0,
    noise: float = 0.0,
    seed: int = 1,
) -> pl.DataFrame:
    """Build a signal that perfectly predicts forward log-returns at ``horizon_days``.

    ``shift_days`` simulates a look-ahead bug (positive shift = signal leaks
    info from the future; negative shift = signal lags). ``noise`` adds
    Gaussian noise so we can dial down predictive strength.
    """
    fwd = basket_return(prices_df, tickers=tickers, horizon_days=horizon_days)
    rng = np.random.default_rng(seed)
    sig = fwd["forward_return"].to_numpy().copy()
    if noise > 0:
        sig = sig + noise * rng.standard_normal(sig.shape[0])
    if shift_days != 0:
        # Positive shift ⇒ signal at row t equals what perfect-predictor signal at row t+shift would be.
        shifted = np.full_like(sig, np.nan)
        if shift_days > 0:
            shifted[:-shift_days] = sig[shift_days:]
        else:
            k = -shift_days
            shifted[k:] = sig[:-k]
        sig = shifted
    return pl.DataFrame({"as_of": fwd["as_of"].to_list(), "signal": sig.tolist()}).drop_nulls()


# ---------------------------------------------------------------------------
# compute_ic
# ---------------------------------------------------------------------------


def test_compute_ic_pearson_matches_scipy_reference() -> None:
    rng = np.random.default_rng(42)
    s = rng.standard_normal(200)
    r = 0.4 * s + rng.standard_normal(200) * 0.5
    res = compute_ic(s, r, method="pearson")
    expected, _ = sp_stats.pearsonr(s, r)
    assert res.n_obs == 200
    assert math.isclose(res.ic, float(expected), abs_tol=1e-12)
    # Pearson IC should be >> 0 — sanity check on the regression.
    assert res.ic > 0.3


def test_compute_ic_spearman_matches_scipy_reference() -> None:
    rng = np.random.default_rng(7)
    s = rng.standard_normal(200)
    r = np.sign(s) * rng.standard_normal(200) ** 2  # monotone-ish but non-linear
    res = compute_ic(s, r, method="spearman")
    sr = sp_stats.spearmanr(s, r)
    expected = float(getattr(sr, "statistic", getattr(sr, "correlation", float("nan"))))
    assert math.isclose(res.ic, expected, abs_tol=1e-12)
    # rank_ic field is always Spearman regardless of `method`.
    assert math.isclose(res.rank_ic, expected, abs_tol=1e-12)


def test_compute_ic_returns_nan_on_constant_input() -> None:
    s = np.zeros(50)
    r = np.linspace(-1, 1, 50)
    res = compute_ic(s, r, method="pearson")
    assert math.isnan(res.ic)
    assert math.isnan(res.t_stat)
    assert res.n_obs == 50  # constant != insufficient — pin sample is preserved


def test_compute_ic_drops_nan_pairs() -> None:
    s = np.array([1.0, 2.0, float("nan"), 4.0, 5.0])
    r = np.array([2.0, 4.0, 6.0, float("nan"), 10.0])
    res = compute_ic(s, r, method="pearson")
    # Only 3 valid pairs after dropping rows with NaN in either column.
    assert res.n_obs == 3
    # (1,2) (2,4) (5,10) are perfectly linear → IC = 1.0 (within FP noise).
    assert math.isclose(res.ic, 1.0, abs_tol=1e-12)


# ---------------------------------------------------------------------------
# basket_return
# ---------------------------------------------------------------------------


def test_basket_return_equal_weight_log_returns() -> None:
    # Two tickers, deterministic price paths so we can hand-compute the
    # equal-weight forward log-return.
    start = date(2024, 1, 1)
    rows: list[dict[str, object]] = []
    for i, d_off in enumerate(range(10)):
        d = start + timedelta(days=d_off)
        # Skip weekends to get a clean trading-day index.
        if d.weekday() >= 5:
            continue
        rows.append({"ticker": "A", "as_of": d, "close_cents": int(100_00 * (1.05**i))})
        rows.append({"ticker": "B", "as_of": d, "close_cents": int(200_00 * (1.02**i))})
    prices = pl.DataFrame(rows)
    out = basket_return(prices, tickers=["A", "B"], horizon_days=2)
    # Row 0: log(close_A[2]/close_A[0]) = log(1.05^2) = 2*log(1.05);
    #         log(close_B[2]/close_B[0]) = 2*log(1.02);
    #         basket = mean = log(1.05) + log(1.02)
    expected_first = math.log(1.05) + math.log(1.02)
    actual_first = float(out["forward_return"].head(1).item())
    # Use cents-rounded prices so allow ~1e-3 tolerance.
    assert math.isclose(actual_first, expected_first, abs_tol=2e-3)


def test_basket_return_drops_rows_without_enough_forward_history() -> None:
    prices = _make_basket_prices(
        start=date(2024, 1, 1), n_trading_days=10, tickers=["A"], seed=0, sigma=0.0
    )
    out = basket_return(prices, tickers=["A"], horizon_days=5)
    # Only the first 5 trading days have a t+5 close, so forward_return is
    # observable on rows 0..4 and NaN on rows 5..9 — drop_nulls leaves 5.
    assert out.height == 5


# ---------------------------------------------------------------------------
# walk_forward_ic
# ---------------------------------------------------------------------------


def test_walk_forward_ic_no_lookahead() -> None:
    """A perfect predictor scores ~1; shifting it +1 day breaks predictive power.

    Pins the look-ahead invariant: walk_forward_ic must use only signals
    observable at the decision date. A signal that pretends to know the
    next day's value should look perfect *only* when paired with the
    correctly-aligned forward return.
    """
    horizon = 5
    prices = _make_basket_prices(
        start=date(2022, 1, 3),
        n_trading_days=400,
        tickers=["A", "B"],
        seed=11,
        sigma=0.012,
    )
    perfect_signal = _make_signal_from_basket(
        prices, horizon_days=horizon, tickers=["A", "B"], shift_days=0
    )
    leaked_signal = _make_signal_from_basket(
        prices, horizon_days=horizon, tickers=["A", "B"], shift_days=1, noise=0.05
    )

    wf_perfect = walk_forward_ic(
        perfect_signal,
        prices,
        horizons_days=[horizon],
        min_history_days=120,
        step_days=30,
        tickers=["A", "B"],
    )
    wf_shifted = walk_forward_ic(
        leaked_signal,
        prices,
        horizons_days=[horizon],
        min_history_days=120,
        step_days=30,
        tickers=["A", "B"],
    )
    # The perfect predictor should produce mean Spearman IC ≈ 1.0 across
    # every window; the shifted variant should drop substantially.
    perfect_spearman = wf_perfect.filter(pl.col("method") == "spearman")["ic"].to_list()
    shifted_spearman = wf_shifted.filter(pl.col("method") == "spearman")["ic"].to_list()
    assert perfect_spearman, "no walk-forward windows produced for perfect signal"
    assert shifted_spearman, "no walk-forward windows produced for shifted signal"
    perfect_mean = float(np.nanmean(perfect_spearman))
    shifted_mean = float(np.nanmean(shifted_spearman))
    assert perfect_mean > 0.95, f"perfect predictor mean IC was {perfect_mean}"
    assert shifted_mean < perfect_mean - 0.5, (
        f"shifted predictor IC ({shifted_mean}) did not drop sharply "
        f"vs perfect ({perfect_mean}) — look-ahead invariant suspect"
    )


def test_walk_forward_ic_handles_missing_days_gracefully() -> None:
    """Signal calendar (every calendar day) ≠ price calendar (weekdays only).
    The as-of join carries each signal forward to the next trading day, so
    the function does not blow up on the calendar mismatch."""
    prices = _make_basket_prices(
        start=date(2023, 1, 2),
        n_trading_days=300,
        tickers=["X"],
        seed=21,
        sigma=0.01,
    )
    # Signal on every calendar day (weekends included); deterministic linear
    # trend so the regression actually exists.
    n_cal = 350
    sig_dates = [date(2023, 1, 2) + timedelta(days=i) for i in range(n_cal)]
    sig_values = [float(i) / n_cal for i in range(n_cal)]
    signals = pl.DataFrame({"as_of": sig_dates, "signal": sig_values})
    wf = walk_forward_ic(
        signals,
        prices,
        horizons_days=[5, 10],
        min_history_days=90,
        step_days=20,
        tickers=["X"],
    )
    # Function must produce SOME windows without raising.
    assert wf.height > 0
    # Window-end dates are all <= max(price.as_of) by construction.
    max_price_d = prices["as_of"].max()
    assert wf["window_end"].max() <= max_price_d


def test_walk_forward_ic_steps_correctly() -> None:
    """Step_days controls window cadence — fewer steps = more windows."""
    prices = _make_basket_prices(
        start=date(2024, 1, 2), n_trading_days=300, tickers=["A"], seed=33, sigma=0.01
    )
    signal = _make_signal_from_basket(
        prices, horizon_days=5, tickers=["A"], shift_days=0, noise=0.5
    )
    wf_30 = walk_forward_ic(
        signal,
        prices,
        horizons_days=[5],
        min_history_days=120,
        step_days=30,
        tickers=["A"],
    )
    wf_15 = walk_forward_ic(
        signal,
        prices,
        horizons_days=[5],
        min_history_days=120,
        step_days=15,
        tickers=["A"],
    )
    # Halving the step count at minimum doubles (within rounding) the
    # number of decision dates.
    n_30 = wf_30.filter(pl.col("method") == "pearson").height
    n_15 = wf_15.filter(pl.col("method") == "pearson").height
    assert n_15 >= n_30
    assert n_15 >= int(0.9 * 2 * n_30) - 1  # tolerate ±1 boundary effect


# ---------------------------------------------------------------------------
# ic_summary / regime_breakdown
# ---------------------------------------------------------------------------


def test_ic_summary_formats_markdown() -> None:
    prices = _make_basket_prices(
        start=date(2024, 1, 2), n_trading_days=300, tickers=["A"], seed=55, sigma=0.01
    )
    signal = _make_signal_from_basket(
        prices, horizon_days=5, tickers=["A"], shift_days=0, noise=0.5
    )
    wf = walk_forward_ic(
        signal,
        prices,
        horizons_days=[5, 10],
        min_history_days=120,
        step_days=30,
        tickers=["A"],
    )
    summary = ic_summary(wf)
    assert not summary.empty
    expected_cols = {
        "horizon_days",
        "method",
        "mean_ic",
        "median_ic",
        "mean_t_stat",
        "mean_hit_rate",
        "ir",
        "n_windows",
    }
    assert expected_cols <= set(summary.columns)
    # 2 horizons x 2 methods -> 4 rows.
    assert len(summary) == 4
    # Markdown render is non-empty + contains the column headers.
    md = summary.to_markdown(index=False)
    assert "horizon_days" in md
    assert "method" in md


def test_regime_breakdown_slices_correctly() -> None:
    """Synthetic walk-forward with windows in COVID, Russia, and Red-Sea
    regimes — each row must land in the correct bucket."""
    rows = [
        # COVID (covid_2020 = 2020-03-01 → 2020-05-31)
        {
            "window_end": date(2020, 4, 1),
            "horizon_days": 5,
            "method": "pearson",
            "ic": 0.10,
            "rank_ic": 0.10,
            "t_stat": 1.0,
            "hit_rate": 0.6,
            "n_obs": 100,
        },
        # Russia (russia_2022 = 2022-02-24 → ∞)
        {
            "window_end": date(2022, 6, 1),
            "horizon_days": 5,
            "method": "pearson",
            "ic": 0.05,
            "rank_ic": 0.05,
            "t_stat": 0.5,
            "hit_rate": 0.5,
            "n_obs": 100,
        },
        # Red-Sea (red_sea_2023 = 2023-11-19 → ∞) AND russia overlap
        {
            "window_end": date(2024, 4, 1),
            "horizon_days": 5,
            "method": "pearson",
            "ic": 0.20,
            "rank_ic": 0.20,
            "t_stat": 2.0,
            "hit_rate": 0.65,
            "n_obs": 100,
        },
        # Pre-COVID — falls in NO regime
        {
            "window_end": date(2019, 6, 1),
            "horizon_days": 5,
            "method": "pearson",
            "ic": -0.10,
            "rank_ic": -0.10,
            "t_stat": -1.0,
            "hit_rate": 0.4,
            "n_obs": 100,
        },
    ]
    wf = pl.DataFrame(rows)
    rb = regime_breakdown(wf)
    assert not rb.empty
    by_regime = {(r["regime"], r["horizon_days"], r["method"]): r for _, r in rb.iterrows()}
    # COVID: 1 window
    assert by_regime[("covid_2020", 5, "pearson")]["n_windows"] == 1
    assert math.isclose(float(by_regime[("covid_2020", 5, "pearson")]["mean_ic"]), 0.10)
    # Russia: 2 windows (2022-06-01 and 2024-04-01) → mean = 0.125
    russia = by_regime[("russia_2022", 5, "pearson")]
    assert russia["n_windows"] == 2
    assert math.isclose(float(russia["mean_ic"]), 0.125)
    # Red-Sea: 1 window (2024-04-01)
    red_sea = by_regime[("red_sea_2023", 5, "pearson")]
    assert red_sea["n_windows"] == 1
    assert math.isclose(float(red_sea["mean_ic"]), 0.20)


def test_regimes_constant_matches_documented_dates() -> None:
    """Pin the regime windows so a future change to the constants is loud."""
    by_name = {name: (start, end) for name, start, end in REGIMES}
    assert by_name["covid_2020"] == (date(2020, 3, 1), date(2020, 5, 31))
    assert by_name["russia_2022"] == (date(2022, 2, 24), None)
    assert by_name["red_sea_2023"] == (date(2023, 11, 19), None)


# ---------------------------------------------------------------------------
# evaluate_verdict / fail-fast trigger
# ---------------------------------------------------------------------------


def test_fail_fast_trigger_when_all_horizons_flat() -> None:
    """Synthetic random signal vs synthetic random returns → no edge anywhere → FAIL_NO_EDGE."""
    sig_fixture = pl.read_parquet(SYNTHETIC_FIXTURE)
    # Sanity: fixture is a 4-year daily series.
    assert sig_fixture.height >= 1000
    # Build a price stream uncorrelated with the signal.
    start = sig_fixture["as_of"].min()
    assert start is not None
    prices = _make_basket_prices(
        start=start,
        n_trading_days=900,
        tickers=["A", "B", "C"],
        seed=12345,
        sigma=0.012,
    )
    wf = walk_forward_ic(
        sig_fixture.rename({"signal": "tightness"}),
        prices,
        horizons_days=list(FAIL_FAST_HORIZONS_DAYS),
        min_history_days=180,
        step_days=30,
        tickers=["A", "B", "C"],
    )
    summary = ic_summary(wf)
    verdict = evaluate_verdict(summary, wf)
    assert verdict == ICVerdict.FAIL_NO_EDGE, (
        f"random signal should trip the gate but verdict was {verdict}; summary=\n{summary}"
    )


def test_fail_fast_does_not_trigger_on_mixed_result() -> None:
    """If even ONE horizon x method combo clears the threshold, the gate
    must not fire."""
    # Build a synthetic walk-forward summary in which the 5d Spearman cell
    # is well above the threshold and every other cell is flat.
    cells: list[dict[str, object]] = []
    for h in FAIL_FAST_HORIZONS_DAYS:
        for m in FAIL_FAST_METHODS:
            if h == 5 and m == "spearman":
                cells.append(
                    {
                        "horizon_days": h,
                        "method": m,
                        "ic": 0.10,
                        "rank_ic": 0.10,
                        "t_stat": 3.0,
                        "hit_rate": 0.6,
                        "n_obs": 200,
                    }
                )
            else:
                cells.append(
                    {
                        "horizon_days": h,
                        "method": m,
                        "ic": 0.001,
                        "rank_ic": 0.001,
                        "t_stat": 0.05,
                        "hit_rate": 0.5,
                        "n_obs": 200,
                    }
                )
    # Replicate each cell across multiple windows so n_windows > MIN_VIABLE_WINDOWS.
    rows: list[dict[str, object]] = []
    for k in range(5):
        for c in cells:
            row = dict(c)
            row["window_end"] = date(2024, 1, 1) + timedelta(days=30 * k)
            rows.append(row)
    wf = pl.DataFrame(rows)
    summary = ic_summary(wf)
    verdict = evaluate_verdict(summary, wf)
    assert verdict == ICVerdict.PASS, (
        f"mixed-result summary should pass but verdict was {verdict}; summary=\n{summary}"
    )


def test_fail_fast_blocked_on_insufficient_data() -> None:
    """Empty walk-forward → BLOCKED_INSUFFICIENT_DATA, not FAIL_NO_EDGE."""
    empty_wf = pl.DataFrame(
        schema={
            "window_end": pl.Date,
            "horizon_days": pl.Int64,
            "method": pl.String,
            "ic": pl.Float64,
            "rank_ic": pl.Float64,
            "t_stat": pl.Float64,
            "hit_rate": pl.Float64,
            "n_obs": pl.Int64,
        }
    )
    summary = ic_summary(empty_wf)
    verdict = evaluate_verdict(summary, empty_wf)
    assert verdict == ICVerdict.BLOCKED_INSUFFICIENT_DATA


def test_thresholds_match_documented_values() -> None:
    """Pin the gate thresholds so a future change is loud."""
    assert IC_FAIL_FAST_MEAN_ABS == 0.02
    assert IC_FAIL_FAST_T_STAT == 1.5
    assert FAIL_FAST_HORIZONS_DAYS == (5, 10, 20)
    assert set(FAIL_FAST_METHODS) == {"pearson", "spearman"}


# ---------------------------------------------------------------------------
# generate_report (smoke test for end-to-end glue)
# ---------------------------------------------------------------------------


def test_generate_report_writes_markdown_with_banner_on_no_edge(tmp_path: Path) -> None:
    sig_fixture = pl.read_parquet(SYNTHETIC_FIXTURE).rename({"signal": "tightness"})
    start = sig_fixture["as_of"].min()
    assert start is not None
    # Same seed/tickers/sigma as test_fail_fast_trigger_when_all_horizons_flat
    # — those parameters reliably produce a flat-IC verdict against this
    # fixture; reusing them keeps the report's banner-rendering test
    # deterministic.
    prices = _make_basket_prices(
        start=start,
        n_trading_days=900,
        tickers=["A", "B", "C"],
        seed=12345,
        sigma=0.012,
    )
    out = tmp_path / "ic_report.md"
    verdict, _wf = generate_report(
        sig_fixture,
        prices,
        out_path=out,
        horizons_days=list(FAIL_FAST_HORIZONS_DAYS),
        min_history_days=180,
        step_days=30,
        tickers=["A", "B", "C"],
    )
    assert verdict == ICVerdict.FAIL_NO_EDGE
    body = out.read_text(encoding="utf-8")
    assert "NO EDGE DETECTED" in body
    assert "## IC summary" in body
    assert "## Regime breakdown" in body
    assert "## Appendix" in body


def test_generate_report_pass_path_writes_no_banner(tmp_path: Path) -> None:
    """Strong-signal path: report should NOT contain the no-edge banner."""
    horizon = 5
    prices = _make_basket_prices(
        start=date(2022, 1, 3),
        n_trading_days=600,
        tickers=["A", "B"],
        seed=2,
        sigma=0.012,
    )
    perfect_signal = _make_signal_from_basket(
        prices, horizon_days=horizon, tickers=["A", "B"], shift_days=0
    )
    out = tmp_path / "ic_pass.md"
    verdict, wf = generate_report(
        perfect_signal,
        prices,
        out_path=out,
        horizons_days=[horizon],
        min_history_days=120,
        step_days=30,
        tickers=["A", "B"],
    )
    assert verdict == ICVerdict.PASS
    body = out.read_text(encoding="utf-8")
    assert "NO EDGE DETECTED" not in body
    assert "EDGE DETECTED" in body
    assert wf.height > 0


# ---------------------------------------------------------------------------
# ICResult shape pin
# ---------------------------------------------------------------------------


def test_ic_result_is_frozen() -> None:
    res = ICResult(ic=0.1, rank_ic=0.1, t_stat=1.0, hit_rate=0.55, n_obs=100)
    with pytest.raises((AttributeError, TypeError)):
        res.ic = 0.2  # type: ignore[misc]


def test_default_horizons_match_phase_contract() -> None:
    assert DEFAULT_HORIZONS_DAYS == (5, 10, 20)
    assert DEFAULT_MIN_HISTORY_DAYS == 180
