# Signal snapshot fixtures

This directory holds a tiny, deterministic fixture set for the end-to-end
snapshot tests in `packages/signals/tests/test_tightness_snapshot.py`.
Every row is hand-picked to drive a specific `as_of` date to a specific
pinned output — a later PR that silently changes the signal math forces
at least one assertion in that test module to fail.

## Files

| File | Rows | Shape notes |
|---|---|---|
| `voyages.parquet` | 12 | 10 laden `td3c` + 2 `td3c_ballast`, phase-01 schema |
| `vessel_registry.parquet` | 12 | 11 VLCC candidates + 1 non-VLCC; no `dwt` column |
| `distance_cache.parquet` | 4 | The four PG↔China anchorage pairs used by the voyages |
| `dark_fleet.parquet` | 3 | Three unmatched SAR detections at PG loading terminals |

These files are committed directly. To regenerate them deterministically:

```bash
uv run python packages/signals/tests/fixtures/snapshot/regenerate.py
```

That script is the source of truth. Do NOT hand-edit the parquet outputs
— if a row needs to change, edit `regenerate.py`, re-run, re-run the
snapshot tests, and update the pinned expected values.

## Fixture layout

### Voyages

Ten laden `td3c` voyages (MMSIs 101-109 + 999) and two `td3c_ballast`
voyages (MMSIs 201, 202). Anchorage ids are short placeholders:
`s2_rt` (Ras Tanura), `s2_br` (Al-Basrah Oil Terminal), `s2_ng` (Ningbo),
`s2_qd` (Qingdao).

Laden in-progress windows (by MMSI):

| MMSI | trip_start | trip_end | origin→dest | In-progress on… |
|---|---|---|---|---|
| 101 | 2020-02-25 | 2020-03-20 | RT→NB | 3/01, 3/05, 3/15, 3/18 (not 3/20, 3/22) |
| 102 | 2020-03-02 | 2020-03-28 | BR→QD | 3/05, 3/15, 3/18, 3/20, 3/22 |
| 103 | 2020-03-05 | null       | RT→NB | 3/05, 3/15, 3/18, 3/20, 3/22 |
| 104 | 2020-03-08 | 2020-04-02 | BR→QD | 3/15, 3/18, 3/20, 3/22 |
| 105 | 2020-03-10 | null       | RT→NB | 3/15, 3/18, 3/20, 3/22 |
| 106 | 2020-03-12 | null       | BR→QD | 3/15, 3/18, 3/20, 3/22 |
| 107 | 2020-03-14 | 2020-04-05 | RT→NB | 3/15, 3/18, 3/20, 3/22 |
| 108 | 2020-02-01 | 2020-02-25 | RT→NB | (never — ends before window) |
| 109 | 2020-04-01 | null       | BR→QD | (never — starts after window) |
| 999 | 2020-03-10 | null       | RT→NB | in-progress but NON-VLCC — registry filter drops it |

Ballast in-progress + ETA (13-kn nominal, sea-route cache distance):

| MMSI | trip_start | trip_end | origin→dest | ETA | Counts in 15-day window on… |
|---|---|---|---|---|---|
| 201 | 2020-03-05 | 2020-03-24 | NB→RT (5920 NM) | 2020-03-23 23:23 UTC | 3/15, 3/18, 3/20, 3/22 |
| 202 | 2020-03-08 | 2020-03-28 | QD→BR (6416 NM) | 2020-03-28 13:32 UTC | 3/15, 3/18, 3/20, 3/22 |

Ballast 201 is in-progress on 2020-03-05 but its ETA (2020-03-23) is
outside that day's 15-day horizon cutoff (2020-03-20) so it does NOT
count toward supply on 2020-03-05.

### Vessel registry

12 rows matching every voyage MMSI. All laden + ballast MMSIs are
`is_vlcc_candidate=True`; MMSI 999 is `False`. No `dwt` column: every
voyage hits the 270,000 DWT route nominal fallback.

### Distance cache

Four anchorage pairs — exactly the pairs used by the voyages. All
voyages hit the cache, so great-circle fallbacks = 0 on every test
date. Values (NM):

| pair | NM |
|---|---|
| Ras Tanura → Ningbo | 5920 |
| Basrah → Qingdao    | 6416 |
| Ningbo → Ras Tanura | 5920 |
| Qingdao → Basrah    | 6416 |

### Dark-fleet detections

Three unmatched detections. The 7-day window is inclusive on both ends
at end-of-day UTC (per ADR 0007); detections are placed so per-date
counts vary cleanly:

| Detection | Timestamp (UTC) | In windows of |
|---|---|---|
| D1 | 2020-03-17 10:00 | 3/18, 3/20, 3/22 |
| D2 | 2020-03-19 12:00 | 3/20, 3/22 |
| D3 | 2020-03-21 12:00 | 3/22 |

Per-date dark adjustment: 0 on 3/01, 3/05, 3/15 · 1 on 3/18 · 2 on 3/20
· 3 on 3/22.

## Expected snapshot values (pinned in tests)

| as_of | demand (ton-miles) | supply | dark | raw eff | eff | ratio | clamped |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2020-03-01 | 1,598,400,000 | 0 | 0 | 0 | 1 | 1,598,400,000 | 1 |
| 2020-03-05 | 4,929,120,000 | 0 | 0 | 0 | 1 | 4,929,120,000 | 1 |
| 2020-03-15 | 11,590,560,000 | 2 | 0 | 2 | 2 | 5,795,280,000 | 0 |
| 2020-03-18 | 11,590,560,000 | 2 | 1 | 1 | 1 | 11,590,560,000 | 0 |
| 2020-03-20 | 9,992,160,000 | 2 | 2 | 0 | 1 | 9,992,160,000 | 1 |
| 2020-03-22 | 9,992,160,000 | 2 | 3 | -1 | 1 | 9,992,160,000 | 1 |

Reminder: demand = 270,000 × Σ distance_NM (over in-progress laden).
Effective supply = max(supply − dark, 1). Ratio = demand / effective.

## Regenerating after an intentional math change

1. Change the signal math in `packages/signals/src/taquantgeo_signals/`.
2. Write a new ADR (`docs/adrs/NNNN-…md`) documenting the change.
3. Re-run `regenerate.py` (nothing to edit — the fixtures are stable;
   only the expected values may shift).
4. Re-run `uv run pytest packages/signals/tests/test_tightness_snapshot.py`;
   read the diff between old and new numbers in the failure output.
5. Update the pinned expected values in `test_tightness_snapshot.py`
   and the table above to match. The PR description must explain why.
