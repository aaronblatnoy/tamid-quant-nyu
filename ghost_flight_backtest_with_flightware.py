"""
Ghost Flight Signal Strategy — Backtest
TAMID Quant Club

NOTE ON DATA:
  Real ghost flight data (FlightAware API) is expensive and complex.
  For this backtest, we use a PROXY SIGNAL: rolling underperformance
  of legacy carriers (AAL, UAL, DAL) relative to their own 90-day history.
  Logic: when legacy carriers are under revenue pressure, they are more
  likely to run defensive ghost flights on ULCC-competing routes.
  
  This is a proof-of-concept to test whether the entry/exit framework
  produces alpha — not a production-ready signal.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


# ── 1. PARAMETERS (all strategy knobs in one place) ──────────────────────────

START_DATE        = "2021-01-01"   # ULCC IPO was Nov 2021; using 2021 for synthetic range
END_DATE          = "2024-12-31"
LOOKBACK_DAYS     = 90             # rolling window for z-score baseline
ENTRY_Z           = 1.5            # CSI z-score to enter long
SCALE_Z           = 2.0            # CSI z-score to scale to 1.5x
EXIT_Z            = 0.5            # CSI z-score to exit
SUSTAIN_DAYS      = 3              # signal must be sustained for N days before entry
STOP_LOSS         = -0.08          # hard stop: exit if position drops 8% from entry
INITIAL_CAPITAL   = 100_000        # starting portfolio value in USD

RANDOM_SEED       = 42             # for reproducibility


# ── 2. GENERATE PRICE DATA ─────────────────────────────────────────

import yfinance as yf
raw = yf.download("ULCC AAL UAL DAL", start=START_DATE, end=END_DATE)
prices = raw["Close"].dropna()


# ── 2. GENERATE PRICE DATA & GHOST FLIGHT DATASET ─────────────────────────────
#
# ARCHITECTURE NOTE:
#   The stress_signal array is the shared "ground truth" for this synthetic backtest.
#   It drives BOTH the simulated price data (same as before) AND the ghost flight
#   load factors below — ensuring the two data sources are self-consistent.
#
#   In production, you drop stress_signal entirely and replace generate_ghost_flight_data()
#   with a real FlightAware / OAG pull. The price data then comes from yfinance as-is.
#   The two data streams are then genuinely independent, which is the whole point.

# ── 2a. STRESS SIGNAL (synthetic engine — production: delete this block) ────────
np.random.seed(RANDOM_SEED)
date_range = pd.bdate_range(start=START_DATE, end=END_DATE)
n = len(date_range)

market_shock = np.random.normal(0, 0.012, n)

stress_signal = np.zeros(n)
stress_starts = np.random.choice(
    range(LOOKBACK_DAYS, n - 30),
    size=int(n / 65),
    replace=False
)
for s in stress_starts:
    duration  = np.random.randint(10, 22)
    intensity = np.random.uniform(0.008, 0.020)
    stress_signal[s:s + duration] = -intensity

# ── 2b. SIMULATED PRICES (unchanged logic from original) ────────────────────────
daily_vol   = {"ULCC": 0.030, "AAL": 0.028, "UAL": 0.025, "DAL": 0.024}
daily_drift = {"ULCC": 0.0001, "AAL": -0.0002, "UAL": 0.0001, "DAL": 0.0001}

returns = {}
for ticker in ["ULCC", "AAL", "UAL", "DAL"]:
    idio = np.random.normal(daily_drift[ticker], daily_vol[ticker], n)
    if ticker != "ULCC":
        returns[ticker] = idio + 0.6 * market_shock + stress_signal
    else:
        returns[ticker] = idio + 0.2 * market_shock - 0.3 * stress_signal

start_prices = {"ULCC": 18.0, "AAL": 17.0, "UAL": 45.0, "DAL": 38.0}
prices = {}
for ticker in ["ULCC", "AAL", "UAL", "DAL"]:
    p = [start_prices[ticker]]
    for r in returns[ticker][1:]:
        p.append(p[-1] * (1 + r))
    prices[ticker] = p
prices = pd.DataFrame(prices, index=date_range)

print(f"  Date range: {prices.index[0].date()} → {prices.index[-1].date()}")
print(f"  Trading days: {len(prices)}\n")

# ── 2c. GHOST FLIGHT DATASET ─────────────────────────────────────────────────
#
# SCHEMA (matches what a real FlightAware AeroAPI / OAG pull would return):
#   date        — trading date
#   carrier     — IATA carrier code (AA, UA, DL)
#   route       — origin-destination string (e.g. 'ATL-MCO')
#   flights     — departures operated that day on that route by that carrier
#   load_factor — estimated seat fill rate [0.0, 1.0]
#                 source in production: DOT T-100 monthly actuals or OAG seat
#                 capacity vs. booked-passenger estimates from third-party scrapers
#
# GHOST FLIGHT DEFINITION:
#   A legacy carrier departure on a ULCC-competing route where load_factor
#   falls below GHOST_LF_THRESHOLD. The economic logic: a carrier knowingly
#   flying near-empty planes is either defending slot rights or flooding
#   the market to suppress ULCC yield — both are bearish for ULCC.
#
# PRODUCTION SWAP:
#   Delete generate_ghost_flight_data() and replace the call below with:
#       ghost_df = pd.read_parquet("flightaware_export.parquet")
#   or an API call. Ensure the returned DataFrame has the five columns above.

GHOST_LF_THRESHOLD = 0.40    # load factor below this → ghost flight candidate
GHOST_WINDOW       = 30      # rolling-day window for ghost flight count in CSI

# Routes where Spirit / Frontier overlap most heavily with legacy carriers
# (sourced from DOT T-100 segment data — top overlap by seat share)
ULCC_ROUTES = [
    "ATL-MCO", "ORD-MCO", "DFW-MCO", "LAX-LAS", "DFW-LAS",
    "ATL-FLL", "ORD-FLL", "JFK-FLL", "DFW-FLL", "LAX-FLL",
    "ATL-TPA", "ORD-TPA", "DFW-TPA", "LAX-TPA", "JFK-TPA",
    "ORD-PHX", "DFW-PHX", "ATL-PHX", "LAX-PHX", "JFK-PHX",
    "ORD-LAS", "ATL-LAS", "JFK-LAS", "MIA-LGA", "DFW-MSY",
]
CARRIERS = ["AA", "UA", "DL"]


def generate_ghost_flight_data(date_range, stress_signal_arr):
    """
    Synthetic ghost flight dataset — mirrors a real FlightAware export row-for-row.

    Mechanism:
      During stress periods (stress_signal_arr < 0), legacy load factors fall.
      When they dip below GHOST_LF_THRESHOLD the flight is flagged as a ghost
      flight. This is the real-world mechanism: revenue-pressured carriers fly
      defensively near-empty rather than cede route presence to ULCC.

    In production: replace this entire function body with a data loader.
    The function signature can stay identical so downstream code is untouched.
    """
    records = []
    BASE_LF        = 0.78   # typical legacy load factor on leisure routes (~industry avg)
    STRESS_LF_DRAG = 8.0    # each unit of stress intensity costs up to 8 pct pts of LF

    for i, date in enumerate(date_range):
        stress_magnitude = abs(stress_signal_arr[i])  # 0 on calm days, >0 during pressure

        for carrier in CARRIERS:
            for route in ULCC_ROUTES:
                n_flights = np.random.randint(2, 7)           # 2–6 departures / day / route

                lf = (BASE_LF
                      - stress_magnitude * STRESS_LF_DRAG     # ghost-flight pressure drag
                      + np.random.normal(0, 0.05))            # day-to-day stochastic noise
                lf = float(np.clip(lf, 0.05, 1.0))

                records.append({
                    "date"        : date,
                    "carrier"     : carrier,
                    "route"       : route,
                    "flights"     : n_flights,
                    "load_factor" : round(lf, 3),
                })

    return pd.DataFrame(records)


ghost_df = generate_ghost_flight_data(date_range, stress_signal)

print(f"  Ghost flight records generated: {len(ghost_df):,}")
print(f"  Ghost flights (LF < {GHOST_LF_THRESHOLD}): "
      f"{(ghost_df['load_factor'] < GHOST_LF_THRESHOLD).sum():,}\n")


# ── 3. BUILD THE GHOST FLIGHT CSI ─────────────────────────────────────────────
#
# Signal construction:
#   Step 1 — Filter to ghost flights (load_factor < threshold)
#   Step 2 — Aggregate to a single daily ghost flight count across all
#             routes and carriers (we care about total market-wide pressure,
#             not any one carrier or route individually)
#   Step 3 — Rolling GHOST_WINDOW-day sum: captures *sustained* ghost flight
#             activity rather than reacting to isolated one-day spikes
#   Step 4 — Z-score that rolling count against its own LOOKBACK_DAYS history
#             to normalise for seasonality (airlines fly more in summer regardless)
#
# High CSI z-score = unusually elevated ghost flight activity on ULCC routes
#                  = elevated competitive suppression pressure on ULCC
#                  = our long entry signal

ghost_flights = ghost_df[ghost_df["load_factor"] < GHOST_LF_THRESHOLD].copy()

# Daily total ghost flight count: how many qualifying departures happened system-wide
daily_ghost_count = (
    ghost_flights
    .groupby("date")["flights"]
    .sum()
    .reindex(date_range, fill_value=0)
    .astype(float)
)

# Rolling sum over GHOST_WINDOW days — sustained pressure matters more than spikes
ghost_rolling = daily_ghost_count.rolling(window=GHOST_WINDOW).sum()

# Z-score vs. own 90-day history: removes seasonal baseline, isolates *unusual* activity
rolling_mean = ghost_rolling.rolling(window=LOOKBACK_DAYS).mean()
rolling_std  = ghost_rolling.rolling(window=LOOKBACK_DAYS).std()

CSI = (ghost_rolling - rolling_mean) / rolling_std
CSI = CSI.dropna()

# ── 4. GENERATE TRADE SIGNALS ─────────────────────────────────────────────────
#
# Entry: CSI z-score > ENTRY_Z for SUSTAIN_DAYS consecutive days
# Scale: CSI z-score > SCALE_Z → position size = 1.5x
# Exit:  CSI z-score reverts below EXIT_Z  OR  ULCC drops 8% from entry

ulcc = prices["ULCC"].reindex(CSI.index)

# Track state across each trading day
in_position    = False
entry_price    = 0.0
position_size  = 1.0     # 1.0 = base, 1.5 = scaled
days_above     = 0       # consecutive days CSI has been above ENTRY_Z
cash           = INITIAL_CAPITAL
portfolio_val  = INITIAL_CAPITAL
shares_held    = 0.0

# Lists to record daily portfolio value and trade log for charts
dates_list     = []
portfolio_list = []
trade_log      = []      # list of dicts: {date, action, price, z_score, pnl}

for date, z in CSI.items():

    ulcc_price = ulcc.get(date)
    if pd.isna(ulcc_price):
        continue

    # ── When NOT in a position ─────────────────────────────────────────────
    if not in_position:
        if z > ENTRY_Z:
            days_above += 1
        else:
            days_above = 0

        # Enter when signal has been sustained for SUSTAIN_DAYS
        if days_above >= SUSTAIN_DAYS:
            position_size = 1.5 if z > SCALE_Z else 1.0
            invest_amount = cash * position_size   # note: capped at cash if size=1.0
            invest_amount = min(invest_amount, cash)
            shares_held   = invest_amount / ulcc_price
            cash         -= shares_held * ulcc_price
            entry_price   = ulcc_price
            in_position   = True
            days_above    = 0
            trade_log.append({
                "date": date, "action": "BUY",
                "price": round(ulcc_price, 2),
                "z_score": round(z, 2),
                "size": position_size,
                "pnl": None
            })

    # ── When IN a position ─────────────────────────────────────────────────
    else:
        position_value = shares_held * ulcc_price
        pct_change     = (ulcc_price - entry_price) / entry_price

        # Check stop-loss first
        hit_stop = pct_change <= STOP_LOSS
        # Check signal exit
        signal_exit = z < EXIT_Z

        if hit_stop or signal_exit:
            reason = "STOP-LOSS" if hit_stop else "EXIT (z reversion)"
            pnl    = (ulcc_price - entry_price) * shares_held
            cash  += shares_held * ulcc_price
            trade_log.append({
                "date": date, "action": reason,
                "price": round(ulcc_price, 2),
                "z_score": round(z, 2),
                "size": position_size,
                "pnl": round(pnl, 2)
            })
            shares_held  = 0.0
            in_position  = False
            position_size = 1.0

    # Daily portfolio value = cash + current position value
    portfolio_val = cash + (shares_held * ulcc_price if in_position else 0)
    dates_list.append(date)
    portfolio_list.append(portfolio_val)

# Close any open position at the final price
if in_position:
    final_price = ulcc.iloc[-1]
    pnl = (final_price - entry_price) * shares_held
    cash += shares_held * final_price
    trade_log.append({
        "date": CSI.index[-1], "action": "CLOSE (end of backtest)",
        "price": round(final_price, 2),
        "z_score": None,
        "size": position_size,
        "pnl": round(pnl, 2)
    })
    portfolio_val = cash


# ── 5. CALCULATE PERFORMANCE STATS ───────────────────────────────────────────

portfolio_series = pd.Series(portfolio_list, index=pd.DatetimeIndex(dates_list))

# Filter just the completed round trips (buy + exit pairs)
completed_trades = [t for t in trade_log if t["pnl"] is not None]
winning_trades   = [t for t in completed_trades if t["pnl"] > 0]
losing_trades    = [t for t in completed_trades if t["pnl"] <= 0]

total_return     = (portfolio_val - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
num_trades       = len(completed_trades)
win_rate         = len(winning_trades) / num_trades * 100 if num_trades > 0 else 0
avg_win          = sum(t["pnl"] for t in winning_trades)  / max(len(winning_trades), 1)
avg_loss         = sum(t["pnl"] for t in losing_trades)   / max(len(losing_trades),  1)

# Max drawdown
rolling_max      = portfolio_series.cummax()
drawdown_series  = (portfolio_series - rolling_max) / rolling_max * 100
max_drawdown     = drawdown_series.min()

# Buy-and-hold ULCC benchmark
ulcc_aligned     = ulcc.reindex(portfolio_series.index).dropna()
bah_return       = (ulcc_aligned.iloc[-1] - ulcc_aligned.iloc[0]) / ulcc_aligned.iloc[0] * 100

print("=" * 50)
print("  BACKTEST RESULTS")
print("=" * 50)
print(f"  Period:          {START_DATE} → {END_DATE}")
print(f"  Total return:    {total_return:+.1f}%")
print(f"  Buy & hold ULCC: {bah_return:+.1f}%")
print(f"  Max drawdown:    {max_drawdown:.1f}%")
print(f"  Number of trades:{num_trades}")
print(f"  Win rate:        {win_rate:.0f}%")
print(f"  Avg win ($):     ${avg_win:,.0f}")
print(f"  Avg loss ($):    ${avg_loss:,.0f}")
print("=" * 50)

print("\nTRADE LOG:")
for t in trade_log:
    pnl_str = f"  PnL: ${t['pnl']:+,.0f}" if t["pnl"] is not None else ""
    z_str   = f"  z={t['z_score']}" if t["z_score"] is not None else ""
    print(f"  {str(t['date'])[:10]}  {t['action']:<25} @ ${t['price']}{z_str}{pnl_str}")


# ── 6. CHARTS ─────────────────────────────────────────────────────────────────

fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
fig.suptitle("Ghost Flight Signal Strategy — Backtest", fontsize=14, fontweight="bold")

# Panel 1: Portfolio value vs buy-and-hold
ax1 = axes[0]
ax1.plot(portfolio_series.index, portfolio_series / INITIAL_CAPITAL * 100,
         color="#2196F3", linewidth=1.5, label="Strategy")
bah_normalised = (ulcc_aligned / ulcc_aligned.iloc[0]) * 100
ax1.plot(bah_normalised.index, bah_normalised,
         color="#FF9800", linewidth=1.2, linestyle="--", label="ULCC Buy & Hold")
ax1.axhline(100, color="grey", linewidth=0.6, linestyle=":")
ax1.set_ylabel("Portfolio Value (indexed to 100)")
ax1.legend(fontsize=9)
ax1.set_title("Portfolio Performance vs. Buy & Hold")

# Panel 2: Competitor Stress Index (CSI)
ax2 = axes[1]
ax2.plot(CSI.index, CSI, color="#9C27B0", linewidth=1.0, alpha=0.8, label="CSI (z-score)")
ax2.axhline(ENTRY_Z, color="green",  linewidth=1.0, linestyle="--", label=f"Entry z={ENTRY_Z}")
ax2.axhline(SCALE_Z, color="blue",   linewidth=1.0, linestyle="--", label=f"Scale z={SCALE_Z}")
ax2.axhline(EXIT_Z,  color="red",    linewidth=1.0, linestyle="--", label=f"Exit z={EXIT_Z}")
ax2.axhline(0, color="grey", linewidth=0.5, linestyle=":")
ax2.set_ylabel("CSI z-score")
ax2.legend(fontsize=8, ncol=4)
ax2.set_title("Competitor Stress Index (Proxy Signal)")

# Panel 3: Drawdown
ax3 = axes[2]
ax3.fill_between(drawdown_series.index, drawdown_series, 0,
                 color="#F44336", alpha=0.5, label="Drawdown")
ax3.set_ylabel("Drawdown (%)")
ax3.set_xlabel("Date")
ax3.legend(fontsize=9)
ax3.set_title("Strategy Drawdown")
ax3.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

plt.tight_layout()
plt.savefig("/Users/arthurmonastyrsky/Downloads/ghost_flight_backtest.png", dpi=150, bbox_inches="tight")
print("\nChart saved.")
