# Build harness changelog

## 2026-04-21 — v1 harness scaffold

- 33 phase contracts under `.build/phases/` covering the remaining v0
  work: vessel classifier → deploy → final audit, plus sub-phases
  23b (design system) and 23c (local e2e), plus 90 (candidate triage)
  and 99 (final holistic audit).
- `run.py` driver (stdlib only): numeric-ordered phase discovery,
  state management, blocked-phase re-attempt on startup, drift check
  against commit baseline, dynamic 91-98 discovery after phase 90,
  SIGINT-safe state writes, --dry-run / --from-phase / --only / --skip
  flags.
- Templates: phase_template.md, handoff_template.md,
  candidate_phase_template.md.
- Seeded candidate_phases.md with three initial candidates (Baltic FFA
  ingest, Spire Maritime historical AIS, compliance gating for
  scaled real-money deployment).
- Test count baseline: 53 at commit `ccdefeb` (recorded on first run).
