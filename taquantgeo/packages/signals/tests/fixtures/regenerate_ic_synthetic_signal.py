"""Regenerate ``ic_synthetic_signal.parquet``.

Deterministic: re-running this script must produce a byte-identical parquet.
The fixture is the noise input for ``test_fail_fast_trigger_when_all_horizons_flat`` —
a seeded random signal over a 4-year daily calendar that is, by construction,
independent of any equity-return series the test pairs it with.

How the test consumes this:
- Build a matching seeded-random equity price series in the test (also
  independent of the signal).
- Feed both into ``walk_forward_ic`` + ``evaluate_verdict``.
- Assert verdict == ICVerdict.FAIL_NO_EDGE.

Why a parquet (vs in-test ``np.random.default_rng``):
- Pinning the bytes makes the test resilient to numpy RNG-stream changes
  across versions (numpy has changed its default BitGenerator twice).
- A reader can inspect the fixture with ``pl.read_parquet`` and re-derive
  the expected verdict by hand without running pytest.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl

OUT_PATH = Path(__file__).resolve().parent / "ic_synthetic_signal.parquet"

# A 4-year span starting 2020-01-01 covers all three regime windows
# (covid_2020, russia_2022, red_sea_2023) so the regime-breakdown test
# can also lean on this fixture without needing extra setup.
START = date(2020, 1, 1)
N_DAYS = 365 * 4 + 1  # 4 years + leap-day padding


def _build() -> pl.DataFrame:
    rng = np.random.default_rng(seed=20260421)
    dates = [START + timedelta(days=i) for i in range(N_DAYS)]
    # Centred around 0 with stdev 1 — magnitude is irrelevant for IC; what
    # matters is that the signal is uncorrelated with the test's price stream.
    signal = rng.standard_normal(N_DAYS)
    return pl.DataFrame({"as_of": dates, "signal": signal.tolist()})


def main() -> None:
    df = _build()
    df.write_parquet(OUT_PATH)
    print(f"wrote {OUT_PATH} ({df.height} rows)")


if __name__ == "__main__":
    main()
