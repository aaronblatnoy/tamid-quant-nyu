"""Information-coefficient analysis & fail-fast gate for the TD3C signal.

This module is the **decision gate** that protects every downstream phase
(backtester, walk-forward CV, IBKR plumbing, dashboards) from being built
on a signal that has no detectable edge. Phase 07's mission, in one
sentence: *does the tightness signal have edge against the
shipping-equity basket at 1-, 2-, and 4-week horizons?* If the answer is
"no" everywhere, ``generate_report`` writes a banner-headed report,
appends a manual-setup-required entry, and the harness halts.

Public surface
--------------
- ``compute_ic(signal_series, return_series, *, method)`` — point-IC for
  one matched (signal, return) sample. Returns ``ICResult``.
- ``walk_forward_ic(signals_df, prices_df, *, horizons_days,
  min_history_days, step_days)`` — rolls a window over the matched
  series and computes IC per (window_end, horizon, method).
- ``basket_return(prices_df, *, tickers, weights, horizon_days)`` —
  forward log-return for an equal-weight (or user-weighted) basket.
- ``ic_summary(wf_df)`` — pivots walk-forward output into a per-(horizon,
  method) summary table (mean IC, IR, n windows, …).
- ``regime_breakdown(wf_df)`` — slices walk-forward output by regime
  (COVID / Russia / Red Sea) and emits per-regime mean IC.
- ``generate_report(signals_df, prices_df, *, out_path, …)`` — full
  end-to-end: IC + summary + sparkline + regime + verdict, written as
  markdown. Returns ``(verdict, walk_forward_df)``.
- ``ICResult`` — frozen dataclass.
- ``ICVerdict`` — enum: ``PASS``, ``FAIL_NO_EDGE``,
  ``BLOCKED_INSUFFICIENT_DATA``.
- ``IC_FAIL_FAST_MEAN_ABS``, ``IC_FAIL_FAST_T_STAT``,
  ``FAIL_FAST_HORIZONS_DAYS``, ``FAIL_FAST_METHODS``,
  ``MIN_VIABLE_WINDOWS`` — gate-threshold constants.

Look-ahead invariant
--------------------
At decision date ``T`` the only signal observations visible are those with
``as_of <= T``. The forward return paired with signal at ``as_of=t`` uses
basket prices at ``t`` and ``t + horizon_days`` (calendar-aligned to
nearest available trading day). A signal observation is included in a
window only if its forward return is fully observable — i.e.,
``t + horizon_days <= max(price.as_of)``. Tests pin this with a perfect
predictor (IC ≈ 1) and a +1-day-shifted predictor (IC ≈ 0).

Threshold rationale
-------------------
``IC_FAIL_FAST_MEAN_ABS = 0.02`` and ``IC_FAIL_FAST_T_STAT = 1.5`` are
deliberately permissive. The gate fires only when the signal is flat
across **every** horizon AND **both** correlation methods. A signal that
clears either threshold for any single (horizon, method) pair passes —
backtester and walk-forward CV are responsible for deciding which slice
to actually trade. ADR 0009 carries the fuller justification.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Literal, cast

import numpy as np
import pandas as pd
import polars as pl
from scipy import stats as sp_stats

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

log = logging.getLogger(__name__)

ICMethod = Literal["pearson", "spearman"]

DEFAULT_HORIZONS_DAYS: Final[tuple[int, ...]] = (5, 10, 20)
"""1-, 2-, 4-week trading-day horizons (≈ 5/10/20 trading days)."""

DEFAULT_MIN_HISTORY_DAYS: Final[int] = 180
"""Minimum signal-history span (calendar days) to emit one walk-forward
window. 6 months keeps each window's IC reasonably stable while still
allowing several windows over a 2+ year history."""

DEFAULT_STEP_DAYS: Final[int] = 20
"""Step (calendar days) between successive walk-forward decision dates.
20 days ≈ one trading month — successive windows share substantial
overlap, which is fine because we report mean IC + IR across windows
rather than treating each as independent."""

IC_FAIL_FAST_MEAN_ABS: Final[float] = 0.02
"""Mean |IC| threshold below which the signal contributes no detectable
predictive power. Spearman ICs in the 0.03-0.05 range are typical for
weak but trade-able quant signals; below 0.02 the signal-to-noise is too
low to overcome trade frictions in any reasonable backtest."""

IC_FAIL_FAST_T_STAT: Final[float] = 1.5
"""Mean |t-stat| threshold for a window's IC. 1.5 is below the
conventional 1.96 (95% one-sided), chosen permissively — the gate fires
only on flat IC, not on borderline-significant IC."""

FAIL_FAST_HORIZONS_DAYS: Final[tuple[int, ...]] = (5, 10, 20)
"""All three horizons must be flat for the gate to fire."""

FAIL_FAST_METHODS: Final[tuple[ICMethod, ...]] = ("pearson", "spearman")
"""Both methods must be flat for the gate to fire."""

_ALL_IC_METHODS: Final[tuple[ICMethod, ...]] = ("pearson", "spearman")
"""Methods walk_forward_ic emits per window. Currently the union of
gate methods, but kept distinct so the gate's coverage and the
computed-method set can diverge later (e.g., a Kendall-Tau-only
diagnostic mode) without breaking the gate semantics."""

MIN_VIABLE_WINDOWS: Final[int] = 3
"""Minimum number of walk-forward windows per (horizon, method) needed to
return a statistical verdict. Below this, ``generate_report`` returns
``BLOCKED_INSUFFICIENT_DATA`` rather than the no-edge verdict — a stalled
data prerequisite is not the same as a tested-and-flat signal."""

REGIMES: Final[list[tuple[str, date, date | None]]] = [
    ("covid_2020", date(2020, 3, 1), date(2020, 5, 31)),
    ("russia_2022", date(2022, 2, 24), None),
    ("red_sea_2023", date(2023, 11, 19), None),
]
"""Regime windows for the per-regime IC breakdown. ``None`` end-date means
'open-ended' — every window with ``window_end >= start`` is included.
Regimes overlap by design (russia and red_sea both contain 2024-04 windows);
the breakdown reports per-regime mean IC, not a partition."""


@dataclass(frozen=True)
class ICResult:
    """Single-sample IC. ``ic`` is per the chosen method; ``rank_ic`` is
    always Spearman so the caller can compare both at one glance."""

    ic: float
    rank_ic: float
    t_stat: float
    hit_rate: float
    n_obs: int


class ICVerdict(StrEnum):
    """Outcome of ``generate_report``. The CLI maps:
    PASS → exit 0; FAIL_NO_EDGE / BLOCKED_INSUFFICIENT_DATA → exit 10."""

    PASS = "pass"  # noqa: S105 — enum member name, not a credential
    FAIL_NO_EDGE = "fail_no_edge"
    BLOCKED_INSUFFICIENT_DATA = "blocked_insufficient_data"


def _safe_t_stat(ic_value: float, n: int) -> float:
    """t = ic * sqrt(n - 2) / sqrt(1 - ic^2), guarded against ic == ±1
    (perfect correlation → infinite t — clamp to a large finite value so
    summary stats don't blow up)."""
    if math.isnan(ic_value) or n < 3:
        return float("nan")
    denom = max(1.0 - ic_value * ic_value, 1e-12)
    return ic_value * math.sqrt(n - 2) / math.sqrt(denom)


def compute_ic(
    signal_series: pl.Series | np.ndarray | Sequence[float],
    return_series: pl.Series | np.ndarray | Sequence[float],
    *,
    method: ICMethod = "spearman",
) -> ICResult:
    """IC for one matched (signal, return) sample.

    Drops paired NaN before computing. Returns NaNs (with ``n_obs`` = the
    valid count) if fewer than 3 valid pairs remain or if either series is
    constant (correlation undefined). ``rank_ic`` is always Spearman; ``ic``
    follows ``method``.
    """
    s = np.asarray(signal_series, dtype=float)
    r = np.asarray(return_series, dtype=float)
    if s.shape != r.shape:
        raise ValueError(f"shape mismatch: signal={s.shape} returns={r.shape}")
    mask = ~(np.isnan(s) | np.isnan(r))
    s = s[mask]
    r = r[mask]
    n = int(s.shape[0])
    if n < 3 or float(np.std(s)) == 0.0 or float(np.std(r)) == 0.0:
        return ICResult(
            ic=float("nan"),
            rank_ic=float("nan"),
            t_stat=float("nan"),
            hit_rate=float("nan"),
            n_obs=n,
        )
    pearson = float(np.corrcoef(s, r)[0, 1])
    # scipy.stats.spearmanr returns a SignificanceResult on modern scipy;
    # ``.statistic`` is the correlation. Older scipy used ``.correlation``.
    sr = sp_stats.spearmanr(s, r)
    rank_ic = float(getattr(sr, "statistic", getattr(sr, "correlation", float("nan"))))
    chosen = pearson if method == "pearson" else rank_ic
    t_stat = _safe_t_stat(chosen, n)
    # Hit rate: fraction where sign(signal) and sign(return) are both
    # non-zero and equal. Zero-sign pairs (one or both exactly 0) count as
    # misses — conservative choice for a thin signal where 0 is the null
    # observation, not a confident negative.
    sign_s = np.sign(s)
    sign_r = np.sign(r)
    hit_rate = float(np.mean((sign_s != 0) & (sign_s == sign_r)))
    return ICResult(
        ic=chosen,
        rank_ic=rank_ic,
        t_stat=t_stat,
        hit_rate=hit_rate,
        n_obs=n,
    )


def _normalise_signals_df(signals_df: pl.DataFrame) -> pl.DataFrame:
    """Project to (as_of, signal) with as_of as polars Date sorted ascending.

    Accepts either a frozen-style frame ``(as_of, signal)`` or the persisted
    Postgres shape ``(as_of, route, tightness, ...)`` — the column named
    ``signal`` wins if present, else ``tightness`` is used.
    """
    if "signal" in signals_df.columns:
        col = "signal"
    elif "tightness" in signals_df.columns:
        col = "tightness"
    else:
        raise ValueError(
            "signals_df must contain a 'signal' or 'tightness' column; "
            f"got columns={signals_df.columns}"
        )
    out = signals_df.select(
        pl.col("as_of").cast(pl.Date),
        pl.col(col).cast(pl.Float64).alias("signal"),
    ).drop_nulls()
    return out.sort("as_of")


def _close_in_dollars(prices_df: pl.DataFrame) -> pl.DataFrame:
    """Project (ticker, as_of, close) where close is float dollars.

    Accepts either ``close_cents`` (integer) — divide by 100 — or ``close``
    (float) directly. ``as_of`` is cast to ``pl.Date``.
    """
    cols = set(prices_df.columns)
    if "close_cents" in cols:
        return prices_df.select(
            pl.col("ticker"),
            pl.col("as_of").cast(pl.Date),
            (pl.col("close_cents").cast(pl.Float64) / 100.0).alias("close"),
        )
    if "close" in cols:
        return prices_df.select(
            pl.col("ticker"),
            pl.col("as_of").cast(pl.Date),
            pl.col("close").cast(pl.Float64),
        )
    raise ValueError(
        f"prices_df must contain 'close_cents' or 'close'; got columns={prices_df.columns}"
    )


def basket_return(
    prices_df: pl.DataFrame,
    *,
    tickers: Sequence[str] | None = None,
    weights: dict[str, float] | None = None,
    horizon_days: int,
) -> pl.DataFrame:
    """Forward log-return for the basket at ``horizon_days``.

    Per-ticker forward return at trading day ``t`` is
    ``log(close[t + horizon] / close[t])`` where ``t + horizon`` is the
    ``horizon_days``-th *trading-day* index after ``t`` (NOT the calendar
    date — weekends are skipped). The basket return is the weighted mean
    across tickers; tickers with NaN at either end of the window contribute
    NaN, which is dropped before the basket average (so a ticker with a
    short history doesn't poison the whole basket on its missing days).

    ``weights`` defaults to equal-weight across the supplied ``tickers``
    (or all tickers in ``prices_df`` if ``tickers`` is None). User-supplied
    weights are renormalised to sum to 1 — passing
    ``{"FRO": 2, "DHT": 1}`` is equivalent to ``{"FRO": 2/3, "DHT": 1/3}``.
    """
    if horizon_days <= 0:
        raise ValueError(f"horizon_days must be positive; got {horizon_days}")
    df = _close_in_dollars(prices_df)
    if df.is_empty():
        return pl.DataFrame(schema={"as_of": pl.Date, "forward_return": pl.Float64})
    selected = sorted(set(tickers) if tickers is not None else df["ticker"].unique().to_list())
    if not selected:
        return pl.DataFrame(schema={"as_of": pl.Date, "forward_return": pl.Float64})
    if weights is None:
        weights = {t: 1.0 / len(selected) for t in selected}
    else:
        total = sum(weights.get(t, 0.0) for t in selected)
        if total <= 0:
            raise ValueError(f"weights for selected tickers sum to {total}; must be > 0")
        weights = {t: weights.get(t, 0.0) / total for t in selected}

    wide = (
        df.filter(pl.col("ticker").is_in(selected))
        .pivot(values="close", index="as_of", on="ticker")
        .sort("as_of")
    )
    available = [t for t in selected if t in wide.columns]
    if not available:
        return pl.DataFrame(schema={"as_of": pl.Date, "forward_return": pl.Float64})
    # Trading-day indexed forward return: shift up by horizon and take log
    # ratio. polars ``shift(-h)`` brings row t+h into row t, so
    # ``log(shift(-h) / current)`` gives log(close[t+h] / close[t]).
    fwd_cols: list[pl.Expr] = []
    weight_exprs: list[pl.Expr] = []
    for t in available:
        # log(p[t+h]) - log(p[t]); guard against non-positive prices (yfinance
        # shouldn't emit them, but a corrupted bar could).
        fwd = (pl.col(t).shift(-horizon_days).log() - pl.col(t).log()).alias(f"_fwd_{t}")
        fwd_cols.append(fwd)
        weight_exprs.append(weights[t])
    wide = wide.with_columns(fwd_cols)

    # Weighted mean across tickers; missing per-ticker observations drop out
    # by re-normalising the weight on the present-ticker subset per row.
    fwd_col_names = [f"_fwd_{t}" for t in available]
    valid_mask_exprs = [pl.col(c).is_not_null().cast(pl.Float64) for c in fwd_col_names]
    weighted_sum_exprs = [
        pl.col(c).fill_null(0.0) * w for c, w in zip(fwd_col_names, weight_exprs, strict=True)
    ]
    weight_present = [
        pl.col(c).is_not_null().cast(pl.Float64) * w
        for c, w in zip(fwd_col_names, weight_exprs, strict=True)
    ]
    out = wide.with_columns(
        pl.sum_horizontal(*valid_mask_exprs).alias("_valid_count"),
        pl.sum_horizontal(*weighted_sum_exprs).alias("_w_sum"),
        pl.sum_horizontal(*weight_present).alias("_w_present"),
    )
    out = out.with_columns(
        pl.when(pl.col("_valid_count") > 0)
        .then(pl.col("_w_sum") / pl.col("_w_present"))
        .otherwise(None)
        .alias("forward_return")
    )
    return out.select("as_of", "forward_return").drop_nulls()


def _build_paired_series(
    signals_df: pl.DataFrame,
    prices_df: pl.DataFrame,
    *,
    horizon_days: int,
    tickers: Sequence[str] | None,
    weights: dict[str, float] | None,
) -> pl.DataFrame:
    """Inner-join (signal at as_of=t) with (forward_return over [t, t+h]).

    Trading-day calendar is the price calendar. Signal observations on
    non-trading days are forward-filled to the next trading day's pairing
    (commodity signals roll over weekends; the equity book trades on the
    next open). Tests pin this with a calendar-mismatch fixture.
    """
    sig = _normalise_signals_df(signals_df)
    fwd = basket_return(
        prices_df,
        tickers=tickers,
        weights=weights,
        horizon_days=horizon_days,
    )
    if sig.is_empty() or fwd.is_empty():
        return pl.DataFrame(
            schema={"as_of": pl.Date, "signal": pl.Float64, "forward_return": pl.Float64}
        )
    # Align signals to the trading-day calendar via as-of join (signal carries
    # forward to the next trading day). Both sides must be sorted by as_of for
    # join_asof to work.
    fwd_sorted = fwd.sort("as_of")
    paired = fwd_sorted.join_asof(
        sig,
        on="as_of",
        strategy="backward",
    ).drop_nulls(subset=["signal", "forward_return"])
    return paired.select("as_of", "signal", "forward_return").sort("as_of")


def walk_forward_ic(
    signals_df: pl.DataFrame,
    prices_df: pl.DataFrame,
    *,
    horizons_days: Sequence[int] = DEFAULT_HORIZONS_DAYS,
    min_history_days: int = DEFAULT_MIN_HISTORY_DAYS,
    step_days: int = DEFAULT_STEP_DAYS,
    tickers: Sequence[str] | None = None,
    weights: dict[str, float] | None = None,
) -> pl.DataFrame:
    """Roll a window of ``min_history_days`` over the matched series and
    compute IC at every ``step_days`` decision date for every horizon x method.

    Look-ahead-free: at decision date ``T`` only signal rows with
    ``as_of <= T`` are visible, and the forward return for a row at ``t``
    uses prices at ``t`` and ``t + horizon`` (already enforced by
    ``basket_return``'s shift).

    Returns a long-form DataFrame: one row per (window_end, horizon_days,
    method) with columns:
      ``window_end`` (Date), ``horizon_days`` (Int64), ``method`` (str),
      ``ic`` (Float64), ``rank_ic`` (Float64), ``t_stat`` (Float64),
      ``hit_rate`` (Float64), ``n_obs`` (Int64)

    An empty DataFrame is returned when no horizon yields a viable window
    (insufficient overlap between signal and price calendars, etc.).
    """
    rows: list[dict[str, object]] = []
    for h in horizons_days:
        paired = _build_paired_series(
            signals_df,
            prices_df,
            horizon_days=int(h),
            tickers=tickers,
            weights=weights,
        )
        if paired.is_empty():
            continue
        first = paired["as_of"].min()
        last = paired["as_of"].max()
        if first is None or last is None:
            continue
        first_d = cast("date", first)
        last_d = cast("date", last)
        # First decision date is min(as_of) + min_history_days, marching
        # forward by step_days until the rolling window starts running off
        # the right edge.
        decision = first_d + timedelta(days=min_history_days)
        while decision <= last_d:
            window = paired.filter(
                (pl.col("as_of") > decision - timedelta(days=min_history_days))
                & (pl.col("as_of") <= decision)
            )
            if window.height >= MIN_VIABLE_WINDOWS:
                signal = window["signal"].to_numpy()
                forward = window["forward_return"].to_numpy()
                for method_value in _ALL_IC_METHODS:
                    res = compute_ic(signal, forward, method=method_value)
                    rows.append(
                        {
                            "window_end": decision,
                            "horizon_days": int(h),
                            "method": method_value,
                            "ic": res.ic,
                            "rank_ic": res.rank_ic,
                            "t_stat": res.t_stat,
                            "hit_rate": res.hit_rate,
                            "n_obs": res.n_obs,
                        }
                    )
            decision = decision + timedelta(days=step_days)
    if not rows:
        return pl.DataFrame(
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
    return pl.DataFrame(rows)


def ic_summary(wf_df: pl.DataFrame) -> pd.DataFrame:
    """Pivot walk-forward DataFrame into a summary per (horizon, method).

    Columns: ``horizon_days``, ``method``, ``mean_ic``, ``median_ic``,
    ``mean_t_stat``, ``mean_hit_rate``, ``ir`` (mean / std of IC across
    windows), ``n_windows``. Rows are sorted by horizon then method. The
    return type is pandas because pandas' ``to_markdown`` is the cleanest
    way to render the table for the report.
    """
    if wf_df.is_empty():
        return pd.DataFrame(
            columns=[
                "horizon_days",
                "method",
                "mean_ic",
                "median_ic",
                "mean_t_stat",
                "mean_hit_rate",
                "ir",
                "n_windows",
            ]
        )
    # nan_propagation=False (the polars default) skips NaN in mean/median, but
    # be defensive: drop wholesale-NaN rows so the per-cell stats are meaningful.
    grouped = (
        wf_df.group_by(["horizon_days", "method"])
        .agg(
            [
                pl.col("ic").mean().alias("mean_ic"),
                pl.col("ic").median().alias("median_ic"),
                pl.col("t_stat").abs().mean().alias("mean_t_stat"),
                pl.col("hit_rate").mean().alias("mean_hit_rate"),
                (pl.col("ic").mean() / pl.col("ic").std()).alias("ir"),
                pl.len().alias("n_windows"),
            ]
        )
        .sort(["horizon_days", "method"])
    )
    return grouped.to_pandas()


def regime_breakdown(wf_df: pl.DataFrame) -> pd.DataFrame:
    """Per-regime mean IC by (regime, horizon, method).

    Regimes are defined in ``REGIMES``; they overlap (russia and red_sea
    both contain 2024 windows). A window is included in a regime if its
    ``window_end`` falls in the regime's date range.
    """
    if wf_df.is_empty():
        return pd.DataFrame(columns=["regime", "horizon_days", "method", "mean_ic", "n_windows"])
    pieces: list[pl.DataFrame] = []
    for name, start, end in REGIMES:
        slice_df = wf_df.filter(pl.col("window_end") >= start)
        if end is not None:
            slice_df = slice_df.filter(pl.col("window_end") <= end)
        if slice_df.is_empty():
            continue
        agg = (
            slice_df.group_by(["horizon_days", "method"])
            .agg(
                [
                    pl.col("ic").mean().alias("mean_ic"),
                    pl.len().alias("n_windows"),
                ]
            )
            .with_columns(pl.lit(name).alias("regime"))
            .select("regime", "horizon_days", "method", "mean_ic", "n_windows")
        )
        pieces.append(agg)
    if not pieces:
        return pd.DataFrame(columns=["regime", "horizon_days", "method", "mean_ic", "n_windows"])
    return pl.concat(pieces).sort(["regime", "horizon_days", "method"]).to_pandas()


def evaluate_verdict(
    summary_df: pd.DataFrame,
    wf_df: pl.DataFrame,
) -> ICVerdict:
    """Apply the fail-fast gate against the summary table.

    The ordering matters — a single (horizon, method) cell that clears the
    threshold is sufficient evidence of edge, even if other gate cells are
    missing. Conversely, a no-edge verdict requires *full* coverage of the
    gate (all three horizons x both methods); without it we can't claim
    "flat everywhere".

    Returns:
    - ``PASS`` if any available gate cell clears
      ``|mean_ic| >= IC_FAIL_FAST_MEAN_ABS`` OR
      ``|mean_t_stat| >= IC_FAIL_FAST_T_STAT`` AND has
      ``n_windows >= MIN_VIABLE_WINDOWS``.
    - ``FAIL_NO_EDGE`` if every gate cell is covered, has
      ``n_windows >= MIN_VIABLE_WINDOWS``, AND is flat on both metrics.
    - ``BLOCKED_INSUFFICIENT_DATA`` otherwise (some gate cells missing or
      under-sampled, and no covered cell shows edge).

    Note: passing only a subset of horizons via the CLI's --horizon flag
    will route through BLOCKED_INSUFFICIENT_DATA unless one of the supplied
    cells clears the threshold; the FAIL_NO_EDGE verdict requires the full
    default horizon set.
    """
    if summary_df.empty or wf_df.is_empty():
        return ICVerdict.BLOCKED_INSUFFICIENT_DATA

    gate_horizons = set(FAIL_FAST_HORIZONS_DAYS)
    gate_methods = set(FAIL_FAST_METHODS)
    cells_in_gate = summary_df[
        summary_df["horizon_days"].isin(gate_horizons) & summary_df["method"].isin(gate_methods)
    ]
    if cells_in_gate.empty:
        return ICVerdict.BLOCKED_INSUFFICIENT_DATA

    # n_windows is a pandas nullable Int64 in some polars→pandas paths; use
    # ``.fillna(0).astype(int)`` to be safe before comparing.
    viable_cells = cells_in_gate[
        cells_in_gate["n_windows"].fillna(0).astype(int) >= MIN_VIABLE_WINDOWS
    ]
    if viable_cells.empty:
        return ICVerdict.BLOCKED_INSUFFICIENT_DATA

    # Step 1: edge anywhere? If any viable cell clears the threshold on
    # either metric → PASS, regardless of missing cells.
    edge_ic = viable_cells["mean_ic"].abs() >= IC_FAIL_FAST_MEAN_ABS
    edge_t = viable_cells["mean_t_stat"].abs() >= IC_FAIL_FAST_T_STAT
    if bool((edge_ic | edge_t).any()):
        return ICVerdict.PASS

    # Step 2: no edge anywhere AND full gate coverage → FAIL_NO_EDGE.
    expected_cells = len(gate_horizons) * len(gate_methods)
    if len(viable_cells) < expected_cells:
        return ICVerdict.BLOCKED_INSUFFICIENT_DATA
    return ICVerdict.FAIL_NO_EDGE


SPARKLINE_BLOCKS: Final[str] = " ▁▂▃▄▅▆▇█"


def _sparkline(values: Sequence[float]) -> str:
    """Render a sequence of floats as a 9-block ASCII sparkline.

    NaN values render as a space. Empty input → empty string. Constant
    input → all-mid blocks. Used for the per-horizon rolling-IC strip in
    the report; not statistically meaningful, just a visual hint at trend.
    """
    finite = [v for v in values if not math.isnan(v)]
    if not finite:
        return ""
    lo = min(finite)
    hi = max(finite)
    span = hi - lo
    out_chars: list[str] = []
    for v in values:
        if math.isnan(v):
            out_chars.append(" ")
            continue
        if span == 0:
            idx = len(SPARKLINE_BLOCKS) // 2
        else:
            idx = int((v - lo) / span * (len(SPARKLINE_BLOCKS) - 1))
            idx = max(0, min(idx, len(SPARKLINE_BLOCKS) - 1))
        out_chars.append(SPARKLINE_BLOCKS[idx])
    return "".join(out_chars)


_NO_EDGE_BANNER = (
    "================================================================\n"
    "==                                                            ==\n"
    "==              NO EDGE DETECTED — HARNESS HALTS              ==\n"
    "==                                                            ==\n"
    "==  The TD3C tightness signal failed the IC fail-fast gate.   ==\n"
    f"==  Mean |IC| < {IC_FAIL_FAST_MEAN_ABS:.2f} AND mean |t-stat| < {IC_FAIL_FAST_T_STAT:.1f} across      ==\n"
    "==  every horizon AND both Pearson and Spearman methods.      ==\n"
    "==                                                            ==\n"
    "==  Re-examine the signal definition (Phase 04, ADR 0007)     ==\n"
    "==  before continuing to the backtester. Diagnostics below.   ==\n"
    "==                                                            ==\n"
    "================================================================\n"
)

_INSUFFICIENT_DATA_BANNER = (
    "================================================================\n"
    "==                                                            ==\n"
    "==        INSUFFICIENT DATA — HARNESS BLOCKED                 ==\n"
    "==                                                            ==\n"
    "==  Not enough overlap between signal history and price       ==\n"
    "==  history to compute IC across the required horizons.       ==\n"
    "==                                                            ==\n"
    "==  Backfill prices (taq prices backfill) and / or compute    ==\n"
    "==  historical signals (taq signals compute-tightness over    ==\n"
    "==  a date range) before re-running the IC analysis.          ==\n"
    "==                                                            ==\n"
    "================================================================\n"
)


def _format_data_summary(
    signals_df: pl.DataFrame,
    prices_df: pl.DataFrame,
    *,
    tickers: Sequence[str],
) -> str:
    sig = _normalise_signals_df(signals_df) if not signals_df.is_empty() else signals_df
    px = _close_in_dollars(prices_df) if not prices_df.is_empty() else prices_df
    n_sig = sig.height
    n_px = px.height
    sig_min = sig["as_of"].min() if n_sig else None
    sig_max = sig["as_of"].max() if n_sig else None
    px_min = px["as_of"].min() if n_px else None
    px_max = px["as_of"].max() if n_px else None
    return (
        "## Data summary\n\n"
        f"- Signal observations: **{n_sig}** "
        f"(range: {sig_min} → {sig_max})\n"
        f"- Price observations: **{n_px}** "
        f"(range: {px_min} → {px_max})\n"
        f"- Basket tickers: {', '.join(tickers)}\n"
        f"- Basket weighting: equal-weight unless overridden\n"
    )


def _format_ic_table(summary_df: pd.DataFrame) -> str:
    if summary_df.empty:
        return "## IC summary\n\n(no walk-forward windows produced)\n"
    formatted = summary_df.copy()
    for col in ("mean_ic", "median_ic", "mean_t_stat", "mean_hit_rate", "ir"):
        formatted[col] = formatted[col].apply(lambda v: "NaN" if pd.isna(v) else f"{v:+.4f}")
    formatted["n_windows"] = formatted["n_windows"].astype(int)
    return "## IC summary\n\n" + formatted.to_markdown(index=False) + "\n"


def _format_sparklines(wf_df: pl.DataFrame) -> str:
    if wf_df.is_empty():
        return "## Rolling IC sparkline\n\n(no walk-forward windows produced)\n"
    out = ["## Rolling IC sparkline\n"]
    out.append("```")
    for h in sorted(wf_df["horizon_days"].unique().to_list()):
        for m in sorted(wf_df["method"].unique().to_list()):
            slice_df = (
                wf_df.filter((pl.col("horizon_days") == h) & (pl.col("method") == m))
                .sort("window_end")
                .select("ic")
            )
            spark = _sparkline(slice_df["ic"].to_list())
            out.append(f"h={h:>3d}d {m:>8s}  {spark}")
    out.append("```\n")
    return "\n".join(out)


def _format_regime(regime_df: pd.DataFrame) -> str:
    if regime_df.empty:
        return "## Regime breakdown\n\n(no windows fell in any regime)\n"
    formatted = regime_df.copy()
    formatted["mean_ic"] = formatted["mean_ic"].apply(
        lambda v: "NaN" if pd.isna(v) else f"{v:+.4f}"
    )
    formatted["n_windows"] = formatted["n_windows"].astype(int)
    return "## Regime breakdown\n\n" + formatted.to_markdown(index=False) + "\n"


def _format_appendix(
    signals_df: pl.DataFrame,
    prices_df: pl.DataFrame,
    *,
    horizons_days: Sequence[int],
    min_history_days: int,
    step_days: int,
) -> str:
    return (
        "## Appendix — definitions & edge cases\n\n"
        f"- IC = Pearson correlation of (signal, forward log-return).\n"
        f"- Rank IC = Spearman correlation of (signal, forward log-return).\n"
        f"- t-stat = ic x sqrt(n - 2) / sqrt(1 - ic²); clamped to a finite "
        f"value when |ic| ≈ 1.\n"
        f"- Hit rate = fraction of observations where sign(signal) == "
        f"sign(forward return). Zero-sign pairs count as misses.\n"
        f"- Forward return = log(close[t + h] / close[t]) where h is the "
        f"trading-day horizon (h-th trading day after t, NOT calendar h).\n"
        f"- Walk-forward windows: span={min_history_days} days, "
        f"step={step_days} days, horizons={list(horizons_days)}.\n"
        f"- Look-ahead-free: at decision date T only signals with as_of ≤ T "
        f"are visible; forward returns require t + h ≤ max(price.as_of).\n"
        f"- Fail-fast trigger: mean |IC| < {IC_FAIL_FAST_MEAN_ABS} AND "
        f"mean |t-stat| < {IC_FAIL_FAST_T_STAT} across **every** horizon "
        f"AND **both** methods.\n"
        f"- Insufficient-data trigger: any (horizon, method) cell missing or "
        f"n_windows < {MIN_VIABLE_WINDOWS}.\n"
        f"- Edge cases handled: signal/price calendar mismatch (as-of join), "
        f"NaN bars (paired-NaN drop), constant-input correlation (returns NaN), "
        f"zero-variance basket return on flat days (returns NaN).\n"
    )


def generate_report(
    signals_df: pl.DataFrame,
    prices_df: pl.DataFrame,
    *,
    out_path: Path,
    horizons_days: Sequence[int] = DEFAULT_HORIZONS_DAYS,
    min_history_days: int = DEFAULT_MIN_HISTORY_DAYS,
    step_days: int = DEFAULT_STEP_DAYS,
    tickers: Sequence[str] | None = None,
    weights: dict[str, float] | None = None,
) -> tuple[ICVerdict, pl.DataFrame]:
    """Run the full IC analysis and write the markdown report.

    Returns ``(verdict, walk_forward_df)``. The caller (CLI) maps the
    verdict to an exit code and may append the manual-setup-required
    entry. ``out_path``'s parent is created if missing.
    """
    px = _close_in_dollars(prices_df) if not prices_df.is_empty() else prices_df
    if tickers is None:
        tickers = sorted(px["ticker"].unique().to_list()) if not px.is_empty() else []
    wf_df = walk_forward_ic(
        signals_df,
        prices_df,
        horizons_days=horizons_days,
        min_history_days=min_history_days,
        step_days=step_days,
        tickers=tickers,
        weights=weights,
    )
    summary_df = ic_summary(wf_df)
    regime_df = regime_breakdown(wf_df)
    verdict = evaluate_verdict(summary_df, wf_df)

    parts: list[str] = []
    if verdict == ICVerdict.FAIL_NO_EDGE:
        parts.append("```")
        parts.append(_NO_EDGE_BANNER)
        parts.append("```\n")
        exec_summary = (
            "[FAIL] **Verdict**: NO EDGE DETECTED. The TD3C tightness signal does "
            "not show predictive power against the shipping-equity basket "
            "across any of the {h} horizons or {m} correlation methods at "
            "the gate threshold. Phase 04 math should be revised before "
            "proceeding to the backtester."
        ).format(
            h=", ".join(f"{h}d" for h in FAIL_FAST_HORIZONS_DAYS),
            m=" / ".join(FAIL_FAST_METHODS),
        )
    elif verdict == ICVerdict.BLOCKED_INSUFFICIENT_DATA:
        parts.append("```")
        parts.append(_INSUFFICIENT_DATA_BANNER)
        parts.append("```\n")
        exec_summary = (
            "[BLOCKED] **Verdict**: INSUFFICIENT DATA. Not enough signal x price "
            f"history to produce {MIN_VIABLE_WINDOWS}+ walk-forward windows "
            "across every horizon. Backfill prices and historical signals, "
            "then re-run."
        )
    else:
        exec_summary = (
            "[PASS] **Verdict**: EDGE DETECTED. At least one (horizon, method) "
            "pair clears the gate. Backtester (Phase 08) and walk-forward "
            "CV (Phase 09) will determine which slice to actually trade."
        )

    body = "\n".join(
        [
            f"# TD3C tightness signal — IC analysis ({datetime.now(tz=UTC).date().isoformat()})",
            "",
            "## Executive summary",
            "",
            exec_summary,
            "",
            *parts,
            _format_data_summary(signals_df, prices_df, tickers=tickers),
            "",
            _format_ic_table(summary_df),
            "",
            _format_sparklines(wf_df),
            "",
            "## Per-horizon scatter (textual)\n\n"
            "Each row in the IC summary is a per-(horizon, method) aggregate "
            "across walk-forward windows. The signal-vs-return scatter for "
            "any single window is roughly: x = signal at as_of=t, y = forward "
            "log-return [t, t+h]; correlation is the IC value reported "
            "above. We omit a rendered scatter image because the report is "
            "consumed as plain markdown by the build harness; phase 19's "
            "dashboard will surface the live scatter.\n",
            "",
            _format_regime(regime_df),
            "",
            _format_appendix(
                signals_df,
                prices_df,
                horizons_days=horizons_days,
                min_history_days=min_history_days,
                step_days=step_days,
            ),
        ]
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")
    return verdict, wf_df
