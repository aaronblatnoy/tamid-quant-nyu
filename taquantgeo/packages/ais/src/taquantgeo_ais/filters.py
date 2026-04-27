"""VLCC filtering predicates.

VLCC = Very Large Crude Carrier. Industry definition is DWT >= 200,000.
DWT isn't broadcast over AIS, so we proxy with length: VLCCs are 320m+ LOA
and there is essentially no overlap with smaller tanker classes at that
length cutoff.
"""

from __future__ import annotations

VLCC_LENGTH_THRESHOLD_M = 320
SHIP_TYPE_OIL_TANKER = 80


def is_vlcc(ship_type: int, length_m: int) -> bool:
    return ship_type == SHIP_TYPE_OIL_TANKER and length_m >= VLCC_LENGTH_THRESHOLD_M
