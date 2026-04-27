# ADR 0004: VLCC-candidate classifier heuristic

- **Status**: accepted
- **Date**: 2026-04-21
- **Deciders**: Sean Parnell
- **Related**: [ADR 0002](0002-gfw-voyages-as-historical-source.md)

## Context

ADR 0002 committed us to GFW's All-Vessels Voyages C4 CSVs as the historical
backbone for TD3C backtesting. That dataset is keyed by `ssvid` (MMSI) and
GFW `vessel_id`, but carries **no ship-type, dwt, or LOA fields**. Before we
can compute tightness on "VLCC voyages only," we need to classify each
vessel as VLCC-candidate or not.

Three signals are available:

1. **GFW `/v3/vessels/{id}` identity** — enriched registry. Sometimes
   carries `tonnageGt` and `lengthM`; also carries a loose shiptypes list.
2. **Live-AIS `vessels` table** — from AISStream-fed ShipStaticData
   messages. Carries the AIS-broadcast `ship_type` code (80 = oil tanker)
   and dimensions. Only populated for vessels we've seen on the live feed.
3. **Duration heuristic** — a vessel appearing on a TD3C-filtered voyages
   manifest has already passed the route's typical transit-day band
   (18–35 days for TD3C). Per ADR 0002 Gap 1, ~70–80% of vessels in that
   band on PG→China are VLCCs.

We need to specify **thresholds** and the **cascade order** in which
these signals fire.

## Decision

### Cascade (first rule to fire wins)

1.  **AIS contradicts.** If live-AIS says this MMSI has a ship_type outside
    the ITU M.1371 tanker range (80-84), return `False / ais_static`. The
    tanker range includes 80 (general tanker), 81 (hazmat cat A — crude oil,
    many VLCCs broadcast), 82 (cat B — chemicals / products), 83 (cat C),
    and 84 (cat D — typically LNG). Codes outside this band (70=cargo,
    79=container, 30=fishing, etc.) are taken as ground-truth contradictions
    of GFW's enrichment.
2.  **GFW strict numeric.** `tonnage_gt ≥ 150,000` OR
    `registered_length_m ≥ 320` → `True / gfw_identity`.
3.  **GFW soft (shiptype + near-threshold size).**
    `"oil_tanker" in gfw_shiptypes` (case-insensitive) AND
    (`tonnage_gt ≥ 100,000` OR `length ≥ 280 m`) → `True / gfw_identity`.
4.  **AIS positive.** `ship_type` in tanker range (80-84) AND `length_m ≥ 320`
    → `True / ais_static`. Small tanker (`ship_type` in 80-84, `length < 320`)
    → `False / ais_static`.
5.  **GFW negative size.** `tonnage_gt < 50,000` → `False / gfw_identity`.
6.  **GFW non-tanker token.** Shiptypes explicitly contain
    `cargo/container/fishing/passenger/tug/bunker/pleasure_craft` and no
    tanker token → `False / gfw_identity`.
7.  **Duration heuristic fallback.** If identity exists but no rule above
    fired, and the vessel came from a TD3C-filtered voyage parquet →
    `True / duration_heuristic`.
8.  **None.** GFW 404s the vessel_id and no AIS fallback → `False / none`.

### Threshold choices

| Constant | Value | Why |
|---|---|---|
| `VLCC_GT_MIN` | 150,000 GT | VLCCs are typically 160–200k GT; pre-2000 VLCCs dip to ~150k. Setting the threshold below the modal mass avoids false-negatives on older units. |
| `VLCC_LENGTH_MIN_M` | 320 m | Matches `filters.VLCC_LENGTH_THRESHOLD_M` (live-AIS filtering). VLCC LOA cluster is 330 m ± 10 m; 320 m is a conservative lower bound with ~zero Suezmax overlap. |
| `VLCC_SOFT_GT_MIN` | 100,000 GT | Soft tier (shiptype-gated) captures the Suezmax–VLCC overlap zone where registry tonnage is sometimes understated. |
| `VLCC_SOFT_LENGTH_MIN_M` | 280 m | Captures long Suezmax / short VLCC crossover. Only fires when oil_tanker token is present, so false-positive rate is low. |
| `SMALL_TANKER_GT_MAX` | 50,000 GT | MR/LR product tankers cluster at 25–45k GT; anything below 50k is firmly NOT a crude carrier. |

### Why OR, not AND, across tonnage / length / shiptype

A pure AND would require all three signals to agree. In practice GFW
registry coverage is thin: `lengthM` is `null` for ~95% of the td3c-route
vessels we sampled (99 unique vessel_ids, 2026-03 month, TD3C); `tonnageGt`
is populated for ~55%; shiptype is `OTHER` for ~75%. Requiring AND would
reject most real VLCCs. OR with a cascade preserves recall while using the
AIS cross-ref (rule 1) to suppress obvious false-positives.

### Why a duration-heuristic fallback

Many legitimate VLCCs (observed: DHT CHINA) come back from GFW identity
with zero usable fields: no `tonnageGt`, no `lengthM`, `shiptypes = ["OTHER"]`.
Without the duration-heuristic fallback we'd mark them non-VLCC. The fact
that the vessel already passed the route+duration filter in `voyages.py`
is genuine signal — it got to where we care about, within the typical
VLCC transit window.

The trade-off is a documented ~20–30% false-positive rate on that tier.
Downstream phases that need higher precision can filter on
`classification_source == "gfw_identity"` only, at the cost of recall.

## Consequences

**Positive**

- Unblocks Phase 02+ (route distance, ton-miles, signal): they can
  `JOIN` voyages against `vessel_registry.parquet` and filter on
  `is_vlcc_candidate`.
- Exhaustive output: every requested `vessel_id` gets a row, including
  404s (as `classification_source="none"`). No silent drops.
- Idempotent cache: repeated runs only re-hit GFW for genuinely new
  vessel_ids. Adding new TD3C months doesn't re-fetch the whole universe.
- Graceful degradation: if Postgres is unreachable (live-AIS vessels
  table) the classifier proceeds without AIS cross-ref and logs a warning
  rather than crashing.

**Negative**

- False positives on Q-Flex/Q-Max LNG carriers (~160k GT) when AIS static
  isn't available OR when AIS ship_type is 84 (which we accept as
  tanker-consistent under the widened range). The tonnage/length rules at
  the GFW tier are what suppress LNG in backtests; AIS alone won't.
  Mitigation path: add an explicit AIS ship_type != 84 exclusion if the
  live stream shows these dominating the `ais_static`-classified subset.
- `duration_heuristic` tier is imprecise. Backtests must report the split
  by `classification_source` and sanity-check results on the
  `gfw_identity`-only subset.
- Thresholds are judgment calls rooted in observed distributions, not
  empirically optimized. Future phase (candidate) can tune them against a
  labeled validation set.

## Alternatives considered

- **Strict AND across tonnage, length, shiptype.** Rejected — GFW
  registry coverage is too thin; recall would collapse.
- **Single GT threshold (≥150k) with no fallbacks.** Rejected — ~30% of
  TD3C vessels have `tonnage_gt = null`.
- **Require AIS static match for every vessel.** Rejected — the live-AIS
  coverage gap (VLCCs entering our stream only after we've backtested
  them) would make backtesting impossible.
- **Accept only AIS ship_type == 80 (strict).** Rejected — real VLCCs
  broadcast 81 (hazmat cat A, crude oil) routinely. A strict 80-only rule
  would mark them False/ais_static under rule 1 even when GFW tonnage
  clearly says VLCC. Widened to 80-84 at the cost of permitting LNG
  carriers through the AIS gate.
- **IMO-number cross-reference to a third-party registry
  (Equasis / IHS Markit).** Kept as an upgrade path — would materially
  improve precision but costs $ and introduces a new external dependency.
  Not in v0 scope.
- **Use GFW `registry_shiptype` field directly.** Observed to be `null`
  for almost every TD3C vessel; not a usable signal at this dataset
  quality.
