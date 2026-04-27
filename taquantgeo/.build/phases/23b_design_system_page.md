# Phase 23b — Design system page

## Metadata
- Effort: `standard`
- Depends on phases: 17
- Applies security-review: `no`
- Max phase runtime (minutes): 90
- External services: none

## Mission
`/internal/design` — an admin-gated reference of every UI primitive and
custom component the app ships with. Exists for two reasons: (1) to
prevent component drift as new pages add custom variants, future phases
should reuse these primitives before inventing new ones; (2) to give the
user a visual regression baseline — screenshot tests snapshot the whole
page so a CSS regression in a shared component is caught the moment it
lands.

## Orientation
- `.build/handoffs/17_handoff.md` through `23_handoff.md`
- `web/src/components/ui/*` — primitives shipped through phases 17-23
- Auth gate established in phase 17 (admin-only routes)

## Service preflight
None.

## Acceptance criteria
- `/internal/design` route renders one section per primitive, then one
  section per custom component:
  - Each section: component name (h2), usage sentence, the component
    rendered at common variants (size, variant, state), copy-paste
    code snippet (syntax-highlighted), and a "copy" button that lands
    the JSX on clipboard
  - Both themes (light/dark) rendered side-by-side per component
- Admin-gated: non-admin session → 403 page. Public nav does NOT list
  this route.
- Playwright visual-regression tests:
  - Take screenshots of the page in both themes (full-page + per-
    component sections) and compare against baseline PNGs committed
    under `web/tests/visual/baselines/`
  - First run creates baselines and marks test as "baseline created —
    review in PR"; subsequent runs diff
  - Tolerance: pixel-diff ≤ 0.1% (strict; CSS changes show up fast)
- Tests cover: every shipped component is represented on this page —
  registry enforces it. Adding a new `web/src/components/ui/*.tsx`
  without adding it here fails a static check.

## File plan
- `web/src/app/internal/design/page.tsx`
- `web/src/app/internal/design/ComponentsRegistry.ts` — declarative
  registry of every primitive + custom component; static check asserts
  every file under `web/src/components/ui/` and `web/src/components/shell/`
  has an entry
- `web/src/app/internal/design/ComponentSection.tsx`
- `web/src/app/internal/design/CodeSnippet.tsx`
- `web/tests/visual/design.spec.ts`
- `web/tests/visual/baselines/*.png` — committed baselines (generated
  on first run)
- `web/scripts/check-design-registry.ts` — CI script that fails if a
  new component file lacks a registry entry
- `.github/workflows/web-ci.yml` — wire the static check
- `CLAUDE.md` — note the registry invariant

## Non-goals
- Storybook integration (design system page is enough for v0).
  Candidate if component count grows.
- Design-token editor — candidate

## Quality gates
- Format + lint + typecheck clean
- Visual regression tests green (baselines generated on first run; PR
  reviewer confirms they look right)
- Registry static check passes
- Lighthouse Perf ≥ 90

## Git workflow
1. Branch `feat/phase-23b-design-system`
2. Commits:
   - `feat(web): /internal/design admin-gated system page`
   - `feat(web): ComponentsRegistry + static check`
   - `test(web): visual regression baselines`
   - `ci(web): design registry check`
3. PR, CI green, squash-merge

## Handoff
List of components registered. First-run visual-regression baseline
summary (total images committed, sizes).
