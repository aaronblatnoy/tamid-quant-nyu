"""CLI commands for the tightness signal."""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path  # noqa: TC003, RUF100 — typer resolves Path annotations at runtime
from typing import Annotated

import polars as pl
import typer
from sqlalchemy import select

from taquantgeo_core.config import settings
from taquantgeo_core.db import session_scope
from taquantgeo_jobs.daily_signal import (
    DEFAULT_BALLAST_VOYAGES_DIR,
    DEFAULT_DARK_FLEET_PATH,
    DEFAULT_DISTANCE_CACHE_PATH,
    DEFAULT_REGISTRY_PATH,
    DEFAULT_VOYAGES_DIR,
)
from taquantgeo_jobs.daily_signal import run_once as run_daily_signal_once
from taquantgeo_prices.models import Price
from taquantgeo_signals.compare import (
    DEFAULT_HORIZONS_DAYS,
    DEFAULT_MIN_HISTORY_DAYS,
    DEFAULT_STEP_DAYS,
    ICVerdict,
    generate_report,
)
from taquantgeo_signals.models import Signal

signals_app = typer.Typer(
    name="signals",
    help="Tightness signal computation and persistence.",
    no_args_is_help=True,
)


@signals_app.command("compute-tightness")
def compute_tightness(
    as_of: Annotated[
        str,
        typer.Option(help="Trading day YYYY-MM-DD. Default: today UTC."),
    ] = "",
    route: Annotated[
        str,
        typer.Option(help="Route key (td3c). Only td3c supported in v0."),
    ] = "td3c",
    voyages_dir: Annotated[
        Path,
        typer.Option(help="Route-partitioned voyages parquet tree."),
    ] = DEFAULT_VOYAGES_DIR,
    registry_path: Annotated[
        Path,
        typer.Option(help="Vessel registry parquet (from classify-vessels)."),
    ] = DEFAULT_REGISTRY_PATH,
    distance_cache_path: Annotated[
        Path,
        typer.Option(help="Distance cache parquet (from compute-distances)."),
    ] = DEFAULT_DISTANCE_CACHE_PATH,
    dark_fleet_path: Annotated[
        Path,
        typer.Option(help="Dark-fleet candidates parquet (from ingest-sar)."),
    ] = DEFAULT_DARK_FLEET_PATH,
    ballast_voyages_dir: Annotated[
        Path,
        typer.Option(help="Ballast (td3c_ballast) voyages tree. Defaults to same tree as voyages."),
    ] = DEFAULT_BALLAST_VOYAGES_DIR,
    persist: Annotated[
        bool,
        typer.Option(
            "--persist/--no-persist",
            help="Upsert the snapshot to Postgres via DATABASE_URL.",
        ),
    ] = False,
) -> None:
    """Compute one tightness snapshot for as_of; print JSON; optionally persist."""
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    target = date.fromisoformat(as_of) if as_of else datetime.now(tz=UTC).date()
    snap = run_daily_signal_once(
        target,
        route=route,
        voyages_dir=voyages_dir,
        registry_path=registry_path,
        distance_cache_path=distance_cache_path,
        dark_fleet_path=dark_fleet_path,
        ballast_voyages_dir=ballast_voyages_dir,
        persist=persist,
    )
    typer.echo(
        json.dumps(
            {
                "as_of": snap.as_of.isoformat(),
                "route": snap.route,
                "forward_demand_ton_miles": snap.forward_demand_ton_miles,
                "forward_supply_count": snap.forward_supply_count,
                "dark_fleet_supply_adjustment": snap.dark_fleet_supply_adjustment,
                "ratio": snap.ratio,
                "z_score_90d": snap.z_score_90d,
                "components": snap.components,
            },
            indent=2,
            default=str,
        )
    )
    if persist:
        typer.echo(f"Upserted snapshot for ({snap.as_of}, {snap.route})")


@signals_app.command("ic")
def ic_cmd(
    since: Annotated[
        str,
        typer.Option(help="First trading day to include, YYYY-MM-DD. Default: no lower bound."),
    ] = "",
    until: Annotated[
        str,
        typer.Option(help="Last trading day to include, YYYY-MM-DD. Default: no upper bound."),
    ] = "",
    out: Annotated[Path, typer.Option(help="Where to write the markdown report.")] = Path(
        "reports/ic_analysis.md"
    ),
    route: Annotated[str, typer.Option(help="Route key (only td3c in v0).")] = "td3c",
    horizon: Annotated[
        list[int] | None,
        typer.Option("--horizon", help="Repeatable; overrides default horizons (5 10 20)."),
    ] = None,
    min_history_days: Annotated[
        int,
        typer.Option(help="Minimum signal-history span (calendar days) per walk-forward window."),
    ] = DEFAULT_MIN_HISTORY_DAYS,
    step_days: Annotated[
        int,
        typer.Option(help="Calendar-day step between walk-forward decision dates."),
    ] = DEFAULT_STEP_DAYS,
    manual_setup_path: Annotated[
        Path,
        typer.Option(help="Where to append the manual-setup-required entry on a blocked verdict."),
    ] = Path(".build/manual_setup_required.md"),
) -> None:
    """Compute walk-forward IC of the tightness signal vs the equity basket; write report."""
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    horizons = list(horizon) if horizon else list(DEFAULT_HORIZONS_DAYS)
    since_d = date.fromisoformat(since) if since else None
    until_d = date.fromisoformat(until) if until else None

    with session_scope() as sess:
        sig_q = select(Signal.as_of, Signal.tightness, Signal.components).where(
            Signal.route == route
        )
        if since_d is not None:
            sig_q = sig_q.where(Signal.as_of >= since_d)
        if until_d is not None:
            sig_q = sig_q.where(Signal.as_of <= until_d)
        sig_rows = sess.execute(sig_q).all()
        # Filter out floored snapshots — phase 04 invariant: signals with
        # supply_floor_clamped == 1 are diagnostic only, not trade-quality.
        clean_sig_rows = [
            (r[0], float(r[1]))
            for r in sig_rows
            if (r[2] or {}).get("supply_floor_clamped", 0) == 0
        ]
        signals_df = (
            pl.DataFrame(
                {
                    "as_of": [r[0] for r in clean_sig_rows],
                    "tightness": [r[1] for r in clean_sig_rows],
                }
            )
            if clean_sig_rows
            else pl.DataFrame(schema={"as_of": pl.Date, "tightness": pl.Float64})
        )

        prc_q = select(Price.ticker, Price.as_of, Price.close_cents)
        if since_d is not None:
            prc_q = prc_q.where(Price.as_of >= since_d)
        # Note: prices are NOT filtered by until_d — forward returns at the upper signal-cutoff need horizon-days of buffer beyond it.
        prc_rows = sess.execute(prc_q).all()
        prices_df = (
            pl.DataFrame(
                {
                    "ticker": [r[0] for r in prc_rows],
                    "as_of": [r[1] for r in prc_rows],
                    "close_cents": [int(r[2]) for r in prc_rows],
                }
            )
            if prc_rows
            else pl.DataFrame(
                schema={"ticker": pl.String, "as_of": pl.Date, "close_cents": pl.Int64}
            )
        )

    typer.echo(f"signal observations: {signals_df.height} (post-floor filter)")
    typer.echo(f"price observations:  {prices_df.height}")

    verdict, _wf = generate_report(
        signals_df,
        prices_df,
        out_path=out,
        horizons_days=horizons,
        min_history_days=min_history_days,
        step_days=step_days,
    )
    typer.echo(f"verdict: {verdict.value}")
    typer.echo(f"report written to: {out}")

    if verdict in (ICVerdict.FAIL_NO_EDGE, ICVerdict.BLOCKED_INSUFFICIENT_DATA):
        manual_setup_path.parent.mkdir(parents=True, exist_ok=True)
        suffix = (
            f"\n## Phase 07 — IC fail-fast gate ({datetime.now(tz=UTC).date().isoformat()})\n\n"
            f"- Verdict: **{verdict.value}**\n"
            f"- Diagnostic: see `{out}`\n"
            + (
                "- Action: re-examine Phase 04 tightness math (ADR 0007) or signal "
                "thresholds before proceeding to backtester (Phase 08).\n"
                if verdict == ICVerdict.FAIL_NO_EDGE
                else "- Action: backfill prices (`taq prices backfill --since 2017-01-01`) and "
                "compute historical signals (`taq signals compute-tightness --as-of <date> "
                "--persist` over a date range) before re-running.\n"
            )
        )
        with manual_setup_path.open("a", encoding="utf-8") as f:
            f.write(suffix)
        raise typer.Exit(code=10)
