"""Tests for GFW /v3/events client.

Parsing + pagination tests replay pre-recorded cassettes (VCR) so CI runs
offline. Cassettes live under `packages/ais/tests/cassettes/gfw_events/` and
were captured once against the live API with a test token; the Authorization
header is scrubbed on write. To refresh: delete a cassette file and re-run
`pytest -k <test_name> --record-mode=once` with GFW_API_TOKEN set.

The 429-backoff test uses httpx.MockTransport directly (faster than VCR for
synthetic failures, and 429s are hard to reproduce against the real API on
demand).
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import httpx
import pytest

from taquantgeo_ais.gfw.events import (
    DATASETS,
    BackoffConfig,
    Event,
    EventsClient,
    build_params,
    event_to_flat_row,
)


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, Any]:
    # pytest-recording defaults to record_mode="none" (offline replay only), so
    # CI fails loudly on missing cassettes. Pass --record-mode=once to refresh.
    return {
        "filter_headers": [("authorization", "REDACTED")],
        "match_on": ("method", "scheme", "host", "path", "query"),
    }


@pytest.fixture(scope="module")
def vcr_cassette_dir(request: pytest.FixtureRequest) -> str:
    # Match the path the feature spec expects.
    return str(request.path.parent / "cassettes" / "gfw_events")


@pytest.fixture
def gfw_token() -> str:
    """Real token when recording cassettes; placeholder when replaying."""
    return os.environ.get("GFW_API_TOKEN") or "test-token"


# ---------- pure-unit (no HTTP) ----------


def test_build_params_port_visit_includes_confidences() -> None:
    params = build_params(
        dataset=DATASETS["port_visit"],
        vessels=["v1", "v2"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        confidences=(4,),
        limit=10,
        offset=0,
    )
    assert params["datasets[0]"] == DATASETS["port_visit"]
    assert params["vessels[0]"] == "v1"
    assert params["vessels[1]"] == "v2"
    assert params["confidences[0]"] == 4
    assert params["start-date"] == "2024-01-01"
    assert params["end-date"] == "2024-12-31"
    assert params["limit"] == 10
    assert params["offset"] == 0


def test_empty_vessel_list_yields_nothing() -> None:
    client = EventsClient("fake-token")
    try:
        assert (
            list(
                client.iter_events(
                    [], "port_visit", start_date=date(2024, 1, 1), end_date=date(2024, 12, 31)
                )
            )
            == []
        )
    finally:
        client.close()


def test_token_required() -> None:
    with pytest.raises(ValueError, match="GFW_API_TOKEN is empty"):
        EventsClient("")


# ---------- parsing tests that exercise Pydantic coercion on real payloads ----------


def test_port_visit_parses_string_typed_numeric_fields() -> None:
    """The server returns confidence and distanceFromShoreKm as strings.
    Pydantic lax-mode coercion must turn them into int/float."""
    raw: dict[str, Any] = {
        "id": "pv1",
        "type": "port_visit",
        "start": "2024-02-01T05:22:06.000Z",
        "end": "2024-02-04T06:28:17.000Z",
        "position": {"lat": 21.8, "lon": 113.3},
        "vessel": {
            "id": "v1",
            "name": "X",
            "ssvid": "357862000",
            "flag": "PAN",
            "type": "other",
        },
        "port_visit": {
            "visitId": "vv1",
            "confidence": "4",  # ← STRING in the wild
            "durationHrs": 73.1,
            "startAnchorage": {
                "anchorageId": "a1",
                "distanceFromShoreKm": "9",  # ← STRING in the wild
                "flag": "CHN",
                "id": "chn-x",
                "lat": 21.8,
                "lon": 113.3,
                "name": "X",
                "topDestination": "X",
            },
        },
    }
    ev = Event.model_validate(raw)
    assert ev.port_visit is not None
    assert ev.port_visit.confidence == 4
    assert ev.port_visit.start_anchorage is not None
    assert ev.port_visit.start_anchorage.anchorage_id == "a1"


def test_gap_parses_string_typed_positions() -> None:
    """gap.onPosition.lat comes as a string in some responses; offPosition as float.
    Both must parse."""
    raw: dict[str, Any] = {
        "id": "g1",
        "type": "gap",
        "start": "2023-08-10T10:49:52.000Z",
        "end": "2023-08-12T10:25:09.000Z",
        "position": {"lat": -2.1, "lon": 84.6},
        "vessel": {"id": "v1", "name": "N", "ssvid": "477906600", "flag": "HKG", "type": "other"},
        "gap": {
            "intentionalDisabling": True,
            "distanceKm": "1054.8368",
            "durationHours": 47.58,
            "impliedSpeedKnots": "11.96",
            "positions12HoursBeforeSat": "22",
            "positionsPerDaySatReception": 87.55,
            "offPosition": {"lat": 0.89, "lon": 88.28},
            "onPosition": {"lat": "-5.09", "lon": "80.92"},
        },
    }
    ev = Event.model_validate(raw)
    assert ev.gap is not None
    assert ev.gap.intentional_disabling is True
    assert ev.gap.distance_km == pytest.approx(1054.8368)
    assert ev.gap.positions_12_hours_before_sat == 22
    assert ev.gap.on_position is not None
    assert ev.gap.on_position.lat == pytest.approx(-5.09)


def test_encounter_parses_nested_other_vessel() -> None:
    raw: dict[str, Any] = {
        "id": "e1",
        "type": "encounter",
        "start": "2022-11-03T00:00:00.000Z",
        "end": "2024-02-28T19:40:00.000Z",
        "position": {"lat": -7.6, "lon": -94.2},
        "vessel": {
            "id": "v1",
            "name": "A",
            "ssvid": "412121039",
            "flag": "CHN",
            "type": "fishing",
        },
        "encounter": {
            "vessel": {
                "id": "v2",
                "flag": "CHN",
                "name": "B",
                "type": "fishing",
                "ssvid": "412421039",
            },
            "medianDistanceKilometers": 0.008,
            "medianSpeedKnots": 0.57,
            "type": "fishing-fishing",
            "potentialRisk": False,
        },
    }
    ev = Event.model_validate(raw)
    assert ev.encounter is not None
    assert ev.encounter.vessel is not None
    assert ev.encounter.vessel.id == "v2"
    assert ev.encounter.median_distance_kilometers == pytest.approx(0.008)


def test_loitering_parses() -> None:
    raw: dict[str, Any] = {
        "id": "l1",
        "type": "loitering",
        "start": "2024-01-25T17:55:24.000Z",
        "end": "2024-02-01T02:58:06.000Z",
        "position": {"lat": 21.4, "lon": 113.2},
        "vessel": {"id": "v1", "name": "X", "ssvid": "1", "flag": "PAN", "type": "other"},
        "loitering": {
            "totalTimeHours": 153.0,
            "totalDistanceKm": 19.2,
            "averageSpeedKnots": 0.067,
            "averageDistanceFromShoreKm": 41.3,
        },
    }
    ev = Event.model_validate(raw)
    assert ev.loitering is not None
    assert ev.loitering.total_time_hours == pytest.approx(153.0)


def test_flat_row_includes_event_kind_prefixed_cols() -> None:
    ev = Event.model_validate(
        {
            "id": "pv1",
            "type": "port_visit",
            "start": "2024-02-01T05:22:06.000Z",
            "end": "2024-02-04T06:28:17.000Z",
            "position": {"lat": 21.8, "lon": 113.3},
            "vessel": {"id": "v1", "ssvid": "357862000", "flag": "PAN", "type": "other"},
            "port_visit": {
                "visitId": "vv1",
                "confidence": "4",
                "durationHrs": 73.1,
                "startAnchorage": {"anchorageId": "a1", "name": "X", "flag": "CHN"},
            },
        }
    )
    row = event_to_flat_row(ev)
    assert row["type"] == "port_visit"
    assert row["pv_confidence"] == 4
    assert row["pv_start_anchorage_id"] == "a1"
    # non-port-visit columns absent (parquet will fill nulls when mixed)
    assert "gap_distance_km" not in row


# ---------- httpx.MockTransport — deterministic rate-limit + pagination ----------


def _make_mock_client(
    responses: list[httpx.Response],
    *,
    page_size: int = 2,
    backoff: BackoffConfig | None = None,
) -> tuple[EventsClient, list[httpx.Request]]:
    calls: list[httpx.Request] = []
    it = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        try:
            return next(it)
        except StopIteration as e:
            raise AssertionError(f"unexpected extra HTTP call to {request.url}") from e

    client = EventsClient(
        "test-token",
        page_size=page_size,
        backoff=backoff or BackoffConfig(initial_seconds=0.0, max_seconds=0.0, max_attempts=4),
        transport=httpx.MockTransport(handler),
    )
    return client, calls


def _event_entry(eid: str, vid: str = "v1") -> dict[str, Any]:
    return {
        "id": eid,
        "type": "port_visit",
        "start": "2024-02-01T05:22:06.000Z",
        "end": "2024-02-04T06:28:17.000Z",
        "position": {"lat": 1.0, "lon": 2.0},
        "vessel": {"id": vid, "ssvid": "1", "flag": "X", "type": "other"},
        "port_visit": {"visitId": "v", "confidence": "4", "durationHrs": 10.0},
    }


def test_pagination_follows_next_offset() -> None:
    page1 = httpx.Response(
        200,
        json={
            "limit": 2,
            "offset": 0,
            "nextOffset": 2,
            "total": 3,
            "entries": [_event_entry("e1"), _event_entry("e2")],
        },
    )
    page2 = httpx.Response(
        200,
        json={
            "limit": 2,
            "offset": 2,
            "nextOffset": None,
            "total": 3,
            "entries": [_event_entry("e3")],
        },
    )
    client, calls = _make_mock_client([page1, page2], page_size=2)
    try:
        events = list(
            client.iter_events(
                ["v1"], "port_visit", start_date=date(2024, 1, 1), end_date=date(2024, 12, 31)
            )
        )
    finally:
        client.close()
    assert [e.id for e in events] == ["e1", "e2", "e3"]
    assert len(calls) == 2
    assert "offset=0" in str(calls[0].url) or calls[0].url.params["offset"] == "0"
    assert calls[1].url.params["offset"] == "2"


def test_429_then_success_backs_off_and_retries() -> None:
    r1 = httpx.Response(429, json={"error": "rate limited"})
    r2 = httpx.Response(429, json={"error": "rate limited"})
    r3 = httpx.Response(
        200,
        json={
            "limit": 2,
            "offset": 0,
            "nextOffset": None,
            "total": 1,
            "entries": [_event_entry("e1")],
        },
    )
    client, calls = _make_mock_client([r1, r2, r3], page_size=2)
    try:
        events = list(
            client.iter_events(
                ["v1"], "port_visit", start_date=date(2024, 1, 1), end_date=date(2024, 12, 31)
            )
        )
    finally:
        client.close()
    assert [e.id for e in events] == ["e1"]
    assert len(calls) == 3


def test_429_exhausted_returns_empty_without_raising() -> None:
    responses = [httpx.Response(429, json={"error": "rate limited"}) for _ in range(4)]
    client, calls = _make_mock_client(
        responses,
        backoff=BackoffConfig(initial_seconds=0.0, max_seconds=0.0, max_attempts=4),
    )
    try:
        events = list(
            client.iter_events(
                ["v1"], "port_visit", start_date=date(2024, 1, 1), end_date=date(2024, 12, 31)
            )
        )
    finally:
        client.close()
    assert events == []
    assert len(calls) == 4


def test_confidences_param_omitted_for_non_port_visit_kinds() -> None:
    r = httpx.Response(
        200,
        json={"limit": 10, "offset": 0, "nextOffset": None, "total": 0, "entries": []},
    )
    client, calls = _make_mock_client([r])
    try:
        _ = list(
            client.iter_events(
                ["v1"], "gap", start_date=date(2024, 1, 1), end_date=date(2024, 12, 31)
            )
        )
    finally:
        client.close()
    assert len(calls) == 1
    # API 422s if confidences is passed on non-port-visit datasets
    assert not any(k.startswith("confidences") for k in calls[0].url.params)


def test_unknown_vessel_empty_entries_yields_nothing() -> None:
    r = httpx.Response(
        200,
        json={"limit": 10, "offset": 0, "nextOffset": None, "total": 0, "entries": []},
    )
    client, _calls = _make_mock_client([r])
    try:
        events = list(
            client.iter_events(
                ["00000000-0000-0000-0000-000000000000"],
                "port_visit",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 12, 31),
            )
        )
    finally:
        client.close()
    assert events == []


def test_chunks_vessels_into_multiple_requests() -> None:
    # 75 vessels with vessel_batch_size=50 → 2 HTTP calls (50 + 25).
    def empty_page() -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "limit": 100,
                "offset": 0,
                "nextOffset": None,
                "total": 0,
                "entries": [],
            },
        )

    client, calls = _make_mock_client([empty_page(), empty_page()], page_size=100)
    client.close()

    # Rebuild with a smaller vessel_batch_size for the chunking assertion.
    calls = []
    responses = iter([empty_page(), empty_page()])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return next(responses)

    with EventsClient(
        "test-token",
        page_size=100,
        vessel_batch_size=50,
        backoff=BackoffConfig(initial_seconds=0.0, max_seconds=0.0, max_attempts=2),
        transport=httpx.MockTransport(handler),
    ) as c:
        vids = [f"vessel-{i:03d}" for i in range(75)]
        list(
            c.iter_events(
                vids,
                "gap",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 12, 31),
            )
        )

    assert len(calls) == 2
    # First call carries vessel IDs 0..49, second carries 50..74.
    assert calls[0].url.params["vessels[0]"] == "vessel-000"
    assert calls[0].url.params["vessels[49]"] == "vessel-049"
    assert "vessels[50]" not in calls[0].url.params
    assert calls[1].url.params["vessels[0]"] == "vessel-050"
    assert calls[1].url.params["vessels[24]"] == "vessel-074"


def test_5xx_triggers_retry_like_429() -> None:
    r1 = httpx.Response(503, json={"error": "service unavailable"})
    r2 = httpx.Response(
        200,
        json={
            "limit": 2,
            "offset": 0,
            "nextOffset": None,
            "total": 1,
            "entries": [_event_entry("e1")],
        },
    )
    client, calls = _make_mock_client([r1, r2])
    try:
        events = list(
            client.iter_events(
                ["v1"],
                "port_visit",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 12, 31),
            )
        )
    finally:
        client.close()
    assert [e.id for e in events] == ["e1"]
    assert len(calls) == 2


def test_non_dict_body_returns_none_without_raising() -> None:
    r = httpx.Response(200, json=["unexpected", "shape"])
    client, calls = _make_mock_client([r])
    try:
        events = list(
            client.iter_events(
                ["v1"],
                "port_visit",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 12, 31),
            )
        )
    finally:
        client.close()
    assert events == []
    assert len(calls) == 1


def test_non_int_next_offset_stops_pagination() -> None:
    r = httpx.Response(
        200,
        json={
            "limit": 2,
            "offset": 0,
            "nextOffset": "NOT_A_NUMBER",
            "total": 99,
            "entries": [_event_entry("e1")],
        },
    )
    client, calls = _make_mock_client([r])
    try:
        events = list(
            client.iter_events(
                ["v1"],
                "port_visit",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 12, 31),
            )
        )
    finally:
        client.close()
    assert [e.id for e in events] == ["e1"]
    assert len(calls) == 1


def test_non_advancing_next_offset_stops_pagination() -> None:
    r = httpx.Response(
        200,
        json={
            "limit": 2,
            "offset": 0,
            "nextOffset": 0,  # would loop forever if not guarded
            "total": 99,
            "entries": [_event_entry("e1")],
        },
    )
    client, calls = _make_mock_client([r])
    try:
        events = list(
            client.iter_events(
                ["v1"],
                "port_visit",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 12, 31),
            )
        )
    finally:
        client.close()
    assert [e.id for e in events] == ["e1"]
    assert len(calls) == 1


def test_flat_rows_mix_kinds_produce_alignable_parquet_schema() -> None:
    pl = pytest.importorskip("polars")
    pv = Event.model_validate(
        {
            "id": "pv1",
            "type": "port_visit",
            "start": "2024-02-01T05:22:06.000Z",
            "end": "2024-02-04T06:28:17.000Z",
            "position": {"lat": 1.0, "lon": 2.0},
            "vessel": {"id": "v1", "ssvid": "1", "flag": "X", "type": "other"},
            "port_visit": {"visitId": "vv", "confidence": "4", "durationHrs": 10.0},
        }
    )
    gap = Event.model_validate(
        {
            "id": "g1",
            "type": "gap",
            "start": "2024-02-10T00:00:00.000Z",
            "end": "2024-02-11T00:00:00.000Z",
            "position": {"lat": 3.0, "lon": 4.0},
            "vessel": {"id": "v2", "ssvid": "2", "flag": "Y", "type": "other"},
            "gap": {
                "intentionalDisabling": True,
                "distanceKm": "100",
                "durationHours": 24.0,
            },
        }
    )
    loit = Event.model_validate(
        {
            "id": "l1",
            "type": "loitering",
            "start": "2024-02-15T00:00:00.000Z",
            "end": "2024-02-16T00:00:00.000Z",
            "position": {"lat": 5.0, "lon": 6.0},
            "vessel": {"id": "v3", "ssvid": "3", "flag": "Z", "type": "other"},
            "loitering": {"totalTimeHours": 24.0, "totalDistanceKm": 0.0},
        }
    )
    rows = [event_to_flat_row(ev) for ev in (pv, gap, loit)]
    df = pl.DataFrame(rows)
    assert df.shape[0] == 3
    # Every event has the common cols; kind-specific cols are null where absent.
    assert set(["id", "type", "lat", "lon", "vessel_id"]).issubset(df.columns)
    pv_row = df.filter(pl.col("type") == "port_visit").row(0, named=True)
    assert pv_row["pv_confidence"] == 4
    assert pv_row["gap_distance_km"] is None
    gap_row = df.filter(pl.col("type") == "gap").row(0, named=True)
    assert gap_row["gap_distance_km"] == 100.0
    assert gap_row["pv_confidence"] is None


# ---------- VCR cassette tests (offline replay) ----------


@pytest.mark.vcr
def test_cassette_port_visit_roundtrip(gfw_token: str) -> None:
    """Cassette was recorded once against live GFW with a real token. Scrubbed
    Authorization on write. Verifies our client round-trips real payloads."""
    with EventsClient(gfw_token, page_size=5) as c:
        events = list(
            c.iter_events(
                ["2114e5305-5432-8186-f90a-88e088ce4dc6"],
                "port_visit",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 6, 30),
            )
        )
    assert len(events) >= 1
    first = events[0]
    assert first.type == "port_visit"
    assert first.port_visit is not None
    assert first.port_visit.confidence == 4


@pytest.mark.vcr
def test_cassette_gap_roundtrip(gfw_token: str) -> None:
    with EventsClient(gfw_token, page_size=5) as c:
        events = list(
            c.iter_events(
                ["ee0b997e5-52c5-19fe-2926-cb1d2d235599"],
                "gap",
                start_date=date(2023, 1, 1),
                end_date=date(2024, 1, 1),
            )
        )
    assert len(events) >= 1
    assert events[0].type == "gap"
    assert events[0].gap is not None


@pytest.mark.vcr
def test_cassette_encounter_roundtrip(gfw_token: str) -> None:
    with EventsClient(gfw_token, page_size=3) as c:
        events = list(
            c.iter_events(
                ["4a0d6c09b-b311-287e-5b2f-9431105aecd2"],
                "encounter",
                start_date=date(2022, 1, 1),
                end_date=date(2024, 6, 30),
            )
        )
    assert len(events) >= 1
    assert events[0].type == "encounter"
    assert events[0].encounter is not None


@pytest.mark.vcr
def test_cassette_loitering_roundtrip(gfw_token: str) -> None:
    with EventsClient(gfw_token, page_size=5) as c:
        events = list(
            c.iter_events(
                ["2114e5305-5432-8186-f90a-88e088ce4dc6"],
                "loitering",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 6, 30),
            )
        )
    assert len(events) >= 1
    assert events[0].type == "loitering"
    assert events[0].loitering is not None
