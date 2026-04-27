"""CLI commands for AIS ingestion."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated

import typer

from taquantgeo_ais.pipeline import run as run_pipeline
from taquantgeo_core.config import settings

ais_app = typer.Typer(name="ais", help="AIS ingestion commands.", no_args_is_help=True)


@ais_app.command()
def stream(
    duration: Annotated[
        int | None,
        typer.Option(help="Stop after N seconds. Default: run forever (Ctrl-C to stop)."),
    ] = None,
    archive_dir: Annotated[
        Path,
        typer.Option(help="Where parquet files land."),
    ] = Path("data/raw"),
    no_db: Annotated[
        bool,
        typer.Option("--no-db", help="Skip Postgres vessel sink (parquet only)."),
    ] = False,
) -> None:
    """Stream AIS from AISStream.io, filter to VLCCs, write parquet + vessels table."""
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not settings.aisstream_api_key:
        typer.echo("AISSTREAM_API_KEY is not set. Add it to .env.", err=True)
        raise typer.Exit(code=1)

    counters = asyncio.run(
        run_pipeline(
            settings.aisstream_api_key,
            archive_root=archive_dir,
            duration_s=float(duration) if duration else None,
            persist_vessels=not no_db,
        )
    )
    typer.echo(f"Done. Counters: {counters}")
