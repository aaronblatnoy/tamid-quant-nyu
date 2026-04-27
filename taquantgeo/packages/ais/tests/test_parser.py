"""Parser tests using realistic AISStream message shapes."""

from __future__ import annotations

import json
from typing import Any

from taquantgeo_ais.parser import parse_envelope, parse_position, parse_static


def _envelope(message_type: str, body: dict[str, Any], mmsi: int = 538003170) -> bytes:
    return json.dumps(
        {
            "MessageType": message_type,
            "Message": {message_type: body},
            "MetaData": {
                "MMSI": mmsi,
                "ShipName": "TEST VESSEL",
                "latitude": 25.0,
                "longitude": 55.0,
                "time_utc": "2026-04-21T12:00:00Z",
            },
        }
    ).encode("utf-8")


def test_parse_envelope_drops_invalid_json() -> None:
    assert parse_envelope(b"not json") is None


def test_parse_envelope_drops_missing_required_fields() -> None:
    assert parse_envelope(b'{"foo": "bar"}') is None


def test_parse_position_round_trip() -> None:
    raw = _envelope(
        "PositionReport",
        {
            "UserID": 538003170,
            "Latitude": 25.5,
            "Longitude": 55.2,
            "Sog": 12.3,
            "Cog": 90.0,
            "TrueHeading": 91,
            "NavigationalStatus": 0,
            "Valid": True,
        },
    )
    env = parse_envelope(raw)
    assert env is not None
    pos = parse_position(env)
    assert pos is not None
    assert pos.UserID == 538003170
    assert pos.Latitude == 25.5
    assert pos.Sog == 12.3


def test_parse_position_returns_none_for_static_envelope() -> None:
    raw = _envelope("ShipStaticData", {"UserID": 1, "Type": 80})
    env = parse_envelope(raw)
    assert env is not None
    assert parse_position(env) is None


def test_parse_static_round_trip() -> None:
    raw = _envelope(
        "ShipStaticData",
        {
            "UserID": 538003170,
            "Type": 80,
            "Name": "VLCC ATLANTIS         ",
            "CallSign": "9HA1234",
            "ImoNumber": 9876543,
            "Dimension": {"A": 200, "B": 130, "C": 30, "D": 30},
            "MaximumStaticDraught": 22.5,
            "Valid": True,
        },
    )
    env = parse_envelope(raw)
    assert env is not None
    static = parse_static(env)
    assert static is not None
    assert static.Type == 80
    assert static.Dimension.length_m == 330
    assert static.Dimension.beam_m == 60
    assert static.ImoNumber == 9876543


def test_class_b_position_parses() -> None:
    raw = _envelope(
        "StandardClassBPositionReport",
        {"UserID": 1, "Latitude": 0.0, "Longitude": 0.0},
    )
    env = parse_envelope(raw)
    assert env is not None
    pos = parse_position(env)
    assert pos is not None
    assert pos.UserID == 1


def test_unknown_message_type_returns_none_for_both_parsers() -> None:
    raw = _envelope("BaseStationReport", {"UserID": 1})
    env = parse_envelope(raw)
    assert env is not None
    assert parse_position(env) is None
    assert parse_static(env) is None
