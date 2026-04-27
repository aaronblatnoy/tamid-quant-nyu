"""taquantgeo_signals — tightness signal computation and comparison vs market.

Public surface:
- ``compute_daily_tightness(as_of, *, ...) -> TightnessSnapshot``
- ``TightnessSnapshot`` frozen dataclass
- ``upsert_snapshot(session, snapshot)``

The IC analysis surface (``compare``: walk-forward IC, basket returns, verdict/report) is re-exported below; ADR 0009 documents the fail-fast gate rationale.

Signal math is fully specified in ``docs/adrs/0007-tightness-signal-definition.md``
and the module docstring of ``tightness.py``. That ADR is the binding contract;
any change to the math requires a new ADR.
"""

from taquantgeo_signals.compare import (
    DEFAULT_HORIZONS_DAYS,
    DEFAULT_MIN_HISTORY_DAYS,
    DEFAULT_STEP_DAYS,
    FAIL_FAST_HORIZONS_DAYS,
    FAIL_FAST_METHODS,
    IC_FAIL_FAST_MEAN_ABS,
    IC_FAIL_FAST_T_STAT,
    MIN_VIABLE_WINDOWS,
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
from taquantgeo_signals.persistence import upsert_snapshot
from taquantgeo_signals.tightness import (
    BALLAST_NOMINAL_SOG_KNOTS,
    DARK_FLEET_WINDOW_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_SUPPLY_HORIZON_DAYS,
    MIN_Z_SCORE_SAMPLE,
    ROUTE_NOMINAL_DWT,
    TightnessSnapshot,
    compute_daily_tightness,
)

__version__ = "0.0.1"

__all__ = [
    "BALLAST_NOMINAL_SOG_KNOTS",
    "DARK_FLEET_WINDOW_DAYS",
    "DEFAULT_HORIZONS_DAYS",
    "DEFAULT_LOOKBACK_DAYS",
    "DEFAULT_MIN_HISTORY_DAYS",
    "DEFAULT_STEP_DAYS",
    "DEFAULT_SUPPLY_HORIZON_DAYS",
    "FAIL_FAST_HORIZONS_DAYS",
    "FAIL_FAST_METHODS",
    "IC_FAIL_FAST_MEAN_ABS",
    "IC_FAIL_FAST_T_STAT",
    "MIN_VIABLE_WINDOWS",
    "MIN_Z_SCORE_SAMPLE",
    "ROUTE_NOMINAL_DWT",
    "ICResult",
    "ICVerdict",
    "TightnessSnapshot",
    "basket_return",
    "compute_daily_tightness",
    "compute_ic",
    "evaluate_verdict",
    "generate_report",
    "ic_summary",
    "regime_breakdown",
    "upsert_snapshot",
    "walk_forward_ic",
]
