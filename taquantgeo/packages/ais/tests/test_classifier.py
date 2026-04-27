"""Tests for the GFW vessel-identity classifier.

Most tests use ``httpx.MockTransport`` directly — the classifier cascade is
pure business logic and deterministic stubs are faster + clearer than VCR
cassettes. One cassette test round-trips a real GFW payload to guard against
drift in the upstream identity schema.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import httpx
import polars as pl
import pytest

from taquantgeo_ais.gfw.api import GfwClient, VesselIdentity
from taquantgeo_ais.gfw.classifier import (
    _REGISTRY_COLUMN_ORDER,
    VLCC_HEURISTIC,
    AisStaticRef,
    classify_one,
    classify_vessels,
)


def _gfw_identity_payload(
    vessel_id: str,
    *,
    ssvid: str | None = "357862000",
    imo: str | None = "9127033",
    shipname: str = "TESTER",
    flag: str | None = "PAN",
    shiptypes: list[str] | None = None,
    tonnage_gt: float | None = None,
    length_m: float | None = None,
) -> dict[str, Any]:
    """Build a minimal /v3/vessels/{id} JSON body matching the live shape."""
    registry: list[dict[str, Any]] = []
    if tonnage_gt is not None or length_m is not None or flag is not None:
        registry.append(
            {
                "tonnageGt": tonnage_gt,
                "lengthM": length_m,
                "flag": flag,
                "shiptype": None,
            }
        )
    return {
        "registryInfoTotalRecords": len(registry),
        "registryInfo": registry,
        "combinedSourcesInfo": [
            {
                "vesselId": vessel_id,
                "shiptypes": [
                    {"name": t, "source": "COMBINATION_OF_REGISTRY_AND_AIS_INFERRED_NN_INFO"}
                    for t in (shiptypes or ["OTHER"])
                ],
            }
        ],
        "selfReportedInfo": [
            {
                "id": vessel_id,
                "ssvid": ssvid,
                "shipname": shipname,
                "flag": flag,
                "imo": imo,
            }
        ],
    }


def _mock_client(
    vessel_to_body: dict[str, dict[str, Any] | None],
    *,
    not_found: set[str] | None = None,
) -> tuple[GfwClient, list[httpx.Request]]:
    """Build a GfwClient backed by MockTransport.

    ``vessel_to_body`` maps vessel_id → JSON body to return (status 200).
    ``not_found`` is a set of vessel_ids that should 404.
    """
    calls: list[httpx.Request] = []
    nf = not_found or set()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        # url.path like /v3/vessels/<id>
        vid = request.url.path.rsplit("/", 1)[-1]
        if vid in nf:
            return httpx.Response(404, json={"error": "not found"})
        body = vessel_to_body.get(vid)
        if body is None:
            return httpx.Response(404, json={"error": "no stub"})
        return httpx.Response(200, json=body)

    client = GfwClient("test-token", transport=httpx.MockTransport(handler))
    return client, calls


# ---------- unit tests: classify_one cascade ----------


def test_classifier_vlcc_by_tonnage() -> None:
    identity = VesselIdentity(
        vessel_id="v1",
        mmsi=1,
        imo="9999999",
        name="BIG ONE",
        flag="PAN",
        gfw_shiptypes=("OTHER",),
        registry_shiptype=None,
        length_m=None,
        tonnage_gt=160_000.0,
    )
    is_vlcc, source = classify_one(identity, ais_static=None, from_td3c_route=True)
    assert is_vlcc is True
    assert source == "gfw_identity"


def test_classifier_vlcc_by_length() -> None:
    identity = VesselIdentity(
        vessel_id="v2",
        mmsi=2,
        imo=None,
        name="LONG ONE",
        flag=None,
        gfw_shiptypes=("OTHER",),
        registry_shiptype=None,
        length_m=330.0,
        tonnage_gt=None,
    )
    is_vlcc, source = classify_one(identity, ais_static=None, from_td3c_route=True)
    assert is_vlcc is True
    assert source == "gfw_identity"


def test_classifier_vlcc_by_shiptype_token() -> None:
    """oil_tanker in shiptypes + near-threshold size → VLCC via soft rule."""
    identity = VesselIdentity(
        vessel_id="v3",
        mmsi=3,
        imo=None,
        name="SOFT TANKER",
        flag=None,
        # GFW returns uppercase; match must be case-insensitive.
        gfw_shiptypes=("OIL_TANKER",),
        registry_shiptype=None,
        length_m=None,
        tonnage_gt=110_000.0,
    )
    is_vlcc, source = classify_one(identity, ais_static=None, from_td3c_route=True)
    assert is_vlcc is True
    assert source == "gfw_identity"

    # Also trips via length at 280 m with oil_tanker token.
    identity2 = VesselIdentity(
        vessel_id="v3b",
        mmsi=31,
        imo=None,
        name="SOFT TANKER 2",
        flag=None,
        gfw_shiptypes=("oil_tanker",),
        registry_shiptype=None,
        length_m=285.0,
        tonnage_gt=None,
    )
    is_vlcc2, source2 = classify_one(identity2, ais_static=None, from_td3c_route=True)
    assert is_vlcc2 is True
    assert source2 == "gfw_identity"


def test_classifier_not_vlcc_product_tanker() -> None:
    """Small oil tanker (GT < 50k) → firm NOT VLCC."""
    identity = VesselIdentity(
        vessel_id="v4",
        mmsi=4,
        imo=None,
        name="PRODUCT",
        flag=None,
        gfw_shiptypes=("OIL_TANKER",),
        registry_shiptype=None,
        length_m=180.0,
        tonnage_gt=28_000.0,
    )
    is_vlcc, source = classify_one(identity, ais_static=None, from_td3c_route=True)
    assert is_vlcc is False
    assert source == "gfw_identity"


def test_classifier_cross_ref_ais_static_contradicts() -> None:
    """AIS says cargo (ship_type=70) → override any GFW VLCC hint."""
    identity = VesselIdentity(
        vessel_id="v5",
        mmsi=5,
        imo=None,
        name="ACTUALLY CARGO",
        flag=None,
        gfw_shiptypes=("OIL_TANKER",),  # GFW wrongly tagged
        registry_shiptype=None,
        length_m=None,
        tonnage_gt=200_000.0,  # would otherwise fire rule 2
    )
    ais_static = AisStaticRef(ship_type=70, length_m=250)
    is_vlcc, source = classify_one(identity, ais_static=ais_static, from_td3c_route=True)
    assert is_vlcc is False
    assert source == "ais_static"


def test_classifier_cross_ref_ais_static_positive() -> None:
    """GFW returns no size data but AIS says ship_type=80, len=325 → VLCC / ais_static."""
    identity = VesselIdentity(
        vessel_id="v6",
        mmsi=6,
        imo=None,
        name="AIS SAYS VLCC",
        flag=None,
        gfw_shiptypes=("OTHER",),
        registry_shiptype=None,
        length_m=None,
        tonnage_gt=None,
    )
    ais_static = AisStaticRef(ship_type=80, length_m=325)
    is_vlcc, source = classify_one(identity, ais_static=ais_static, from_td3c_route=True)
    assert is_vlcc is True
    assert source == "ais_static"


def test_classifier_duration_heuristic_fallback() -> None:
    """Identity exists but no size + no AIS static + from_td3c → True / duration_heuristic."""
    identity = VesselIdentity(
        vessel_id="v7",
        mmsi=7,
        imo=None,
        name="UNKNOWN BUT ON ROUTE",
        flag=None,
        gfw_shiptypes=("OTHER",),
        registry_shiptype=None,
        length_m=None,
        tonnage_gt=None,
    )
    is_vlcc, source = classify_one(identity, ais_static=None, from_td3c_route=True)
    assert is_vlcc is True
    assert source == "duration_heuristic"

    # But when NOT from TD3C, the fallback doesn't fire.
    is_vlcc2, source2 = classify_one(identity, ais_static=None, from_td3c_route=False)
    assert is_vlcc2 is False
    assert source2 == "none"


def test_classifier_cargo_token_rules_out() -> None:
    """shiptypes contains CARGO with no tanker token → not VLCC."""
    identity = VesselIdentity(
        vessel_id="v8",
        mmsi=8,
        imo=None,
        name="CONTAINER",
        flag=None,
        gfw_shiptypes=("CARGO",),
        registry_shiptype=None,
        length_m=None,
        tonnage_gt=None,
    )
    is_vlcc, source = classify_one(identity, ais_static=None, from_td3c_route=True)
    assert is_vlcc is False
    assert source == "gfw_identity"


# ---------- orchestrator: classify_vessels ----------


def test_classify_vessels_cross_refs_ais_ship_type(tmp_path: Path) -> None:
    """When the AIS lookup has the MMSI, ais_ship_type column is populated."""
    body_vlcc = _gfw_identity_payload(
        "vid1", ssvid="111111111", tonnage_gt=180_000.0, shipname="VLCC ONE"
    )
    client, _calls = _mock_client({"vid1": body_vlcc})
    ais_lookup = {111_111_111: AisStaticRef(ship_type=80, length_m=330)}
    out = tmp_path / "registry.parquet"
    try:
        df = classify_vessels(["vid1"], client, out_path=out, ais_lookup=ais_lookup)
    finally:
        client.close()

    assert df.height == 1
    row = df.row(0, named=True)
    assert row["ais_ship_type"] == 80
    assert row["is_vlcc_candidate"] is True
    # Gross tonnage fired first → gfw_identity, not ais_static
    assert row["classification_source"] == "gfw_identity"


def test_classify_vessels_unknown_vessel_id_yields_none_source(tmp_path: Path) -> None:
    """GFW 404 → row written, classification_source='none', is_vlcc_candidate=False."""
    client, _calls = _mock_client({}, not_found={"ghost"})
    out = tmp_path / "registry.parquet"
    try:
        df = classify_vessels(["ghost"], client, out_path=out)
    finally:
        client.close()

    assert df.height == 1
    row = df.row(0, named=True)
    assert row["vessel_id"] == "ghost"
    assert row["is_vlcc_candidate"] is False
    assert row["classification_source"] == "none"
    assert row["mmsi"] is None
    assert row["name"] is None


def test_classify_vessels_resume_from_cache(tmp_path: Path) -> None:
    """Second run with the same inputs does NOT re-hit GFW for known vessel_ids."""
    body1 = _gfw_identity_payload("vid1", ssvid="1", tonnage_gt=180_000.0)
    body2 = _gfw_identity_payload("vid2", ssvid="2", tonnage_gt=20_000.0)

    client1, calls1 = _mock_client({"vid1": body1, "vid2": body2})
    out = tmp_path / "registry.parquet"
    try:
        df1 = classify_vessels(["vid1", "vid2"], client1, out_path=out)
    finally:
        client1.close()
    assert df1.height == 2
    assert len(calls1) == 2

    # Second run — inputs unchanged, cache present. Expect zero new HTTP calls.
    client2, calls2 = _mock_client({"vid1": body1, "vid2": body2})
    try:
        df2 = classify_vessels(["vid1", "vid2"], client2, out_path=out)
    finally:
        client2.close()

    assert df2.height == 2
    assert len(calls2) == 0  # cache hit, no network
    # Row identity preserved
    assert set(df2["vessel_id"].to_list()) == {"vid1", "vid2"}


def test_classify_vessels_force_refetches(tmp_path: Path) -> None:
    body = _gfw_identity_payload("vid1", tonnage_gt=180_000.0)

    client1, calls1 = _mock_client({"vid1": body})
    out = tmp_path / "registry.parquet"
    try:
        _ = classify_vessels(["vid1"], client1, out_path=out)
    finally:
        client1.close()
    assert len(calls1) == 1

    client2, calls2 = _mock_client({"vid1": body})
    try:
        _ = classify_vessels(["vid1"], client2, out_path=out, force=True)
    finally:
        client2.close()
    assert len(calls2) == 1  # re-fetched


def test_classify_vessels_output_schema_and_column_order(tmp_path: Path) -> None:
    body = _gfw_identity_payload(
        "vid1", ssvid="111", tonnage_gt=180_000.0, length_m=335.0, shiptypes=["OIL_TANKER"]
    )
    client, _ = _mock_client({"vid1": body})
    out = tmp_path / "registry.parquet"
    try:
        df = classify_vessels(["vid1"], client, out_path=out)
    finally:
        client.close()

    # Column order matches the acceptance contract verbatim.
    assert tuple(df.columns) == _REGISTRY_COLUMN_ORDER
    assert out.exists()

    # Re-read the parquet and confirm dtypes.
    loaded = pl.read_parquet(out)
    assert loaded["mmsi"].dtype == pl.Int64
    assert loaded["vessel_id"].dtype == pl.String
    assert loaded["gfw_shiptypes"].dtype == pl.List(pl.String)
    assert loaded["gross_tonnage"].dtype == pl.Float64
    assert loaded["registered_length_m"].dtype == pl.Float64
    assert loaded["is_vlcc_candidate"].dtype == pl.Boolean
    assert loaded["ais_ship_type"].dtype == pl.Int64
    # fetched_at is UTC-tagged.
    assert isinstance(loaded["fetched_at"].dtype, pl.Datetime)
    ts: datetime = loaded["fetched_at"].item(0)
    assert ts.tzinfo is not None
    assert ts.tzinfo.utcoffset(ts) == UTC.utcoffset(ts)


def test_vlcc_heuristic_constant_is_descriptive() -> None:
    """VLCC_HEURISTIC is documentation — must mention the core thresholds."""
    # Thresholds from ADR 0004.
    assert "150000" in VLCC_HEURISTIC or "150,000" in VLCC_HEURISTIC
    assert "320" in VLCC_HEURISTIC
    assert "oil_tanker" in VLCC_HEURISTIC
    assert "none" in VLCC_HEURISTIC


# ---------- cassette round-trip (scrubbed against live GFW) ----------


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, Any]:
    return {
        "filter_headers": [("authorization", "REDACTED")],
        "match_on": ("method", "scheme", "host", "path", "query"),
    }


@pytest.fixture(scope="module")
def vcr_cassette_dir(request: pytest.FixtureRequest) -> str:
    return str(request.path.parent / "cassettes" / "classifier")


@pytest.fixture
def gfw_token() -> str:
    return os.environ.get("GFW_API_TOKEN") or "test-token"


@pytest.mark.vcr
def test_cassette_identity_roundtrip_real_vessel(gfw_token: str, tmp_path: Path) -> None:
    """Round-trip one real GFW identity payload through classify_vessels.

    Cassette recorded once against live GFW; Authorization scrubbed.
    The specific vessel_id below is ``DS VENTURE`` (VLCC, ~157k GT).
    Regenerate: delete the cassette file and run with --record-mode=once.
    """
    out = tmp_path / "registry.parquet"
    with GfwClient(gfw_token) as client:
        df = classify_vessels(
            ["2936a72a4-4a89-483e-1c6c-c97233154c78"],
            client,
            out_path=out,
        )
    assert df.height == 1
    row = df.row(0, named=True)
    assert row["vessel_id"] == "2936a72a4-4a89-483e-1c6c-c97233154c78"
    assert row["is_vlcc_candidate"] is True
    assert row["classification_source"] == "gfw_identity"
    assert row["name"] == "DS VENTURE"
    assert row["gross_tonnage"] is not None
    assert row["gross_tonnage"] >= 150_000
