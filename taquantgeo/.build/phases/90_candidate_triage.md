# Phase 90 — Candidate triage

## Metadata
- Effort: `max`
- Depends on phases: 28
- Applies security-review: `no`
- Max phase runtime (minutes): 180
- External services: none

## Mission
Between phase 00 and phase 28 the agents will have accumulated
candidate-phase entries in `.build/candidate_phases.md` — work they
noticed was phase-worthy but out of scope. Rather than leaving them
forever, this phase audits each candidate and makes one of three
decisions: PROMOTE (create a real numbered phase file at 91-98 that the
driver will then execute autonomously), BACKLOG (leave in the file with
a triage timestamp so next run doesn't re-review), or ARCHIVE (move to
`archived_candidates.md` with a one-line rationale). Writes
`triage_report.md` as the audit trail.

## Orientation
- `.build/candidate_phases.md` — the queue
- Every phase file in `.build/phases/` — to understand what IS already
  scoped
- Every handoff `.build/handoffs/*.md` — to understand what was actually
  shipped (handoffs are gitignored; this phase agent reads them from
  the repo's working copy)
- All ADRs — to identify non-applicable candidates (e.g., anything that
  contradicts a shipped ADR should be archived, not promoted)
- README.md — project thesis

## Service preflight
None.

## Acceptance criteria
- For EACH entry in `candidate_phases.md` (sections separated by `---`
  or `## Candidate NN`), apply decision logic:
  - **PROMOTE** criteria (all must hold):
    - Meaningfully improves v0 quality / coverage / operational
      readiness (concrete, not aesthetic)
    - Implementable with currently available data / services at time
      of triage (no wait-on-external-grant dependencies)
    - Aligns with existing ADRs (or the candidate itself includes an
      ADR-amendment motivation)
    - Clear acceptance criteria can be formulated in the promoted phase
      file
    - Default bias: CONSERVATIVE. Borderline → BACKLOG. "Nice to have"
      → BACKLOG.
  - **ARCHIVE** criteria (any one suffices):
    - Contradicts a shipped ADR without a reasoned amendment
    - Already covered by a shipped or scheduled phase (pointer to
      which)
    - Superseded by a later candidate that captures the same idea
      better
    - Not applicable to v0's thesis
  - **BACKLOG** default for everything else — tag with
    `triaged-YYYY-MM-DD` so future triage ignores.
- Promoted phases become real phase files:
  - Filename: `phases/9N_<slug>.md` where N is 1-8 (reserved range
    91-98; 90 is this phase, 99 is the final audit)
  - Must conform to `.build/templates/phase_template.md`
  - Include all required sections: Metadata, Mission, Orientation,
    Service preflight, Acceptance criteria, File plan, Non-goals,
    Quality gates, Git workflow, Handoff
  - Max 8 promotions per run (the 91-98 slots)
  - If more than 8 would pass PROMOTE criteria, pick the top 8 by
    their impact-on-v0; move the rest to BACKLOG with a note
- Archived candidates moved (not copied) to `archived_candidates.md`:
  - Keep the candidate's original body
  - Append `### Archived YYYY-MM-DD`
  - Append `**Rationale:** <one line citing ADR / handoff / decision>`
- `triage_report.md` (committed) contains:
  - Total candidates reviewed
  - PROMOTE list: slug → new phase number
  - BACKLOG list: slug → reason still open
  - ARCHIVE list: slug → archival rationale
  - Summary sentence: "Harness will next run promoted phases
    91-<last> before executing phase 99 final audit."

## File plan
- For each PROMOTE: `.build/phases/9N_<slug>.md` — new phase file
- `.build/candidate_phases.md` — rewritten with backlog-only entries
  + triage timestamps
- `.build/archived_candidates.md` — new entries appended
- `.build/triage_report.md` — new (committed)

## Non-goals
- Implementing promoted candidates. Promotion only writes the phase
  file; the driver re-scans after this phase and executes the new
  phases autonomously
- Recursion: promoted 91-98 phases CANNOT trigger another triage round.
  They can append to `candidate_phases.md` for a future harness run.
- Renumbering prior phases — IDs are stable
- Editing existing phase files beyond what's necessary to note that a
  candidate was promoted (e.g., "see phase 91 for the XYZ improvement")

## Quality gates
- Format + lint + typecheck clean (phase only edits docs + markdown;
  these should pass trivially but the gate applies uniformly)
- Each promoted phase file passes the same linter that phase 00
  applied (every required section present)
- `triage_report.md` row totals match (`reviewed = promoted + backlog +
  archived`)
- Pre-commit meta-review full loop (multi-file, policy-adjacent)
- Three-round review loop per `Effort: max` — the promotion decision is
  the core judgment; review should challenge any borderline PROMOTE

## Git workflow
1. Branch `chore/phase-90-candidate-triage`
2. Commits:
   - `chore(build): triage candidate_phases.md`
   - `chore(build): promote <N> candidates to phases/9*`
   - `chore(build): archive <N> candidates with rationale`
   - `docs(build): triage_report.md`
3. PR, CI green, squash-merge

## Handoff
The triage_report verbatim. List of promoted phase filenames so the
driver logs can correlate.
