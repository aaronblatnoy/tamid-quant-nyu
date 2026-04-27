"""Parse AISStream.io JSON messages into typed Pydantic objects."""

from __future__ import annotations

import json

from pydantic import ValidationError

from taquantgeo_ais.models import AISEnvelope, PositionReport, ShipStaticData

POSITION_TYPES = frozenset(
    {
        "PositionReport",
        "StandardClassBPositionReport",
        "ExtendedClassBPositionReport",
    }
)
STATIC_TYPES = frozenset({"ShipStaticData", "StaticDataReport"})


def parse_envelope(raw: bytes | str) -> AISEnvelope | None:
    """Parse the outer AISStream envelope. Returns None on malformed input
    so the caller can drop unknown shapes silently."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    try:
        return AISEnvelope.model_validate(data)
    except ValidationError:
        return None


def parse_position(env: AISEnvelope) -> PositionReport | None:
    if env.MessageType not in POSITION_TYPES:
        return None
    body = env.Message.get(env.MessageType)
    if not body:
        return None
    try:
        return PositionReport.model_validate(body)
    except ValidationError:
        return None


def parse_static(env: AISEnvelope) -> ShipStaticData | None:
    if env.MessageType not in STATIC_TYPES:
        return None
    body = env.Message.get(env.MessageType)
    if not body:
        return None
    try:
        return ShipStaticData.model_validate(body)
    except ValidationError:
        return None
