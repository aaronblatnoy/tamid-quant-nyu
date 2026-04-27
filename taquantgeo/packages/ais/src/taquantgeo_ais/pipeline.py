"""End-to-end AIS pipeline: stream → parse → VLCC-filter → parquet + DB sink."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from taquantgeo_ais.archiver import ParquetArchiver
from taquantgeo_ais.filters import is_vlcc
from taquantgeo_ais.parser import parse_envelope, parse_position, parse_static
from taquantgeo_ais.sink_postgres import upsert_vessel
from taquantgeo_ais.streamer import stream

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


async def run(
    api_key: str,
    *,
    archive_root: Path,
    duration_s: float | None = None,
    persist_vessels: bool = True,
) -> dict[str, int]:
    """Run the AIS pipeline. Stops after `duration_s` if provided.

    Returns throughput counters: received, parsed, statics, vlcc_positions, dropped.
    """
    counters = {
        "received": 0,
        "parsed": 0,
        "statics": 0,
        "vlcc_positions": 0,
        "dropped": 0,
    }
    vlcc_mmsis: set[int] = set()
    archiver = ParquetArchiver(archive_root)

    async def _consume() -> None:
        loop = asyncio.get_running_loop()
        async for raw in stream(api_key):
            counters["received"] += 1
            env = parse_envelope(raw)
            if env is None:
                counters["dropped"] += 1
                continue
            counters["parsed"] += 1

            static = parse_static(env)
            if static is not None:
                if is_vlcc(static.Type, static.Dimension.length_m):
                    vlcc_mmsis.add(static.UserID)
                    counters["statics"] += 1
                    if persist_vessels:
                        await loop.run_in_executor(None, upsert_vessel, static)
                continue

            pos = parse_position(env)
            if pos is not None and pos.UserID in vlcc_mmsis:
                archiver.add(pos.UserID, pos, datetime.now(UTC))
                counters["vlcc_positions"] += 1

    try:
        if duration_s is not None:
            await asyncio.wait_for(_consume(), timeout=duration_s)
        else:
            await _consume()
    except TimeoutError:
        logger.info("duration elapsed, shutting down")
    finally:
        archiver.flush()

    return counters
