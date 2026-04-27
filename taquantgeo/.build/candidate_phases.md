# Candidate phases

Queue of phase-worthy work that emerged mid-run but was out of scope for
the phase that noticed it. Each entry is a short contract stub; phase 90
triages entries into PROMOTE (becomes a real phases/9N_slug.md and the
harness runs it), BACKLOG (stays here with a `triaged-YYYY-MM-DD` tag),
or ARCHIVE (moved to `archived_candidates.md` with rationale).

The harness itself does NOT implement candidates mid-phase. If an agent
spots a new idea, it appends a stub here and moves on.

Format follows `.build/templates/candidate_phase_template.md`.

---

## Candidate — Baltic Exchange TD3C FFA / spot price ingest

### Proposed by
Seed — pre-harness. Motivation from ADR 0002 Gap 4.

### Problem
Backtest + IC (phases 07/09) currently proxy freight rates via a
shipping-equity basket because Baltic Exchange data is paywalled
(~$5k/year retail). Equity proxies correlate ~70-80% with TD3C at
weekly horizon — defensible for v0 but loses signal fidelity. If
Baltic academic access is granted (applied), we should replace the
equity proxy with the actual TD3C Worldscale series for both IC and
backtest attribution.

### Proposed scope (sketch)
- Ingest TD3C spot + FFA settlements from Baltic daily feed
- Add `ffa_prices` and `spot_rates` tables + alembic migrations
- Re-run phase 07 IC analysis with both series side-by-side
  (equity proxy and real TD3C)
- Update ADR 0002 Gap 4 and ADR 0009 with the improved series
- Backtester config gains `target_series: "equities" | "td3c_spot" |
  "td3c_ffa"`

### Evidence this matters
- ADR 0002 Gap 4 explicitly calls this out as a real gap
- IC with a 70-80% correlated proxy is strictly weaker than IC against
  the real series
- The v0 evidence bundle is more defensible with the real series

### Rough acceptance criteria
- Series ingested daily via scheduled job
- IC analysis shows both series' IC side-by-side in the report
- Backtester runs against either without code change

### Dependencies / blockers
- Baltic academic access (pending; timing external)
- Or paid subscription (~$5k/yr) — user decision

### Estimated effort
`max`.

### Disposition
`open`.

---

## Candidate — Spire Maritime historical AIS (academic tier)

### Proposed by
Seed — pre-harness. Motivation from ADR 0002 Gap 2 + alternatives
considered.

### Problem
GFW's C4 voyages systematically exclude dark-fleet VLCCs (AIS spoofed
or off). Phase 03 ships a SAR-based proxy but SAR coverage is patchy
mid-ocean. Spire Maritime publishes tanker-focused AIS covering most
VLCCs including dark-fleet candidates via terrestrial + satellite
feeds. Academic-tier access (applied) would materially close ADR 0002
Gap 2.

### Proposed scope (sketch)
- New ingestion adapter for Spire's historical API
- Cross-reference Spire positions against our GFW voyages to quantify
  under-coverage
- Bolt into phase 04's dark-fleet adjustment as a higher-fidelity
  alternative to the SAR proxy
- Document coverage delta in a new ADR

### Evidence this matters
- ADR 0002 Gap 2 explicitly noted
- Phase 03's SAR proxy is a partial mitigation; Spire would be
  higher-signal

### Rough acceptance criteria
- Daily Spire pull backfilled to 2020
- Vessel cross-reference yields <5% unmatched VLCC-class vessels on
  a sample month
- Phase 04 dark-fleet adjustment configurable to use Spire over SAR

### Dependencies / blockers
- Spire academic application (pending)

### Estimated effort
`max`.

### Disposition
`open`.

---

## Candidate — Compliance gating for real-money deployment at scale

### Proposed by
Seed — pre-harness. Motivation from the user instruction that v0 is
personal-capital only but future scale would require compliance review.

### Problem
If TaQuantGeo grows beyond a single-operator personal-capital book,
financial regulations apply (MiFID II in EU, CFTC registration for
futures-like instruments in US, FINRA considerations for equity
trading scale). We ship with disclaimers but no compliance gating.

### Proposed scope (sketch)
- Pre-deployment compliance checklist integrated into
  `docs/runbook.md`
- Audit-log retention SLA (7+ years) documented
- Trade-reporting hooks for Form 13F / similar (stubbed; activated
  on threshold)
- Counsel engagement checkpoint documented in the runbook
- ADR capturing regulatory framework for the target jurisdiction

### Evidence this matters
- Phase 27 disclaimer explicitly flags "not legal advice — get a
  lawyer". This candidate is the technical pre-work for that lawyer
  conversation.

### Rough acceptance criteria
- Checklist + runbook section exists
- Audit-log retention SLA documented and verified by backup strategy
  (phase 25)
- Trade-reporting hook exists behind a disabled feature flag

### Dependencies / blockers
- User decision on target jurisdiction + capital scale
- Legal counsel engagement (external)

### Estimated effort
`standard` for the technical scaffold; the legal work itself is
external-to-harness

### Disposition
`open`.

---

## Candidate — Shared parquet atomic-write helper

### Proposed by
Phase 03 handoff, 2026-04-21, during `feat/phase-03-sar-dark-fleet`
work. Flagged by the style-review subagent in round 2.

### Problem
`packages/ais/src/taquantgeo_ais/gfw/sar.py` and
`packages/ais/src/taquantgeo_ais/gfw/distance.py` both define an
`_atomic_write_parquet` helper with the same tmp-then-`os.replace`
pattern, same error cleanup, and near-identical docstring. Two
test files (test_sar.py, test_distance.py) also duplicate the
crash-path and clean-tmp-on-success invariants. Any future bugfix
(e.g. fsync for durability, Windows rename fallback, telemetry on
rename failures) has to land in both places or silently drift.

### Proposed scope (sketch)
- Extract the helper to `packages/ais/src/taquantgeo_ais/io.py`
  (or `packages/core/src/taquantgeo_core/io.py` if it's used more
  broadly). Pick the location that matches the eventual
  signals/backtest reuse pattern.
- Replace both call sites in `sar.py` and `distance.py`.
- Collapse the two test bodies into a single shared parametrised
  test that both sar and distance reference via their own smoke
  invocation.
- Add an ADR note (extension of 0005 or a short new one) capturing
  the contract: atomic via same-fs tmp + `os.replace`, tmp cleanup
  on BaseException, zstd-compressed output, parent dir autocreate.

### Evidence this matters
- Phase 03 style-review explicitly flagged this as the largest
  remaining style issue after round 1 fixes.
- Every future consumer of parquet outputs (phase 04 signals,
  phase 08 backtester, phase 16 audit log) will want the same
  crash-safe write primitive.

### Rough acceptance criteria
- Shared helper exists and is imported by sar.py + distance.py.
- Both test files' atomic-write tests collapse (or at minimum,
  both call the same underlying invariant helper).
- No behaviour change on the output parquet files.
- Docs updated with the helper's contract.

### Dependencies / blockers
- None. Mechanical refactor.

### Estimated effort
`standard`.

### Disposition
`open`.

---

## Candidate — Historical signal batch compute (Phase 07 unblock)

### Proposed by
Phase 07 handoff (2026-04-21), during `feat/phase-07-ic-analysis`.

### Problem
Phase 07's IC fail-fast gate returned `BLOCKED_INSUFFICIENT_DATA` against
real data — only 1 persisted signal exists today and it is
supply-floor-clamped, so 0 usable signals reach the gate. The unblock
requires running `taq signals compute-tightness --as-of <date> --persist`
over a multi-month range, but no batch-mode CLI exists today;
one-day-per-invocation has unacceptable per-call DB-roundtrip overhead and complicates
the `prior_snapshots_df` load.

### Proposed scope (sketch)
New `taq signals backfill-tightness --since YYYY-MM-DD --until YYYY-MM-DD
--route td3c [--persist]` CLI that iterates `compute_daily_tightness` over
the date range with shared in-memory `prior_snapshots` accumulator (one DB
read at start, then in-memory growth). Honours the same path defaults as
`compute-tightness`.

### Evidence this matters
- Phase 07 verdict is BLOCKED today; the harness halts here.
- Direct unblock for Phase 08 onward.

### Rough acceptance criteria
- Backfilling a 6-month range produces ≥120 signal rows in Postgres and a
  re-run of `taq signals ic` resolves to PASS or FAIL_NO_EDGE (no longer
  BLOCKED).
- Tests pin (a) the date-range iteration, (b) the in-memory
  `prior_snapshots` accumulation, (c) idempotency on re-run.

### Dependencies / blockers
- None new — uses Phase 04's `compute_daily_tightness` and Phase 04's
  signals table.
- Indirectly depends on more historical voyages being on disk (see
  multi-month voyages candidate below).

### Estimated effort
`standard`. ~1 day of work.

### Disposition
`open`.

---

## Candidate — Multi-month historical voyages backfill

### Proposed by
Phase 07 handoff (2026-04-21).

### Problem
Only thin early-2026 voyages are on disk (`data/raw/gfw/voyages/voyages_c4_pipe_v3_202602.csv`
and a couple of partial files). Even a full historical-signal batch compute
can produce at most ~30 daily snapshots — well below Phase 07's
`min_history_days=180` requirement.

### Proposed scope (sketch)
- Operator-driven download of GFW C4 voyages CSVs for at least 24 prior
  months, plus running `taq gfw ingest-voyages` over each.
- Optional automation: a small `taq gfw bulk-ingest --years 2 --route td3c`
  wrapper that knows the GFW URL convention.

### Evidence this matters
- Same as historical signal batch compute — Phase 07 verdict cannot fire
  without it.
- Backtester (Phase 08), walk-forward CV (Phase 09), and any future
  production trading rest on having ≥1 year of historical signals.

### Rough acceptance criteria
- ≥24 months of `voyages_c4_pipe_v3_<YYYYMM>.csv` ingested into
  `data/processed/voyages/route=td3c/year=YYYY/month=MM/`.
- Validated via a smoke test that loads each month and counts rows.

### Dependencies / blockers
- Operator access to GFW C4 download portal (free academic tier). No code
  dependency.

### Estimated effort
`standard`. The CLI wrapper is ~½ day; the actual CSV downloads are
operator time + bandwidth.

### Disposition
`open`.

---

## Candidate — Per-name IC attribution + bootstrap CIs

### Proposed by
Phase 07 handoff (2026-04-21). Combines two related diagnostics flagged
during the IC review.

### Problem
Phase 07 reports basket-level IC only. Per-name attribution would identify
which equity carries the TD3C-correlated signal vs which is noise — useful
for portfolio-construction (Phase 08+) and for spotting when one name
(e.g. TNK) drifts from the basket. Separately, the t-stat approximation in
`evaluate_verdict` gives a point estimate; bootstrap CIs would let the gate
make probabilistic decisions on borderline cases.

### Proposed scope (sketch)
- Add per-ticker IC computation to `walk_forward_ic` (returns extra rows
  tagged by ticker).
- Bootstrap K=1000 CI on mean IC per (horizon, method, ticker?) cell.
- Surface in the report appendix and (later) the Phase 19 dashboard.

### Evidence this matters
- ADR 0009 explicitly defers bootstrap CIs as out-of-scope for v0 but
  flags them as a follow-up.
- Per-name attribution is a Phase 08 portfolio-construction need.

### Rough acceptance criteria
- Report contains a per-ticker IC table.
- New tests pin per-ticker IC matches manual computation against a 2-ticker
  fixture.
- Bootstrap CI half-width < 0.03 on a 4-year synthetic fixture.

### Dependencies / blockers
- Phase 07 unblocked first (needs real signal history).
- Possibly an ADR 0009 amendment if the bootstrap changes the gate
  semantics.

### Estimated effort
`max`. Touches the gate's statistical core; ~3-4 days with proper review.

### Disposition
`open`.

---
