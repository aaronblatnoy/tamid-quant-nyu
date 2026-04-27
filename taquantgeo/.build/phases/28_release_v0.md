# Phase 28 — Release v0.1.0

## Metadata
- Effort: `standard`
- Depends on phases: all prior (00-27)
- Applies security-review: `no`
- Max phase runtime (minutes): 60
- External services: none

## Mission
The coordinated release commit. Tag `v0.1.0`. `release-please` (or
equivalent) generates `CHANGELOG.md` from conventional commits. README's
Phase table flips all-green and links to the evidence bundle
(`reports/ic_analysis.md`, `reports/backtest_v1.md`) and the
architectural summary. GitHub release is created with the tag. This is a
ceremony phase — nothing is built; everything is about making the
already-built work legible to a future reader (or collaborator, or
investor) who opens the repo for the first time.

## Orientation
- All prior handoffs — summary notes from 04 / 07 / 09 / 24 are
  particularly useful for the release notes
- README.md current state — the Phase table needs each row updated
- `reports/ic_analysis.md` and `reports/backtest_v1.md` if generated
- All ADRs

## Service preflight
None.

## Acceptance criteria
- `README.md`:
  - Phase table: every row ✅ (or the honest state if a phase was
    halted)
  - New "Evidence bundle" section with links to the generated reports
    (or a path where to find them since reports/ is gitignored; e.g.,
    reproducible commands to regenerate: `taq signals ic ...`,
    `taq backtest wfcv ...`)
  - "Architecture overview" section linking to key ADRs (stack,
    signal, backtester, deploy, audit, broker)
  - "Quickstart" kept up-to-date with anything that changed
  - Badges: CI, license, release (v0.1.0)
- `CHANGELOG.md` generated via release-please from conventional commits
  (or manually curated if release-please integration is too heavy)
- Tag `v0.1.0` created and pushed
- GitHub release with:
  - Release notes body summarizing what v0 includes (pipeline,
    backtester with WFCV, paper-ready trading rails, dashboard, ops
    runbook, disclaimers)
  - Highlights section: "What you can do with v0" (run a backtest,
    view the globe, review the evidence bundle)
  - Known limitations: direct links to ADR 0002 Gap 4 (TD3C FFA
    paywalled), ADR 0009 (IC gate history), ADR 0014 (IBKR flip still
    requires operator toggle)
  - Zero hype; factual
- Deploy workflow triggered by the tag (phase 24's workflow already
  listens on `v*`). Wait for deploy to complete green.
- Final release-check script: `.build/scripts/release_checklist.py` (new; committed) verifies every prerequisite for v0.1.0 by running these checks in order — each returns True/False and the script exits non-zero if any fail:
  1. README Phase table is fully ✅ (or honestly reflects halts)
  2. CHANGELOG.md exists at the repo root and contains a v0.1.0 section
  3. Tag `v0.1.0` exists locally AND on origin
  4. Every phase handoff under `.build/handoffs/*_handoff.md` parses with `status: completed` (or is a documented exception)
  5. The tag-triggered deploy workflow completed green
  6. `reports/ic_analysis.md` and `reports/backtest_v1.md` exist at their documented paths (or regeneration commands are in the evidence-bundle doc)
- Tests for the checklist script itself live under `tests/unit/test_release_checklist.py`:
  - `test_release_checklist_fails_when_readme_phase_table_incomplete` — synthesized README with a phase still marked ⏳
  - `test_release_checklist_fails_when_changelog_missing` — remove CHANGELOG.md in tmp; script fails
  - `test_release_checklist_fails_when_tag_missing` — local repo without v0.1.0 tag; script fails
  - `test_release_checklist_fails_when_handoff_status_not_completed` — synthesized handoff with status=blocked; script fails
  - `test_release_checklist_fails_when_deploy_workflow_red` — stubbed `gh run list` output showing failed run; script fails
  - `test_release_checklist_passes_when_all_checks_satisfied` — all prerequisites in place; script exits 0

## File plan
- `README.md` — updates
- `CHANGELOG.md` — generated or curated
- `.build/scripts/release_checklist.py` — new
- `tests/unit/test_release_checklist.py` — unit tests for the release_checklist.py script (enumerated in Acceptance criteria).
- `.github/release-please-config.json` (if release-please path chosen)
- `.github/release-please-manifest.json`
- `.github/workflows/release-please.yml` — optional
- `docs/EVIDENCE_BUNDLE.md` — curated index of the evidence artifacts
  (ic_analysis.md, backtest_v1.md, any PRs) even though the files
  themselves are gitignored; this doc explains how to regenerate them

## Non-goals
- Semantic versioning automation beyond v0.1.0 — release-please can
  take it from here
- Tagging any downstream / minor releases — those come from future work
- Public announcement (Twitter, HN, etc.) — user-side activity
- Monetization gates / licenses change — repo stays MIT per existing
  LICENSE

## Quality gates
- Format + lint + typecheck clean
- Release-check script passes
- Deploy workflow triggered by tag succeeds (wait and verify)
- Pre-commit meta-review full loop (docs-heavy, release-sensitive)

## Git workflow
1. Branch `release/v0.1.0`
2. Commits:
   - `docs: flip README Phase table; evidence bundle + architecture
     summary`
   - `docs: CHANGELOG v0.1.0`
   - `tools: .build/release_checklist.py`
3. PR, CI green, squash-merge
4. `git tag v0.1.0 -m "v0.1.0"`, `git push origin v0.1.0`
5. Wait for tag-triggered deploy workflow to succeed
6. `gh release create v0.1.0 --title "v0.1.0" --notes-file
   release_notes_v0.1.0.md` (notes body described in acceptance)

## Handoff
Tag SHA, GitHub release URL, deploy workflow run URL. Release checklist
output. Any phase that was NOT marked `completed` explicitly called out
with its state.
