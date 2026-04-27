"""GFW /v3/events client — near-real-time freshness layer.

Complements the monthly C4 voyages CSVs (see ADR 0002) with per-vessel event
queries that lag real-time by ~3 days instead of ~30. Used by the signal
pipeline to detect port visits, AIS gaps, STS encounters, and loitering
between monthly CSV releases.

Quirks observed against the live API (captured 2026-04-21):

- Endpoint is ``GET /v3/events`` (plural, not vessel-scoped).
- Query params use indexed arrays: ``datasets[0]``, ``vessels[0..N]``,
  ``confidences[0]``. Date params are ``start-date`` / ``end-date``
  (hyphenated, ``YYYY-MM-DD``).
- Pagination is offset/limit. Passing ``limit`` without ``offset`` → 422.
  ``nextOffset`` in the body is ``null`` once the window is exhausted.
- ``confidences[0]`` is valid *only* for port-visit datasets. Passing it on
  gap / encounter / loitering requests → 422. We gate that on the caller side.
- Unknown vessel ids do NOT 404 — the server returns an empty entries list
  with ``total: 0``. We pass that through as zero events.
- Several numeric fields arrive as strings ("4", "1054.83", "22").
  Pydantic's lax coercion handles str→int/float for us; no custom validators
  needed beyond ``extra="ignore"``.

Pydantic / SQLAlchemy rule from CLAUDE.md: this module defines runtime
Pydantic models, so it does NOT use ``from __future__ import annotations``.
"""

import logging
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, Literal, cast

import httpx
from pydantic import AliasGenerator, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

log = logging.getLogger(__name__)

BASE_URL: Final = "https://gateway.api.globalfishingwatch.org/v3"

EventKind = Literal["port_visit", "gap", "encounter", "loitering"]

DATASETS: Final[dict[EventKind, str]] = {
    "port_visit": "public-global-port-visits-events:latest",
    "gap": "public-global-gaps-events:latest",
    "encounter": "public-global-encounters-events:latest",
    "loitering": "public-global-loitering-events:latest",
}


class _EventModel(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        frozen=True,
        populate_by_name=True,
        alias_generator=AliasGenerator(validation_alias=to_camel),
    )


class EventPosition(_EventModel):
    lat: float
    lon: float


class EventVessel(_EventModel):
    id: str
    name: str | None = None
    ssvid: str | None = None
    flag: str | None = None
    type: str | None = None


class EventAnchorage(_EventModel):
    anchorage_id: str | None = None
    flag: str | None = None
    id: str | None = None
    lat: float | None = None
    lon: float | None = None
    name: str | None = None
    top_destination: str | None = None


class PortVisitDetails(_EventModel):
    visit_id: str | None = None
    # Server returns confidence as a string ("4") — Pydantic lax mode coerces.
    confidence: int | None = None
    duration_hrs: float | None = None
    start_anchorage: EventAnchorage | None = None
    intermediate_anchorage: EventAnchorage | None = None
    end_anchorage: EventAnchorage | None = None


class GapDetails(_EventModel):
    intentional_disabling: bool | None = None
    distance_km: float | None = None
    duration_hours: float | None = None
    implied_speed_knots: float | None = None
    positions_12_hours_before_sat: int | None = None
    positions_per_day_sat_reception: float | None = None
    off_position: EventPosition | None = None
    on_position: EventPosition | None = None


class EncounterDetails(_EventModel):
    vessel: EventVessel | None = None
    median_distance_kilometers: float | None = None
    median_speed_knots: float | None = None
    type: str | None = None
    potential_risk: bool | None = None


class LoiteringDetails(_EventModel):
    total_time_hours: float | None = None
    total_distance_km: float | None = None
    average_speed_knots: float | None = None
    average_distance_from_shore_km: float | None = None


class Event(_EventModel):
    """One /v3/events entry. Only the discriminator subfield matching ``type``
    will be populated; the rest stay ``None``."""

    id: str
    type: str
    start: datetime
    end: datetime
    position: EventPosition
    vessel: EventVessel
    port_visit: PortVisitDetails | None = Field(default=None, validation_alias="port_visit")
    gap: GapDetails | None = None
    encounter: EncounterDetails | None = None
    loitering: LoiteringDetails | None = None


@dataclass(frozen=True)
class BackoffConfig:
    initial_seconds: float = 5.0
    max_seconds: float = 300.0
    max_attempts: int = 8


class EventsClient:
    """Thin httpx wrapper around /v3/events with pagination + 429 backoff.

    Caller passes an iterable of GFW vessel_ids; pagination is handled
    transparently. On repeated 429s the method logs and returns (rather than
    raising) so a batch job doesn't crash on transient rate-limit pressure.
    """

    def __init__(
        self,
        token: str,
        *,
        timeout: float = 30.0,
        page_size: int = 100,
        vessel_batch_size: int = 50,
        backoff: BackoffConfig | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not token:
            raise ValueError("GFW_API_TOKEN is empty")
        if transport is None:
            self._client = httpx.Client(
                base_url=BASE_URL,
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout,
            )
        else:
            self._client = httpx.Client(
                base_url=BASE_URL,
                headers={"Authorization": f"Bearer {token}"},
                timeout=timeout,
                transport=transport,
            )
        self._page_size = page_size
        self._vessel_batch_size = vessel_batch_size
        self._backoff = backoff or BackoffConfig()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "EventsClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def iter_events(
        self,
        vessel_ids: Iterable[str],
        event_kind: EventKind,
        start_date: date,
        end_date: date,
        *,
        confidences: tuple[int, ...] = (4,),
    ) -> Iterator[Event]:
        """Yield events for `vessel_ids` in [start_date, end_date], paginating
        transparently and batching vessels per HTTP call. ``confidences`` is
        ignored for non-port-visit kinds (the API 422s if passed)."""
        dataset = DATASETS[event_kind]
        effective_confidences: tuple[int, ...] = confidences if event_kind == "port_visit" else ()
        vessel_list = list(vessel_ids)
        if not vessel_list:
            return

        for batch in _chunks(vessel_list, self._vessel_batch_size):
            yield from self._iter_batch(
                dataset=dataset,
                vessels=batch,
                start_date=start_date,
                end_date=end_date,
                confidences=effective_confidences,
            )

    def _iter_batch(
        self,
        *,
        dataset: str,
        vessels: list[str],
        start_date: date,
        end_date: date,
        confidences: tuple[int, ...],
    ) -> Iterator[Event]:
        offset = 0
        while True:
            params = build_params(
                dataset=dataset,
                vessels=vessels,
                start_date=start_date,
                end_date=end_date,
                confidences=confidences,
                limit=self._page_size,
                offset=offset,
            )
            body = self._get_with_backoff("/events", params)
            if body is None:
                return
            raw_entries: object = body.get("entries") or []
            if not isinstance(raw_entries, list):
                log.warning("GFW /events returned non-list entries: %r", type(raw_entries).__name__)
                return
            for raw in cast(list[object], raw_entries):  # noqa: TC006
                if isinstance(raw, dict):
                    yield Event.model_validate(raw)
            next_offset = body.get("nextOffset")
            if next_offset is None or not raw_entries:
                return
            try:
                candidate = int(next_offset)
            except (TypeError, ValueError):
                log.warning("GFW /events returned non-int nextOffset: %r", next_offset)
                return
            if candidate <= offset:
                log.warning(
                    "GFW /events nextOffset (%d) did not advance past current offset (%d); stopping",
                    candidate,
                    offset,
                )
                return
            offset = candidate

    def _get_with_backoff(self, url: str, params: dict[str, str | int]) -> dict[str, Any] | None:
        delay = self._backoff.initial_seconds
        for attempt in range(1, self._backoff.max_attempts + 1):
            r = self._client.get(url, params=params)
            if r.status_code == 429 or r.status_code >= 500:
                log.warning(
                    "GFW %d on %s; sleeping %.1fs before retry %d/%d",
                    r.status_code,
                    url,
                    delay,
                    attempt,
                    self._backoff.max_attempts,
                )
                time.sleep(delay)
                delay = min(delay * 2, self._backoff.max_seconds)
                continue
            r.raise_for_status()
            body = r.json()
            if isinstance(body, dict):
                return cast(dict[str, Any], body)  # noqa: TC006
            log.warning("GFW %s returned non-dict body: %r", url, type(body).__name__)
            return None
        log.error(
            "GFW %s exhausted %d transient-failure retries; skipping this batch",
            url,
            self._backoff.max_attempts,
        )
        return None


def build_params(
    *,
    dataset: str,
    vessels: list[str],
    start_date: date,
    end_date: date,
    confidences: tuple[int, ...],
    limit: int,
    offset: int,
) -> dict[str, str | int]:
    params: dict[str, str | int] = {
        "datasets[0]": dataset,
        "start-date": start_date.isoformat(),
        "end-date": end_date.isoformat(),
        "limit": limit,
        "offset": offset,
    }
    for i, vid in enumerate(vessels):
        params[f"vessels[{i}]"] = vid
    for i, c in enumerate(confidences):
        params[f"confidences[{i}]"] = c
    return params


def _chunks(seq: list[str], n: int) -> Iterator[list[str]]:
    if n <= 0:
        raise ValueError("chunk size must be positive")
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def event_to_flat_row(e: Event) -> dict[str, Any]:
    """Flatten one Event into a single-row dict suitable for parquet.

    Keeps the event-kind-specific subfields with a prefix so all events can go
    into a single parquet file. Columns absent on a given event become nulls.
    """
    row: dict[str, Any] = {
        "id": e.id,
        "type": e.type,
        "start": e.start,
        "end": e.end,
        "lat": e.position.lat,
        "lon": e.position.lon,
        "vessel_id": e.vessel.id,
        "vessel_name": e.vessel.name,
        "ssvid": e.vessel.ssvid,
        "flag": e.vessel.flag,
        "vessel_type": e.vessel.type,
    }
    if e.port_visit is not None:
        row.update(
            {
                "pv_visit_id": e.port_visit.visit_id,
                "pv_confidence": e.port_visit.confidence,
                "pv_duration_hrs": e.port_visit.duration_hrs,
                "pv_start_anchorage_id": (
                    e.port_visit.start_anchorage.anchorage_id
                    if e.port_visit.start_anchorage is not None
                    else None
                ),
                "pv_end_anchorage_id": (
                    e.port_visit.end_anchorage.anchorage_id
                    if e.port_visit.end_anchorage is not None
                    else None
                ),
                "pv_start_anchorage_name": (
                    e.port_visit.start_anchorage.name
                    if e.port_visit.start_anchorage is not None
                    else None
                ),
                "pv_end_anchorage_name": (
                    e.port_visit.end_anchorage.name
                    if e.port_visit.end_anchorage is not None
                    else None
                ),
                "pv_start_anchorage_flag": (
                    e.port_visit.start_anchorage.flag
                    if e.port_visit.start_anchorage is not None
                    else None
                ),
            }
        )
    if e.gap is not None:
        row.update(
            {
                "gap_intentional_disabling": e.gap.intentional_disabling,
                "gap_distance_km": e.gap.distance_km,
                "gap_duration_hours": e.gap.duration_hours,
                "gap_implied_speed_knots": e.gap.implied_speed_knots,
            }
        )
    if e.encounter is not None:
        row.update(
            {
                "enc_other_vessel_id": (
                    e.encounter.vessel.id if e.encounter.vessel is not None else None
                ),
                "enc_other_ssvid": (
                    e.encounter.vessel.ssvid if e.encounter.vessel is not None else None
                ),
                "enc_other_flag": (
                    e.encounter.vessel.flag if e.encounter.vessel is not None else None
                ),
                "enc_median_distance_km": e.encounter.median_distance_kilometers,
                "enc_median_speed_knots": e.encounter.median_speed_knots,
                "enc_type": e.encounter.type,
                "enc_potential_risk": e.encounter.potential_risk,
            }
        )
    if e.loitering is not None:
        row.update(
            {
                "loit_total_time_hours": e.loitering.total_time_hours,
                "loit_total_distance_km": e.loitering.total_distance_km,
                "loit_average_speed_knots": e.loitering.average_speed_knots,
                "loit_avg_distance_from_shore_km": e.loitering.average_distance_from_shore_km,
            }
        )
    return row
