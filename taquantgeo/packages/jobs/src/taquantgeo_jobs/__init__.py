"""taquantgeo_jobs — scheduled pipelines.

Phase 11 wires these into APScheduler. For v0 phases 04-10 each job is
exposed as a plain ``run_once()`` function so it can be called from the
CLI, from tests, and later from the scheduler without rework.
"""

from taquantgeo_jobs.daily_signal import run_once as run_daily_signal

__version__ = "0.0.1"

__all__ = ["run_daily_signal"]
