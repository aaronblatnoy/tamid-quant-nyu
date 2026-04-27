"""CLI commands for Global Fishing Watch historical data."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, get_args

import polars as pl
import typer

from taquantgeo_ais.gfw.anchorages import load_anchorages
from taquantgeo_ais.gfw.api import GfwClient
from taquantgeo_ais.gfw.classifier import (
    classify_vessels as classify_vessels_registry,
)
from taquantgeo_ais.gfw.classifier import (
    load_ais_static_lookup,
    read_vessel_ids_from_voyages,
)
from taquantgeo_ais.gfw.distance import compute_distances_cached
from taquantgeo_ais.gfw.events import DATASETS, EventKind, EventsClient, event_to_flat_row
from taquantgeo_ais.gfw.extract import extract_route
from taquantgeo_ais.gfw.routes import MAJOR_LOADING_TERMINALS, TD3C, TD3C_BALLAST, all_routes
from taquantgeo_ais.gfw.sar import (
    DEFAULT_BUFFER_KM,
    DEFAULT_TIME_WINDOW_DAYS,
    MIN_VESSEL_LENGTH_M,
    load_voyages_for_crossref,
)
from taquantgeo_ais.gfw.sar import ingest_sar as ingest_sar_pipeline
from taquantgeo_core.config import settings

_EVENT_KIND_CHOICES = tuple(get_args(EventKind))  # ("port_visit","gap","encounter","loitering")

# Accept human-friendly plural / hyphenated forms on the CLI. "port-visits" is
# what GFW's own portal uses; internally we keep the singular Literal.
_EVENT_KIND_ALIASES: dict[str, EventKind] = {
    "port_visit": "port_visit",
    "port-visit": "port_visit",
    "port_visits": "port_visit",
    "port-visits": "port_visit",
    "gap": "gap",
    "gaps": "gap",
    "encounter": "encounter",
    "encounters": "encounter",
    "loitering": "loitering",
}


def _parse_event_kind(raw: str) -> EventKind:
    resolved = _EVENT_KIND_ALIASES.get(raw.lower())
    if resolved is None:
        msg = f"unknown event kind {raw!r}; choices: {sorted(_EVENT_KIND_ALIASES)}"
        raise typer.BadParameter(msg)
    return resolved


def _get_token() -> str:
    token = settings.gfw_api_token or os.environ.get("GFW_API_TOKEN", "")
    if not token:
        typer.echo("GFW_API_TOKEN is not set in env / .env", err=True)
        raise typer.Exit(code=2)
    return token


gfw_app = typer.Typer(
    name="gfw",
    help="Global Fishing Watch historical data pipeline.",
    no_args_is_help=True,
)


@gfw_app.command("ingest-voyages")
def ingest_voyages(
    voyages_csv: Annotated[
        Path,
        typer.Option(help="GFW voyages monthly CSV (e.g. voyages_c4_pipe_v3_202603.csv)."),
    ],
    anchorages_csv: Annotated[
        Path,
        typer.Option(help="GFW named_anchorages CSV."),
    ] = Path("data/raw/gfw/anchorages/named_anchorages_v2_pipe_v3_202601.csv"),
    route: Annotated[
        str,
        typer.Option(help="Route key (td3c, td3c_ballast). Default: td3c."),
    ] = "td3c",
    out_dir: Annotated[
        Path,
        typer.Option(help="Where to write route-partitioned parquet."),
    ] = Path("data/processed/voyages"),
    no_duration_filter: Annotated[
        bool,
        typer.Option("--no-duration-filter", help="Skip the route-typical duration band filter."),
    ] = False,
) -> None:
    """Join voyages x anchorages, filter to the given route, write parquet."""
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    routes = all_routes()
    if route not in routes:
        typer.echo(
            f"unknown route {route!r}; available: {sorted(routes)}",
            err=True,
        )
        raise typer.Exit(code=2)

    df = extract_route(
        voyages_csv=voyages_csv,
        anchorages_csv=anchorages_csv,
        route=routes[route],
        out_dir=out_dir,
        apply_duration_filter=not no_duration_filter,
    )
    typer.echo(f"Extracted {df.shape[0]} voyages to {out_dir}/route={route}/")


@gfw_app.command("list-routes")
def list_routes() -> None:
    """Show available routes and their definitions."""
    for r in all_routes().values():
        typer.echo(f"{r.name}")
        typer.echo(f"  {r.description}")
        typer.echo(f"  origin: {sorted(r.origin_iso3)}")
        typer.echo(f"  destination: {sorted(r.destination_iso3)}")
        typer.echo(
            f"  typical transit: {r.typical_transit_days[0]}-{r.typical_transit_days[1]} days"
        )


_ = TD3C, TD3C_BALLAST  # ensure constants importable at module level for docs


@gfw_app.command("sample-events")
def sample_events(
    vessel_id: Annotated[
        str,
        typer.Option(help="GFW vessel_id (UUID-like) to probe."),
    ],
    event_type: Annotated[
        str,
        typer.Option(help="port_visit | gap | encounter | loitering (hyphens also accepted)."),
    ] = "port_visit",
    confidence: Annotated[
        int,
        typer.Option(help="Port-visit confidence tier (1-4). Ignored for non-port-visit kinds."),
    ] = 4,
    since: Annotated[
        str,
        typer.Option(help="Start date YYYY-MM-DD. Default: 90 days ago."),
    ] = "",
    until: Annotated[
        str,
        typer.Option(help="End date YYYY-MM-DD. Default: today UTC."),
    ] = "",
) -> None:
    """Print the first page of events for one vessel as JSON. Hits live API."""
    kind = _parse_event_kind(event_type)
    today = datetime.now(tz=UTC).date()
    start = date.fromisoformat(since) if since else today - timedelta(days=90)
    end = date.fromisoformat(until) if until else today
    token = _get_token()
    with EventsClient(token, page_size=10) as client:
        events = list(
            client.iter_events(
                [vessel_id],
                kind,
                start_date=start,
                end_date=end,
                confidences=(confidence,),
            )
        )
    # Cap output at 10 events — this is a probe, not a fetch.
    sample = [e.model_dump(mode="json") for e in events[:10]]
    payload = {
        "dataset": DATASETS[kind],
        "count": len(events),
        "sample": sample,
    }
    typer.echo(json.dumps(payload, indent=2, default=str))


@gfw_app.command("fetch-events")
def fetch_events(
    vessel_ids_file: Annotated[
        Path,
        typer.Option(
            help="Text file with one vessel_id per line (lines starting with # are ignored).",
        ),
    ],
    event_type: Annotated[
        str,
        typer.Option(help="port_visit | gap | encounter | loitering."),
    ] = "port_visit",
    since: Annotated[
        str,
        typer.Option(help="Start date YYYY-MM-DD (required)."),
    ] = "",
    until: Annotated[
        str,
        typer.Option(help="End date YYYY-MM-DD. Default: today UTC."),
    ] = "",
    confidence: Annotated[
        int,
        typer.Option(help="Port-visit confidence tier. Ignored for non-port-visit kinds."),
    ] = 4,
    out_dir: Annotated[
        Path,
        typer.Option(help="Output directory. Events are written one parquet per run."),
    ] = Path("data/processed/events"),
) -> None:
    """Fetch events for a list of vessels, write one batched parquet per run.

    Output path: data/processed/events/type=<kind>/year=YYYY/month=MM/events_<since>_<until>.parquet
    (year/month from --since). One file per run makes downstream joins easier
    than per-vessel files.
    """
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    kind = _parse_event_kind(event_type)
    if not since:
        typer.echo("--since is required", err=True)
        raise typer.Exit(code=2)
    start = date.fromisoformat(since)
    end = date.fromisoformat(until) if until else datetime.now(tz=UTC).date()

    vessel_ids = [
        stripped
        for line in vessel_ids_file.read_text().splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]
    if not vessel_ids:
        typer.echo(f"no vessel_ids in {vessel_ids_file}", err=True)
        raise typer.Exit(code=2)

    token = _get_token()
    rows: list[dict[str, object]] = []
    with EventsClient(token) as client:
        for ev in client.iter_events(
            vessel_ids,
            kind,
            start_date=start,
            end_date=end,
            confidences=(confidence,),
        ):
            rows.append(event_to_flat_row(ev))

    target_dir = out_dir / f"type={kind}" / f"year={start.year:04d}" / f"month={start.month:02d}"
    target_dir.mkdir(parents=True, exist_ok=True)
    out_path = target_dir / f"events_{start.isoformat()}_{end.isoformat()}.parquet"

    if not rows:
        typer.echo(
            f"No events for {len(vessel_ids)} vessel(s) in [{start}, {end}]; not writing parquet."
        )
        return

    df = pl.DataFrame(rows)
    df.write_parquet(out_path)
    typer.echo(f"Wrote {df.shape[0]} {kind} events → {out_path}")


@gfw_app.command("classify-vessels")
def classify_vessels(
    voyages_dir: Annotated[
        Path,
        typer.Option(help="Route-partitioned voyages parquet tree to scan for vessel_ids."),
    ] = Path("data/processed/voyages"),
    out: Annotated[
        Path,
        typer.Option(help="Output parquet path for the vessel registry."),
    ] = Path("data/processed/vessel_registry.parquet"),
    force: Annotated[
        bool,
        typer.Option(
            "--force", help="Re-fetch all vessel_ids even if present in the existing registry."
        ),
    ] = False,
    no_ais_cross_ref: Annotated[
        bool,
        typer.Option(
            "--no-ais-cross-ref",
            help="Skip the Postgres AIS static cross-reference (tests / offline runs).",
        ),
    ] = False,
) -> None:
    """Scan voyage parquet, classify vessels via GFW + AIS static, write registry."""
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not voyages_dir.exists():
        typer.echo(f"voyages directory does not exist: {voyages_dir}", err=True)
        raise typer.Exit(code=2)

    vessel_ids, mmsis = read_vessel_ids_from_voyages(voyages_dir)
    if not vessel_ids:
        typer.echo(f"no vessel_ids found under {voyages_dir}", err=True)
        raise typer.Exit(code=2)

    ais_lookup = None if no_ais_cross_ref else load_ais_static_lookup(mmsis)

    token = _get_token()
    with GfwClient(token) as client:
        df = classify_vessels_registry(
            vessel_ids,
            client,
            out_path=out,
            ais_lookup=ais_lookup,
            force=force,
        )

    typer.echo(f"Total rows: {df.height}")
    typer.echo(
        f"VLCC candidates (is_vlcc_candidate=True): {df.filter(pl.col('is_vlcc_candidate')).height}"
    )
    counts = (
        df.group_by("classification_source").agg(pl.len().alias("n")).sort("classification_source")
    )
    typer.echo("By classification_source:")
    for row in counts.iter_rows(named=True):
        typer.echo(f"  {row['classification_source']}: {row['n']}")


@gfw_app.command("compute-distances")
def compute_distances(
    voyages_dir: Annotated[
        Path,
        typer.Option(help="Route-partitioned voyages parquet tree to scan for anchorage pairs."),
    ] = Path("data/processed/voyages"),
    out: Annotated[
        Path,
        typer.Option(help="Output parquet path for the pair-keyed distance cache."),
    ] = Path("data/processed/distance_cache.parquet"),
    force: Annotated[
        bool,
        typer.Option("--force", help="Recompute every pair even if present in the existing cache."),
    ] = False,
    no_prefer_malacca: Annotated[
        bool,
        typer.Option(
            "--no-prefer-malacca",
            help="Restrict the Malacca Strait in searoute — forces Sunda/Lombok routing.",
        ),
    ] = False,
) -> None:
    """Compute sea-route distances for all unique anchorage pairs in voyages parquet."""
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not voyages_dir.exists():
        typer.echo(f"voyages directory does not exist: {voyages_dir}", err=True)
        raise typer.Exit(code=2)

    df = compute_distances_cached(
        voyages_dir, out, force=force, prefer_malacca=not no_prefer_malacca
    )
    typer.echo(f"Total pairs: {df.height}")
    if df.height > 0:
        fallback_count = int(df.get_column("is_great_circle_fallback").sum())
        typer.echo(
            f"Great-circle fallbacks: {fallback_count} ({fallback_count / df.height * 100:.1f}%)"
        )
        typer.echo(f"Median NM: {df.get_column('nautical_miles').median():.1f}")
    typer.echo(f"Wrote {out}")


@gfw_app.command("ingest-sar")
def ingest_sar(
    since: Annotated[
        str,
        typer.Option(help="Start date YYYY-MM-DD (inclusive). Required."),
    ] = "",
    until: Annotated[
        str,
        typer.Option(
            help="End date YYYY-MM-DD inclusive (interpreted as end-of-day UTC). Default: today UTC."
        ),
    ] = "",
    sar_dir: Annotated[
        Path,
        typer.Option(help="Directory of SAR CSVs (sar_vessel_detections_*.csv)."),
    ] = Path("data/raw/gfw/sar_vessels"),
    anchorages_csv: Annotated[
        Path,
        typer.Option(help="GFW named_anchorages CSV."),
    ] = Path("data/raw/gfw/anchorages/named_anchorages_v2_pipe_v3_202601.csv"),
    voyages_dir: Annotated[
        Path,
        typer.Option(help="Route-partitioned voyages parquet tree for cross-reference."),
    ] = Path("data/processed/voyages"),
    out: Annotated[
        Path,
        typer.Option(help="Output parquet path for dark-fleet candidates."),
    ] = Path("data/processed/dark_fleet_candidates.parquet"),
    buffer_km: Annotated[
        float,
        typer.Option(help="Great-circle buffer around a terminal anchorage."),
    ] = DEFAULT_BUFFER_KM,
    min_length_m: Annotated[
        float,
        typer.Option(help="Drop SAR detections shorter than this (m)."),
    ] = MIN_VESSEL_LENGTH_M,
    time_window_days: Annotated[
        int,
        typer.Option(help="+/- window for SAR-detection ↔ voyage-start match."),
    ] = DEFAULT_TIME_WINDOW_DAYS,
) -> None:
    """Cross-reference SAR vessel detections with AIS voyages to surface dark-fleet
    candidates at PG loading terminals."""
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not since:
        typer.echo("--since is required (YYYY-MM-DD)", err=True)
        raise typer.Exit(code=2)
    start = datetime.fromisoformat(since).replace(tzinfo=UTC)
    if until:
        end = datetime.fromisoformat(until).replace(
            hour=23, minute=59, second=59, microsecond=999999, tzinfo=UTC
        )
    else:
        end = datetime.now(tz=UTC)

    if not sar_dir.exists():
        typer.echo(f"SAR directory does not exist: {sar_dir}", err=True)
        raise typer.Exit(code=2)
    if not anchorages_csv.exists():
        typer.echo(f"anchorages CSV does not exist: {anchorages_csv}", err=True)
        raise typer.Exit(code=2)

    anchorages = load_anchorages(anchorages_csv)
    voyages = load_voyages_for_crossref(voyages_dir)

    df = ingest_sar_pipeline(
        sar_dir,
        anchorages,
        voyages,
        MAJOR_LOADING_TERMINALS,
        out_path=out,
        since=start,
        until=end,
        min_length_m=min_length_m,
        buffer_km=buffer_km,
        time_window_days=time_window_days,
    )

    typer.echo(f"Total SAR candidate rows: {df.height}")
    if df.height == 0:
        typer.echo(f"Wrote {out}")
        return
    dark = df.filter(~pl.col("has_matching_voyage"))
    typer.echo(f"Dark candidates (no matching voyage in ±{time_window_days}d): {dark.height}")
    typer.echo(f"  with NULL MMSI (fully dark): {dark['mmsi'].null_count()}")
    typer.echo(f"  with MMSI but no voyage match: {dark.height - dark['mmsi'].null_count()}")
    per_anc = (
        df.group_by("nearest_anchorage_label")
        .agg(pl.len().alias("n"))
        .sort("nearest_anchorage_label")
    )
    typer.echo("By nearest anchorage:")
    for row in per_anc.iter_rows(named=True):
        typer.echo(f"  {row['nearest_anchorage_label']}: {row['n']}")
    typer.echo(f"Wrote {out}")
