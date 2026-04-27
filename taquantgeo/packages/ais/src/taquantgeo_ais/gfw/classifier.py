"""GFW vessel-identity batch classifier.

Produces a vessel registry parquet that tags each GFW ``vessel_id`` as
VLCC-candidate or not. Downstream pipelines join against it to filter
TD3C voyages to VLCC-only before computing the tightness signal.

Heuristic (full rationale in ADR 0004) — the first rule to fire wins:

1.  AIS static contradicts: if the live-AIS ``vessels`` table knows this
    MMSI with an AIS ``ship_type`` outside the ITU M.1371 tanker range
    80--84 inclusive (e.g. cargo=70, container=79), return ``False`` with
    source ``ais_static``. LNG carriers often broadcast ``84``; they are
    in-range, so rule~1 does not reject them on ``ship_type`` alone; LNG
    false-positive suppression relies on tonnage/length thresholds
    elsewhere, not on ``ship_type``. Trust AIS ground truth over GFW's
    enriched labels when they disagree.
2.  GFW identity — strict numeric: ``tonnage_gt >= 150_000`` OR
    ``registered_length_m >= 320`` → ``True`` / ``gfw_identity``.
3.  GFW identity — soft (shiptype + near-threshold size): ``oil_tanker``
    token present in ``gfw_shiptypes`` AND (``tonnage_gt >= 100_000``
    OR ``registered_length_m >= 280``) → ``True`` / ``gfw_identity``.
4.  AIS static — positive: ``ship_type`` in 80--84 (ITU tanker subtypes)
    AND ``length_m >= 320`` → ``True`` / ``ais_static``. Tanker broadcast
    but too short (e.g. ``ship_type`` in 80--84 but length < 320) →
    ``False`` / ``ais_static``. LNG carriers (often broadcast ``84``) can
    match this branch when long enough; LNG false-positive suppression
    relies on tonnage/length thresholds elsewhere, not on ``ship_type``.
5.  GFW identity — negative size: ``tonnage_gt < 50_000`` → ``False`` /
    ``gfw_identity``. (Firm small-tanker evidence.)
6.  GFW identity — non-tanker token: shiptypes explicitly contain
    ``cargo``/``container``/``fishing`` and no tanker token → ``False``
    / ``gfw_identity``.
7.  Duration heuristic — vessel appeared on a TD3C-filtered voyages
    manifest (already narrowed to 18-35 day transit band per
    ``routes.TD3C.typical_transit_days``). If identity exists but
    nothing above fired, ~70-80% of these are VLCCs per ADR 0002 gap 1;
    return ``True`` / ``duration_heuristic``.
8.  Default — ``False`` / ``none``. Reached when GFW 404s the vessel_id
    and we have no AIS static fallback.

Quirks observed running against the live API (captured 2026-04-21):

- ``registryInfo`` is an empty list for the vast majority of vessels.
  When present, ``lengthM`` is still usually ``null``; ``tonnageGt`` is
  filled more often. Our cascade treats missing fields as "no signal"
  rather than "negative signal".
- ``combinedSourcesInfo.shiptypes[].name`` is an UPPERCASE token
  (``OTHER``, ``CARGO``, ``OIL_TANKER``, ``NA``). We match
  case-insensitively.
- Many legitimate VLCCs (e.g. ``DHT CHINA``) come back with no size
  fields at all and shiptype ``OTHER``. The duration heuristic is the
  only thing that rescues them; without it we'd underclassify.
- Q-Flex / Q-Max LNG carriers at ~160k GT will tonnage-match the strict
  VLCC rule. We accept the false-positive rate at this stage because
  AIS-static cross-reference (ship_type=84 for LNG) overrides. When no
  AIS static is available, downstream phases may still apply an IMO /
  registry lookup; out of scope here.
- 404 on ``/vessels/{id}`` is genuinely "GFW does not know this id" —
  every row still goes in the parquet with ``classification_source=
  "none"`` so the registry is exhaustive vs the input set.

This module defines frozen dataclasses and pure functions at module
scope; like ``api.py`` it uses ``from __future__ import annotations``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Literal

import polars as pl
from sqlalchemy import select

from taquantgeo_ais.filters import SHIP_TYPE_OIL_TANKER, VLCC_LENGTH_THRESHOLD_M
from taquantgeo_core.db import session_scope
from taquantgeo_core.schemas import Vessel

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from taquantgeo_ais.gfw.api import GfwClient, VesselIdentity

log = logging.getLogger(__name__)

VLCC_GT_MIN: Final = 150_000.0
"""Strict tonnage threshold for VLCC classification (gross tons)."""

VLCC_LENGTH_MIN_M: Final = float(VLCC_LENGTH_THRESHOLD_M)
"""Strict length threshold — matches ``filters.VLCC_LENGTH_THRESHOLD_M``."""

AIS_TANKER_CODE_RANGE: Final = range(80, 85)
"""ITU M.1371 AIS ship_type codes 80-84 inclusive (all tanker subtypes)."""

VLCC_SOFT_GT_MIN: Final = 100_000.0
"""Near-threshold tonnage used only when shiptypes contains an oil-tanker token."""

VLCC_SOFT_LENGTH_MIN_M: Final = 280.0
"""Near-threshold length used only when shiptypes contains an oil-tanker token."""

SMALL_TANKER_GT_MAX: Final = 50_000.0
"""Below this we assert "not VLCC" with confidence (product tankers, small MRs)."""

_OIL_TANKER_TOKEN: Final = "oil_tanker"  # noqa: S105 — this is a shiptype token string, not a password
_NON_TANKER_TOKENS: Final = frozenset(
    {"cargo", "container", "fishing", "passenger", "tug", "bunker", "pleasure_craft"}
)

ClassificationSource = Literal["gfw_identity", "ais_static", "duration_heuristic", "none"]

VLCC_HEURISTIC: Final = (
    "VLCC-candidate if any of: "
    f"(a) GFW gross_tonnage >= {VLCC_GT_MIN:.0f} GT; "
    f"(b) GFW registered_length >= {VLCC_LENGTH_MIN_M:.0f} m; "
    f"(c) oil_tanker shiptype AND (GT >= {VLCC_SOFT_GT_MIN:.0f} OR len >= {VLCC_SOFT_LENGTH_MIN_M:.0f} m); "
    f"(d) AIS ship_type in tanker range ({SHIP_TYPE_OIL_TANKER}-84) AND length >= {VLCC_LENGTH_MIN_M:.0f} m; "
    "(e) vessel appeared on a TD3C-filtered voyages manifest (duration band 18-35 days). "
    f"Firm NOT VLCC if: AIS ship_type set and NOT in tanker range {SHIP_TYPE_OIL_TANKER}-84; "
    f"or GFW GT < {SMALL_TANKER_GT_MAX:.0f}; or non-tanker shiptype. "
    "404 from GFW with no AIS fallback → 'none'."
)


@dataclass(frozen=True)
class AisStaticRef:
    """Minimal projection of the AIS ``vessels`` row we need for cross-ref."""

    ship_type: int | None
    length_m: int | None


def _has_token(shiptypes: tuple[str, ...], token: str) -> bool:
    token_lower = token.lower()
    return any(token_lower in st.lower() for st in shiptypes)


def _has_any_token(shiptypes: tuple[str, ...], tokens: frozenset[str]) -> bool:
    lowered = {st.lower() for st in shiptypes}
    return any(any(tok in s for s in lowered) for tok in tokens)


def classify_one(  # noqa: PLR0911, PLR0912
    identity: VesselIdentity | None,
    ais_static: AisStaticRef | None,
    *,
    from_td3c_route: bool,
) -> tuple[bool, ClassificationSource]:
    """Apply the VLCC heuristic cascade to one vessel. See module docstring."""

    # Rule 1: AIS says this is not any tanker subtype → contradicts GFW.
    if (
        ais_static is not None
        and ais_static.ship_type is not None
        and ais_static.ship_type not in AIS_TANKER_CODE_RANGE
    ):
        return False, "ais_static"

    # Rule 2: GFW strict numeric.
    if identity is not None:
        if identity.tonnage_gt is not None and identity.tonnage_gt >= VLCC_GT_MIN:
            return True, "gfw_identity"
        if identity.length_m is not None and identity.length_m >= VLCC_LENGTH_MIN_M:
            return True, "gfw_identity"

        # Rule 3: GFW soft — oil_tanker token + near-threshold size.
        if _has_token(identity.gfw_shiptypes, _OIL_TANKER_TOKEN):
            if identity.tonnage_gt is not None and identity.tonnage_gt >= VLCC_SOFT_GT_MIN:
                return True, "gfw_identity"
            if identity.length_m is not None and identity.length_m >= VLCC_SOFT_LENGTH_MIN_M:
                return True, "gfw_identity"

    # Rule 4: AIS-static positive.
    if ais_static is not None and ais_static.ship_type in AIS_TANKER_CODE_RANGE:
        if ais_static.length_m is not None and ais_static.length_m >= VLCC_LENGTH_MIN_M:
            return True, "ais_static"
        # AIS says oil tanker but too small → firm negative.
        if ais_static.length_m is not None:
            return False, "ais_static"

    # Rule 5: GFW negative — small tanker by tonnage.
    if identity is not None:
        if identity.tonnage_gt is not None and identity.tonnage_gt < SMALL_TANKER_GT_MAX:
            return False, "gfw_identity"

        # Rule 6: non-tanker token without any tanker token.
        has_tanker = _has_token(identity.gfw_shiptypes, _OIL_TANKER_TOKEN) or _has_token(
            identity.gfw_shiptypes, "tanker"
        )
        if not has_tanker and _has_any_token(identity.gfw_shiptypes, _NON_TANKER_TOKENS):
            return False, "gfw_identity"

    # Rule 7: duration heuristic — we saw this vessel on a TD3C voyage and GFW
    # knows it (identity is not None). Per ADR 0002 Gap 1 the transit-band
    # filter already narrowed to ~70-80% VLCC prevalence on PG→China.
    if from_td3c_route and identity is not None:
        return True, "duration_heuristic"

    # Rule 8: nothing fired; GFW didn't know this id and AIS static couldn't help.
    return False, "none"


# Column order is locked because it's part of the phase acceptance contract.
_REGISTRY_COLUMN_ORDER: Final = (
    "mmsi",
    "vessel_id",
    "imo",
    "name",
    "flag",
    "gfw_shiptypes",
    "gross_tonnage",
    "registered_length_m",
    "is_vlcc_candidate",
    "classification_source",
    "ais_ship_type",
    "fetched_at",
)

_REGISTRY_SCHEMA: Final = pl.Schema(
    {
        "mmsi": pl.Int64,
        "vessel_id": pl.String,
        "imo": pl.String,
        "name": pl.String,
        "flag": pl.String,
        "gfw_shiptypes": pl.List(pl.String),
        "gross_tonnage": pl.Float64,
        "registered_length_m": pl.Float64,
        "is_vlcc_candidate": pl.Boolean,
        "classification_source": pl.String,
        "ais_ship_type": pl.Int64,
        "fetched_at": pl.Datetime(time_unit="us", time_zone="UTC"),
    }
)


def _load_existing(out_path: Path) -> pl.DataFrame | None:
    if not out_path.exists():
        return None
    try:
        df = pl.read_parquet(out_path)
    except (OSError, pl.exceptions.ComputeError):
        log.warning("existing registry at %s is unreadable; ignoring cache", out_path)
        return None
    # Enforce our canonical column order; extra/missing columns drop or default.
    missing = [c for c in _REGISTRY_COLUMN_ORDER if c not in df.columns]
    if missing:
        log.warning(
            "existing registry at %s is missing columns %s; ignoring cache", out_path, missing
        )
        return None
    return df.select(_REGISTRY_COLUMN_ORDER)


def _vessel_ids_to_fetch(
    requested: list[str], cached: pl.DataFrame | None, *, force: bool
) -> list[str]:
    if force or cached is None or cached.is_empty():
        return requested
    known = set(cached["vessel_id"].to_list())
    return [v for v in requested if v not in known]


def _row_from_identity(
    identity: VesselIdentity | None,
    *,
    vessel_id: str,
    ais_lookup: Mapping[int, AisStaticRef] | None,
    from_td3c_route: bool,
    fetched_at: datetime,
) -> dict[str, object]:
    mmsi = identity.mmsi if identity is not None else None
    ais_ref = ais_lookup.get(mmsi) if (ais_lookup is not None and mmsi is not None) else None

    is_vlcc, source = classify_one(identity, ais_ref, from_td3c_route=from_td3c_route)

    return {
        "mmsi": mmsi,
        "vessel_id": vessel_id,
        "imo": identity.imo if identity is not None else None,
        "name": identity.name if identity is not None else None,
        "flag": identity.flag if identity is not None else None,
        "gfw_shiptypes": (list(identity.gfw_shiptypes) if identity is not None else []),
        "gross_tonnage": identity.tonnage_gt if identity is not None else None,
        "registered_length_m": identity.length_m if identity is not None else None,
        "is_vlcc_candidate": is_vlcc,
        "classification_source": source,
        "ais_ship_type": ais_ref.ship_type if ais_ref is not None else None,
        "fetched_at": fetched_at,
    }


def classify_vessels(
    vessel_ids: Iterable[str],
    client: GfwClient,
    *,
    out_path: Path,
    ais_lookup: Mapping[int, AisStaticRef] | None = None,
    from_td3c_route: bool = True,
    force: bool = False,
) -> pl.DataFrame:
    """Fetch GFW identity for each ``vessel_id``, classify, write parquet, return df.

    Behaviour
    ---------
    - Idempotent: if ``out_path`` exists and ``force`` is ``False``, only
      previously-unseen vessel_ids hit the network. Existing rows are
      preserved verbatim (including their ``fetched_at`` timestamp).
    - Exhaustive: every vessel_id in ``vessel_ids`` appears in the output,
      even on 404 (``classification_source="none"``).
    - AIS cross-ref: when ``ais_lookup`` has the vessel's MMSI, the
      ``ais_ship_type`` column is populated. If the AIS record contradicts
      GFW (non-oil-tanker), it overrides the classification per rule 1.
    - ``from_td3c_route`` defaults ``True`` because this is how the CLI
      invokes it; pass ``False`` when classifying an arbitrary vessel set.

    Returns a DataFrame matching the acceptance-criterion column order and
    schema.
    """

    requested = list(dict.fromkeys(vessel_ids))  # dedupe, preserve order
    cached = _load_existing(out_path)
    to_fetch = _vessel_ids_to_fetch(requested, cached, force=force)

    log.info(
        "classify_vessels: %d requested, %d cached, %d to fetch (force=%s)",
        len(requested),
        0 if cached is None else cached.height,
        len(to_fetch),
        force,
    )

    fetched_at = datetime.now(tz=UTC)
    new_rows: list[dict[str, object]] = []
    for vid, identity in zip(to_fetch, client.iter_vessel_identities(to_fetch), strict=True):
        new_rows.append(
            _row_from_identity(
                identity,
                vessel_id=vid,
                ais_lookup=ais_lookup,
                from_td3c_route=from_td3c_route,
                fetched_at=fetched_at,
            )
        )

    new_df = (
        pl.DataFrame(new_rows, schema=_REGISTRY_SCHEMA)
        if new_rows
        else pl.DataFrame(schema=_REGISTRY_SCHEMA)
    )

    # Merge cache + new, then filter to the requested set so stale cached rows
    # from prior runs aren't silently propagated when the caller's universe shrinks.
    merged = new_df if cached is None else pl.concat([cached, new_df], how="vertical_relaxed")
    if force:
        # On force, rows in new_df supersede cached ones for the same vessel_id.
        merged = merged.unique(subset=["vessel_id"], keep="last", maintain_order=True)
    else:
        merged = merged.unique(subset=["vessel_id"], keep="first", maintain_order=True)

    requested_set = set(requested)
    merged = merged.filter(pl.col("vessel_id").is_in(requested_set))

    # Preserve input ordering in the output.
    order_df = pl.DataFrame({"vessel_id": requested, "__order": list(range(len(requested)))})
    merged = (
        merged.join(order_df, on="vessel_id", how="left")
        .sort("__order")
        .drop("__order")
        .select(_REGISTRY_COLUMN_ORDER)
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.write_parquet(out_path)

    log.info("wrote %d rows → %s", merged.height, out_path)
    return merged


def load_ais_static_lookup(mmsis: Iterable[int]) -> dict[int, AisStaticRef]:
    """Look up AIS static dims from the operational Postgres ``vessels`` table.

    Returns an empty dict if the DB is unreachable — the classifier degrades
    gracefully rather than failing the whole batch.
    """
    mmsi_set = list({int(m) for m in mmsis})
    if not mmsi_set:
        return {}
    try:
        with session_scope() as s:
            rows = s.execute(
                select(Vessel.mmsi, Vessel.ship_type, Vessel.length_m).where(
                    Vessel.mmsi.in_(mmsi_set)
                )
            ).all()
    except Exception as e:
        log.warning("AIS static lookup failed (%s); proceeding without cross-ref", e)
        return {}
    return {
        int(mmsi): AisStaticRef(ship_type=ship_type, length_m=length_m)
        for mmsi, ship_type, length_m in rows
    }


def read_vessel_ids_from_voyages(voyages_dir: Path) -> tuple[list[str], list[int]]:
    """Scan a route-partitioned voyages directory, return (vessel_ids, mmsis).

    Both lists are deduplicated and ordered by first appearance. ``mmsis``
    drops nulls. Uses polars lazy scan so large trees stream without loading
    the whole universe into memory.
    """
    pattern = str(voyages_dir / "**" / "*.parquet")
    try:
        lf = pl.scan_parquet(pattern)
        df = lf.select(["vessel_id", "ssvid"]).unique().collect()
    except (FileNotFoundError, pl.exceptions.ComputeError) as e:
        log.warning("no parquet files under voyages_dir %s (or scan failed): %s", voyages_dir, e)
        return [], []
    vessel_ids = df["vessel_id"].drop_nulls().unique(maintain_order=True).to_list()
    mmsis = [int(m) for m in df["ssvid"].drop_nulls().unique(maintain_order=True).to_list()]
    return vessel_ids, mmsis
