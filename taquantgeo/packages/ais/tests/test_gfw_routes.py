"""Tests for GFW route definitions."""

from __future__ import annotations

from taquantgeo_ais.gfw.routes import TD3C, TD3C_BALLAST, all_routes


def test_td3c_is_persian_gulf_to_china() -> None:
    assert "CHN" in TD3C.destination_iso3
    for code in ("SAU", "ARE", "KWT", "IRQ", "IRN", "QAT", "BHR", "OMN"):
        assert code in TD3C.origin_iso3, f"{code} missing from TD3C origins"


def test_td3c_ballast_is_reverse_of_td3c() -> None:
    assert TD3C_BALLAST.origin_iso3 == TD3C.destination_iso3
    assert TD3C_BALLAST.destination_iso3 == TD3C.origin_iso3


def test_all_routes_keyed_by_name() -> None:
    routes = all_routes()
    assert routes["td3c"] is TD3C
    assert routes["td3c_ballast"] is TD3C_BALLAST
