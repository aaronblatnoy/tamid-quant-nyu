"""Smoke test — proves every workspace package imports cleanly."""

import importlib

from taquantgeo_cli.main import app as cli_app

PACKAGES = [
    "taquantgeo_core",
    "taquantgeo_ais",
    "taquantgeo_signals",
    "taquantgeo_backtest",
    "taquantgeo_trade",
    "taquantgeo_api",
    "taquantgeo_jobs",
    "taquantgeo_cli",
]


def test_all_packages_import_with_consistent_version() -> None:
    for name in PACKAGES:
        mod = importlib.import_module(name)
        assert mod.__version__ == "0.0.1", f"{name} has unexpected version {mod.__version__}"


def test_cli_app_loads() -> None:
    assert cli_app.info.name == "taq"
