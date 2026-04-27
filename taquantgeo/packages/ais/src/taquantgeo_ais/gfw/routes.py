"""Route and region definitions for freight trading strategies.

Encoded here rather than in a config file because a route (like TD3C) is
part of the *code* contract: changing the country set should be a
reviewed change, not a silent env tweak.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    """A named freight route: origin country set → destination country set.

    Direction matters. The "laden" leg is origin→destination; the "ballast"
    leg is destination→origin.
    """

    name: str
    origin_iso3: frozenset[str]
    destination_iso3: frozenset[str]
    # Typical VLCC transit days for this route, used to filter duration-plausible voyages
    typical_transit_days: tuple[float, float]
    description: str


# TD3C: Persian Gulf → China. The most heavily traded VLCC freight contract.
TD3C = Route(
    name="td3c",
    origin_iso3=frozenset({"SAU", "ARE", "KWT", "IRQ", "IRN", "BHR", "QAT", "OMN"}),
    destination_iso3=frozenset({"CHN"}),
    typical_transit_days=(18.0, 35.0),
    description="Persian Gulf → China VLCC crude (most liquid FFA contract).",
)

# TD3C ballast leg: vessels returning empty to the Gulf.
TD3C_BALLAST = Route(
    name="td3c_ballast",
    origin_iso3=TD3C.destination_iso3,
    destination_iso3=TD3C.origin_iso3,
    typical_transit_days=(20.0, 40.0),  # slightly longer when ballasting slower
    description="China → Persian Gulf ballast leg for TD3C.",
)

# Known major VLCC loading terminals (lat, lon, canonical_name, iso3).
# Audited against GFW anchorages — all present within < 15 km.
MAJOR_LOADING_TERMINALS: tuple[tuple[float, float, str, str], ...] = (
    (26.70, 50.18, "Ras Tanura", "SAU"),
    (26.87, 49.95, "Juaymah", "SAU"),
    (25.90, 51.60, "Ras Laffan", "QAT"),
    (29.23, 50.32, "Kharg Island", "IRN"),
    (29.72, 48.83, "Basrah Oil Terminal", "IRQ"),
    (25.12, 56.34, "Fujairah", "ARE"),
    (24.18, 52.62, "Jebel Dhanna", "ARE"),
    (25.14, 52.87, "Das Island", "ARE"),
    (25.05, 54.99, "Jebel Ali", "ARE"),
    (29.07, 48.15, "Mina Al-Ahmadi", "KWT"),
    (27.53, 52.56, "Assaluyeh", "IRN"),
)


def all_routes() -> dict[str, Route]:
    return {r.name: r for r in (TD3C, TD3C_BALLAST)}
