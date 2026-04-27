"""Pydantic models for AISStream.io messages.

Only fields we actually consume are typed. Unknown fields are ignored so
AISStream can extend their schema without breaking us.

We deliberately do NOT use `from __future__ import annotations`: Pydantic's
validator needs the referenced types (datetime, etc.) resolvable at runtime.
"""

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# AISStream sends time_utc as Go's default time format:
#   "2026-04-21 05:06:15.42106193 +0000 UTC"
# This regex normalizes it to ISO 8601 so datetime.fromisoformat (which
# Pydantic uses under the hood) can parse it.
_GO_TZ_RE = re.compile(r" ([+-]\d{2})(\d{2})\b")
_FRAC_RE = re.compile(r"\.(\d{6})\d+")


class _AisModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class AisDimension(_AisModel):
    """Vessel dimensions in meters: A=bow offset, B=stern offset,
    C=port offset, D=starboard offset (from the AIS reporting reference)."""

    A: int = 0
    B: int = 0
    C: int = 0
    D: int = 0

    @property
    def length_m(self) -> int:
        return self.A + self.B

    @property
    def beam_m(self) -> int:
        return self.C + self.D


class PositionReport(_AisModel):
    UserID: int  # MMSI
    Latitude: float
    Longitude: float
    Sog: float | None = None
    Cog: float | None = None
    TrueHeading: int | None = None
    NavigationalStatus: int | None = None
    Valid: bool = True


class ShipStaticData(_AisModel):
    UserID: int  # MMSI
    Type: int = 0
    Name: str = ""
    CallSign: str = ""
    ImoNumber: int = 0
    Dimension: AisDimension = Field(default_factory=AisDimension)
    MaximumStaticDraught: float = 0.0
    Destination: str = ""
    Valid: bool = True


class MetaData(_AisModel):
    MMSI: int
    ShipName: str = ""
    latitude: float
    longitude: float
    time_utc: datetime

    @field_validator("time_utc", mode="before")
    @classmethod
    def _normalize_go_time(cls, v: object) -> object:
        if not isinstance(v, str):
            return v
        s = v.removesuffix(" UTC").strip()
        s = _GO_TZ_RE.sub(r"\1:\2", s)  # " +0000" → "+00:00"
        s = s.replace(" ", "T", 1)  # date/time separator
        s = _FRAC_RE.sub(r".\1", s)  # truncate ns → us
        return s


class AISEnvelope(_AisModel):
    MessageType: str
    Message: dict[str, dict[str, Any]]
    MetaData: MetaData
