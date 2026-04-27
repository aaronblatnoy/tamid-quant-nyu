"""VLCC filter tests."""

from __future__ import annotations

import pytest

from taquantgeo_ais.filters import is_vlcc


@pytest.mark.parametrize(
    ("ship_type", "length_m", "expected"),
    [
        (80, 330, True),  # canonical VLCC
        (80, 320, True),  # boundary, inclusive
        (80, 319, False),  # just below threshold
        (80, 250, False),  # Suezmax-class oil tanker, too small
        (70, 330, False),  # general cargo, not oil
        (89, 330, False),  # other tanker subtype, not 80
        (80, 0, False),  # no dimension data
    ],
)
def test_is_vlcc(ship_type: int, length_m: int, expected: bool) -> None:
    assert is_vlcc(ship_type, length_m) is expected
