"""taq CLI entrypoint."""

from __future__ import annotations

import typer

from taquantgeo_cli import __version__
from taquantgeo_cli.ais import ais_app
from taquantgeo_cli.gfw import gfw_app
from taquantgeo_cli.prices import prices_app
from taquantgeo_cli.signals import signals_app

app = typer.Typer(
    name="taq",
    help="TaQuantGeo command-line tool.",
    no_args_is_help=True,
)
app.add_typer(ais_app, name="ais")
app.add_typer(gfw_app, name="gfw")
app.add_typer(signals_app, name="signals")
app.add_typer(prices_app, name="prices")


@app.callback()
def _root() -> None:
    """TaQuantGeo CLI — needed so typer treats commands below as a group."""


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(f"taquantgeo {__version__}")


if __name__ == "__main__":
    app()
