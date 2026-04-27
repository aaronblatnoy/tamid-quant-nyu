# ADR 0006: SAR × voyages dark-fleet cross-reference

- **Status**: accepted
- **Date**: 2026-04-21
- **Deciders**: Sean Parnell
- **Related**: [ADR 0002](0002-gfw-voyages-as-historical-source.md) (Gap 2)

## Context

The TD3C tightness signal relies on VLCC ballast-leg count (supply) and
voyage ton-miles (demand). GFW's C4 voyages dataset — our primary supply
source — systematically excludes VLCCs whose AIS transponder was off or
spoofed during loading or transit. The gap is material: industry
estimates for the sanctioned/dark fleet have grown past 600 VLCCs
post-2022 sanctions against Russian and Iranian crude, a meaningful
fraction of the global ~900-vessel VLCC fleet. If our signal undercounts
supply because of systematic AIS-off behaviour, we'll misprice
tightness — especially for cargos lifted at Kharg (IRN) and Basrah
(IRQ, some dark loading) which are over-represented in the dark-fleet
tail.

GFW publishes monthly `sar_vessel_detections_pipev4_YYYYMM.csv` — a
global roll-up of Sentinel-1 Synthetic Aperture Radar detections of
vessels at sea, independent of AIS. Each detection carries lat/lon,
measured length, presence/matching/fishing scores, matched MMSI (NULL
when no AIS broadcast was seen), and a matched_category label. A
detection with `mmsi IS NULL AND length >= 200m` at a VLCC loading
terminal is a strong dark-VLCC candidate.

## Decision

**Pipeline**: load SAR CSVs → date-window → length-filter (≥ 200 m) →
spatial-filter (≤ 10 km of any major loading terminal's nearest GFW
anchorage) → temporal cross-reference with our AIS-reported voyages
(±3 days) → write `data/processed/dark_fleet_candidates.parquet`.

**Length threshold: 200 m**. VLCCs are universally longer than 300 m;
Suezmax is ~270 m; LR2 product tankers start at ~250 m. Dropping below
200 m eliminates almost every containership/bulker/product-tanker
class while retaining the full crude-tanker tail. We do NOT filter on
`matched_category` because SAR-derived category labels are learned
features of the footprint and misclassification of a dark VLCC as
`other` or `noisy_vessel` would silently exclude exactly the vessel we
most want to count.

**Buffer: 10 km**. GFW anchorages sit at the port approach (not the
berth itself); a VLCC at the actual loading dock is typically 2–8 km
from the anchorage centroid. 10 km is generous enough to cover
approach-anchorage holding and dock positions while tight enough to
exclude vessels merely transiting the terminal's coast. The `buffer_km`
CLI knob lets us tune per-terminal if data justifies; at 10 km the real
2026-03 sample shows 17 SAR detections across 6 of 11 terminals —
plausible population density, no obvious false positives from mid-Gulf
transit.

**Time window: ±3 days**. Worldscale crude lifts typically span 24–48 h
at the berth. Sentinel-1 overpass cadence is ~6 days at equator, ~2–3
days at mid-latitudes. A 3-day window on either side of a voyage's
trip_start catches the loading-and-departure envelope while being tight
enough that a SAR hit 10 days after a voyage departure flags as dark
(rightly — that SAR hit is a different vessel or a different visit).

**Terminal resolution**: each `MAJOR_LOADING_TERMINALS` entry resolves
to the single nearest same-iso3 anchorage. Restricting to same-iso3
prevents pathological cross-border matches (e.g. Fujairah resolving to
an Omani anchorage 20 km south). If no same-iso3 anchorage exists for
a terminal, the terminal is dropped from the pipeline with a WARN —
this is what we want for local-dev fixtures (e.g. a test with only SAU
anchorages ignores all non-SAU terminals cleanly).

**NULL-MMSI detections are kept, not dropped**. A detection without a
matched MMSI is — by construction — the strongest dark signal. ~27% of
SAR detections globally have NULL MMSI; most are small non-AIS vessels
but the subset in the ≥200 m × PG-terminal intersection is dominated
by transponder-off tankers. Reporting `mmsi_null_count` separately in
the CLI summary lets the operator see the strength of the dark signal
at a glance.

## Consequences

**Positive**

- **Closes ADR 0002 Gap 2 for the loading-terminal segment.** SAR sees
  vessels AIS cannot; cross-referencing with our voyages yields the
  declared-supply adjustment phase 04 needs.
- **Cheap**. GFW SAR CSVs are free (requires a research token we
  already hold). No paid Spire / commercial satellite feed.
- **Orthogonal to AIS coverage failures.** Any future AIS outage or
  spoofing wave shows up as a jump in SAR dark-candidate count
  without code changes.
- **Attributable per-terminal**. The output parquet carries
  `nearest_anchorage_id` and `nearest_anchorage_label`; phase 04 can
  weight dark supply per loading terminal, and the operator can audit
  which terminals are most opaque (useful for monitoring sanction
  enforcement dynamics).

**Negative**

- **Global voyages data is not TD3C-scoped.** The cross-reference
  checks our existing `data/processed/voyages` tree which currently
  holds *only* TD3C-filtered voyages. A VLCC that loaded at Ras Tanura
  and sailed to Europe (a TD23 voyage) would not appear in our voyages
  and would be flagged as a dark candidate — a false positive for
  "dark to TD3C" but still a valid "not on our radar" candidate.
  For v0 we accept this as over-counting the dark adjustment; a future
  phase can ingest a global (not-TD3C-only) voyages tree for the
  cross-reference when we need tighter attribution.
- **SAR coverage is non-uniform**. Sentinel-1 is designed for coastal
  and EEZ surveillance. Mid-Indian-Ocean dark-fleet transit is
  under-observed; a dark VLCC that loads at Kharg, disables AIS, and
  transits mid-ocean may be invisible to SAR entirely. We flag this
  in the module docstring. Partial mitigation: loading terminals (PG)
  and discharge terminals (CHN) have dense SAR coverage, so the
  "dark at loading" signal is the strongest we can emit today.
  Reconsider mid-ocean coverage if/when Spire academic access is
  granted (candidate phase).
- **3-day window is a judgment call**. Too tight and we miss legitimate
  loadings; too wide and we match dark SAR hits to unrelated voyages.
  The ADR pins it at 3 and the CLI lets the operator override via
  `--time-window-days`. A later tuning phase can sweep this against
  labeled (vessel, voyage, lifting-record) tuples if we can get any.
- **Length filter drops small tankers**. MR/LR1 tankers (<200 m) are
  invisible to this pipeline even if they're dark-fleet. This is fine
  for TD3C (VLCC-only contract) but would break a future Suezmax or
  product-tanker signal — a caveat for any reuse of this module
  outside TD3C.
- **Snapshot tests are pinned to the hand-crafted fixture, not to real
  SAR data**. Fixture counts are deterministic by construction (see
  test docstring). The real data has no ground-truth label, so we do
  NOT pin any test against `data/raw/gfw/sar_vessels/*.csv` counts —
  the snapshot is fixture-only. A future phase that gets
  sanctions-list ground truth can add a real-data labeled test.

## Alternatives considered

- **Spire Maritime historical AIS feed.** Commercial; academic tier
  applied but not yet approved. Would close this gap more completely
  because Spire's satellite AIS sees dark vessels that terrestrial AIS
  cannot. Promoted to `.build/candidate_phases.md` as a separate
  candidate; SAR-based approach here is the free, available-now
  mitigation.
- **Use `matched_category` to filter (drop fishing/passenger/gear).**
  Rejected: SAR category labels are learned; dropping `noisy_vessel`
  or `other` rows might drop genuine dark VLCCs with misclassified
  SAR footprints. Length alone is the defensible first cut.
- **Use SAR `matching_score > threshold`** to fuse with AIS and reduce
  MMSI ambiguity. Considered and deferred: the matching score's
  semantics are documented (0–100 with >50 being strong match) but
  our current downstream logic treats matched and unmatched rows
  identically within the dark-fleet-candidate count. A future signal
  weighting pass can use the score.
- **SAR infrastructure dataset (fixed structures).** The separate
  `sar_fixed_infrastructure_*.csv` is structures, not vessels. Out
  of scope here.
- **Per-terminal buffer tuning**. Some terminals have larger or smaller
  holding areas. A future improvement could set per-terminal
  `buffer_km`; for v0 the single 10 km setting is a simple,
  defensible choice.
- **Hard-exclude NULL-MMSI rows**. Rejected — this would throw away
  exactly the signal we're trying to measure.
- **Store only dark candidates (drop `has_matching_voyage=True` rows
  before writing).** Rejected: the operator needs the matched-voyage
  rows for audit (confirming the pipeline is finding real matches)
  and for computing dark-rate = dark / total. The output is the full
  annotated set; the consumer filters.
