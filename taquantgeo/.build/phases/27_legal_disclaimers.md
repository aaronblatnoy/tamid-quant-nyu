# Phase 27 — Legal disclaimers

## Metadata
- Effort: `standard`
- Depends on phases: 09
- Applies security-review: `no`
- Max phase runtime (minutes): 60
- External services: none

## Mission
Before the v0 release artifacts go external (report shared with a
collaborator, demo shown to a potential partner, screenshot posted),
the standard trading-research disclaimers must be on the site and
linked from every material that includes backtest data. Data-source
attribution (GFW CC license, AISStream license, yfinance ToS) is
required for the free-tier usage. This is NOT legal advice — we flag
the disclaimer document loudly as a starting point and ask the user to
get a real lawyer to review before any external monetization or demo.

## Orientation
- `docs/adrs/0009-ic-fail-fast-gate.md`
- `reports/backtest_v1.md` — the artifact whose claims need disclaimers
- GFW, AISStream, yfinance ToS pages (read current; don't rely on
  memory)
- Existing repo LICENSE

## Service preflight
None.

## Acceptance criteria
- `docs/DISCLAIMER.md` covering:
  - Backtest-is-not-predictive boilerplate
  - Past-performance-does-not-guarantee-future-returns
  - Trading-risk disclosure (capital at risk, leverage implications
    when trading equities, concentration risk in a narrow basket)
  - Data-source attribution:
    - Global Fishing Watch: Creative Commons BY-NC-SA attribution text
      as published on their data portal, with a link
    - AISStream.io license summary + link
    - yfinance is a wrapper around Yahoo Finance public pages; cite
      yfinance ToS and note the upstream
  - A loud top-of-file banner: "**This is not legal advice.** A
    licensed attorney must review this document before any external
    demo, monetization, or capital-raising activity."
- `web/src/app/about/page.tsx` — about page with "Legal & Disclaimers"
  section linking to the markdown. Markdown is also rendered inline on
  the page (server-rendered from the committed file at build time).
- Footer link to the about page added to the `TopBar` or a new `Footer`
  component on every dashboard page
- Backtest viewer (phase 21) export-as-HTML includes the disclaimer
  text verbatim in the exported artifact
- Playwright test: `/about` renders; disclaimer banner visible;
  citations to the three data sources present
- Linter `docs/scripts/check_disclaimer.py` that fails if any of the
  four required sections (backtest / past-perf / trading-risk /
  attribution) is missing from `docs/DISCLAIMER.md`
- Linter wired to CI on push to `docs/DISCLAIMER.md`

## File plan
- `docs/DISCLAIMER.md`
- `web/src/app/about/page.tsx`
- `web/src/components/shell/Footer.tsx`
- `web/src/lib/markdown.ts` — server-side markdown → React helper if
  not already present (phase 17 may have shipped)
- `docs/scripts/check_disclaimer.py`
- `docs/scripts/test_check_disclaimer.py`
- `.github/workflows/ci.yml` — extend with the disclaimer lint job
- Update phase 21's `ExportButton.tsx` to inline the disclaimer at the
  bottom of every exported HTML

## Non-goals
- Having a lawyer review — the phase explicitly flags this as a
  follow-up for the user outside the harness
- GDPR / CCPA privacy policy — candidate (we don't collect PII yet;
  GitHub OAuth login data is the only user data; a privacy policy
  should be added before any public user sign-ups)
- Terms of Service for end users — candidate

## Quality gates
- Lint + format + typecheck clean
- Disclaimer linter passes
- Playwright about-page test green
- Pre-commit meta-review full loop
- Exported HTML from phase 21 now contains the disclaimer
  (verify via test)

## Git workflow
1. Branch `docs/phase-27-legal-disclaimers`
2. Commits:
   - `docs: legal disclaimers with data-source attribution`
   - `feat(web): /about page + footer link`
   - `feat(web): disclaimer in backtest export`
   - `tools: disclaimer linter + CI wiring`
   - `test(web): about page renders + citations`
3. PR, CI green, squash-merge

## Handoff
Confirm footer link visible on every dashboard route. Copy the
"not-legal-advice" banner verbatim into handoff for record.
