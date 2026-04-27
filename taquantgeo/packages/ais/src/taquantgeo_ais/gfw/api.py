"""Minimal GFW REST API client.

The free/non-commercial tier of the v3 API is what we use. Covers vessel
identity lookups by `vesselId` (not name — name search is noisy on tankers).
Research-tier BigQuery access is a separate grant and is NOT used here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

BASE_URL = "https://gateway.api.globalfishingwatch.org/v3"
DEFAULT_IDENTITY_DATASET = "public-global-vessel-identity:latest"


@dataclass(frozen=True)
class VesselIdentity:
    """Classification and registry info for one vessel, as returned by GFW."""

    vessel_id: str
    mmsi: int | None
    imo: str | None
    name: str | None
    flag: str | None
    gfw_shiptypes: tuple[str, ...]
    registry_shiptype: str | None
    length_m: float | None
    tonnage_gt: float | None


class GfwClient:
    def __init__(
        self,
        token: str,
        *,
        timeout: float = 15.0,
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

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GfwClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_vessel_identity(
        self, vessel_id: str, *, dataset: str = DEFAULT_IDENTITY_DATASET
    ) -> VesselIdentity | None:
        """Fetch identity for a single vessel. Returns None on 404."""
        r = self._client.get(f"/vessels/{vessel_id}", params={"dataset": dataset})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return _parse_identity(vessel_id, r.json())

    def iter_vessel_identities(
        self, vessel_ids: Iterable[str], *, dataset: str = DEFAULT_IDENTITY_DATASET
    ) -> Iterator[VesselIdentity | None]:
        """Lookup many vessels sequentially. Yields None for 404s so callers
        can line results up with inputs."""
        for vid in vessel_ids:
            yield self.get_vessel_identity(vid, dataset=dataset)


def _parse_identity(vessel_id: str, body: dict[str, object]) -> VesselIdentity:
    sri = _first_dict(body.get("selfReportedInfo"))
    csi = _first_dict(body.get("combinedSourcesInfo"))
    reg = _first_dict(body.get("registryInfo"))

    shiptypes = tuple(
        str(t.get("name"))
        for t in (csi.get("shiptypes") or [])
        if isinstance(t, dict) and t.get("name")
    )

    ssvid = sri.get("ssvid")
    mmsi: int | None = None
    if ssvid is not None:
        try:
            mmsi = int(ssvid)
        except (TypeError, ValueError):
            mmsi = None

    return VesselIdentity(
        vessel_id=vessel_id,
        mmsi=mmsi,
        imo=(str(sri.get("imo")) if sri.get("imo") is not None else None),
        name=(sri.get("shipname") or None),
        flag=reg.get("flag") or sri.get("flag") or None,
        gfw_shiptypes=shiptypes,
        registry_shiptype=reg.get("shiptype") or None,
        length_m=_maybe_float(reg.get("lengthM")),
        tonnage_gt=_maybe_float(reg.get("tonnageGt")),
    )


def _first_dict(value: object) -> dict[str, object]:
    """Return the first dict from a list-of-dicts field, or an empty dict."""
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return {}


def _maybe_float(v: object) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
