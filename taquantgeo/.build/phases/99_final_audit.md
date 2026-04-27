# Phase 99 — Final holistic audit

## Metadata
- Effort: `max`
- Depends on phases: 28 (plus any promoted 91-98)
- Applies security-review: `yes`
- Max phase runtime (minutes): 240
- External services: none

## Mission
The last pass. A fresh agent reads the entire repo, every ADR, every
handoff, every PR description, and produces `docs/FINAL_AUDIT.md`:
architectural coherence, test coverage gaps, documentation gaps,
security surface summary, dependency health, operational readiness
score. For every gap flagged, a GitHub issue is filed with enough
context that a future worker — human or harness — can pick it up.
Codex will audit this project; the final audit is the document the user
hands to Codex first.

## Orientation
- Every phase handoff under `.build/handoffs/`
- Every ADR under `docs/adrs/`
- Every PR merged on main: `gh pr list --state merged --limit 100`
- `.build/triage_report.md` from phase 90
- `docs/runbook.md`
- README.md
- `reports/ic_analysis.md`, `reports/backtest_v1.md` (evidence bundle)
- Current test count, current test skip-reasons
- `uv lock` output for dependency health

## Service preflight
None.

## Acceptance criteria
- `docs/FINAL_AUDIT.md` with sections:
  1. **Executive summary** — one paragraph on overall state of the
     system, v0 readiness, top-3 risks
  2. **Architectural coherence** — does the code match the ADRs? List
     every ADR and spot-check one implementation file per ADR. Flag
     drift.
  3. **Test coverage gaps** — per-package coverage table (uv run
     pytest with `coverage.py`). Packages below 80% flagged. Missing
     test markers flagged. `@pytest.mark.live` tests not recently
     executed flagged.
  4. **Documentation gaps** — every public surface must have a doc
     reference. Runbook linter output from phase 26 referenced.
     Disclaimer linter from phase 27. CLAUDE.md completeness.
  5. **Security surface summary** — every file touched by a security-
     review subagent listed with its ADR + pass notes. RiskGate,
     IbkrBroker, Reconciliation, AuditLog, kill-switch engagement,
     step-up re-auth, deploy SSH path, secret scrubber
  6. **Dependency health** — `uv pip list --outdated` output (or
     equivalent). Known CVEs via `pip-audit` or `safety` (document
     tool choice). Pinned versions vs unpinned.
  7. **Operational readiness score** — rubric per component (pipeline,
     backtest, trading rails, deploy, backup, alerts) with 0-5 scores
     and justification. Weighted average reported. Score < 4 flagged
     as a gap.
  8. **Gaps filed as issues** — list of GitHub issue URLs, one per
     gap, with a short title and link back into FINAL_AUDIT.md
     subsection
  9. **Known limitations carried into v0.1** — cited back to ADRs,
     not re-litigated here
  10. **Recommended follow-ups** — ordered by expected impact; this is
      input to a future `candidate_phases.md` seed for harness v2
- Every flagged gap MUST correspond to a filed GH issue via
  `gh issue create`. Tests: the phase's own script verifies every
  anchor in section 8 resolves to a real issue URL.
- Phase does NOT fix gaps. Audit only.

## File plan
- `docs/FINAL_AUDIT.md` — new
- `.build/scripts/run_final_audit.py` — helper that composes coverage
  + outdated deps + security-summary automatically so the agent is
  doing analysis, not rote data collection
- `.build/scripts/verify_issue_links.py` — verifies every issue link
  in FINAL_AUDIT.md returns 200 via `gh issue view`
- `.github/ISSUE_TEMPLATE/final_audit_followup.md` — template for gap-
  followup issues so they're consistent

## Non-goals
- Fixing the gaps found — explicit non-goal. Audit → issues → future
  phases / harness runs / manual work
- Evaluating trade decisions / live P&L — this is code/process audit,
  not performance review
- Redesigning any ADR — audit flags drift; ADRs are amended by the
  work that deals with the drift

## Quality gates
- Format + lint + typecheck clean
- `verify_issue_links.py` passes (every linked issue actually exists)
- Pre-commit meta-review + **security-review subagent mandatory**
  (security section is the sharpest edge)
- Three-round review loop per `Effort: max`
- If the audit flags any critical gap in production-path code,
  phase completes with status `partially_completed` and a top-level
  warning — driver treats this as blocked so user reviews before
  the audit is considered closed

## Git workflow
1. Branch `docs/phase-99-final-audit`
2. Commits:
   - `tools: final-audit helper scripts + issue-link verifier`
   - `docs: FINAL_AUDIT.md v0.1 complete`
   - `chore: file follow-up issues for flagged gaps`
3. PR, CI green, squash-merge

## Handoff
Readiness score. Number of issues filed. Top-3 risks summary reproduced
verbatim. If the audit flagged any show-stopper (e.g., the kill-switch
race test doesn't actually run because a fixture is missing), write
status=`partially_completed` so the user reviews before closing v0.
