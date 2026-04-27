# Phase 17 — Frontend scaffold

## Metadata
- Effort: `standard`
- Depends on phases: 00
- Applies security-review: `yes`
- Reason: ships Auth.js v5 + GitHub OAuth + admin-gate — the auth foundation every later authenticated surface rides on; audit once here rather than rediscovering at phase 22.
- Max phase runtime (minutes): 120
- External services: none (Vercel deploy comes in phase 24)

## Mission
Scaffold the Next.js 15 dashboard to the point where every subsequent UI
phase (globe, signal dashboard, voyage explorer, backtest viewer, live
trading, alerts feed, design system) can just add routes. Get the hard
parts — routing layout, auth, theming, data fetching, command palette,
type-safe fetch wrapper — right once so the later phases only compose.
Linear-grade polish is the bar (shadcn/ui + Tailwind v4 + Inter + JetBrains
Mono + dark-mode-first). Do NOT ship any feature content in this phase —
just the shell + design tokens + shared components + auth.

## Orientation
- `.build/handoffs/00_handoff.md`
- `web/package.json` — current placeholder (per recent commit)
- `docs/adrs/0001-stack-decisions.md` — frontend stack rationale
- `CLAUDE.md` — conventions for the web workspace

## Service preflight
- Node.js 20+ must be available (`node --version` ≥ v20). If not,
  manual-setup entry (install via `nvm install 20`, estimated 5min).
- `pnpm` must be available. If not, `npm install -g pnpm` is acceptable
  in the phase without blocking.

## Acceptance criteria
- `web/package.json` is a real Next.js 15 app (not the placeholder).
  Core deps:
  - `next@15.x`, `react@19.x`, `react-dom@19.x`
  - `typescript@5.x`, `@types/node`, `@types/react`
  - `tailwindcss@4.x` with the new v4 config (CSS-based theming)
  - shadcn/ui initialized (`npx shadcn@latest init`; components
    committed to `web/src/components/ui/`)
  - `@tanstack/react-query@5.x`
  - `zustand@5.x`
  - `next-auth@5.x` (Auth.js v5) with GitHub provider
  - `cmdk` + shadcn `<Command>`
  - `lucide-react` icons
  - Inter + JetBrains Mono via `next/font`
- `web/src/app/layout.tsx` provides:
  - Dark-mode-first theme (`class="dark"` default; toggle persists in
    localStorage via Zustand)
  - Top bar with command palette (⌘K), user menu, environment badge
    (reads `NEXT_PUBLIC_APP_ENV`)
  - Persistent left sidebar (collapsible, icon+label). Nav items:
    Globe, Signal, Voyages, Backtests, Trading, Alerts (entries are
    there; pages empty until their phases). Design System entry visible
    only to admin (hidden link to `/internal/design`).
  - Route groups `(live)`, `(research)`, `(ops)`, `(trading)` created
    with empty index pages (each phase adds its own children)
- `web/src/components/ui/*` — shadcn primitives: Button, Card, Dialog,
  Drawer, DropdownMenu, Input, Label, Select, Separator, Sheet, Tabs,
  Toast, Skeleton, Table. Plus custom:
  - `DataTable` (wrapping @tanstack/react-table)
  - `EmptyState`, `ErrorBoundary`, `Spinner`, `KBD` (hint), `Badge`,
    `Sparkline` (pure SVG)
- `web/src/lib/api.ts` — typed fetch wrapper. Reads
  `NEXT_PUBLIC_API_URL`, injects auth headers, returns typed data or
  throws typed error. Used by every future data-fetch call.
- `web/src/lib/query.ts` — `QueryClient` + provider wiring; sensible
  stale time (30s) + retry defaults
- `web/src/auth/` — Auth.js v5 with GitHub provider, session
  callbacks, admin-only route gate for `/internal/*`. Env vars
  `NEXTAUTH_SECRET`, `GITHUB_ID`, `GITHUB_SECRET` required; if absent
  (dev mode), the app boots with a "no-auth dev user" stub and a
  bright warning banner
- Global keyboard shortcuts scaffolding (placeholder handler for
  `/`, `F`, `L` actions — wired by individual phases later)
- `web/README.md` documents: `pnpm install`, `pnpm dev`, `pnpm build`,
  env vars, link to phase 18+
- Lighthouse perf budget: `pnpm build && pnpm start` then programmatic
  lighthouse run on `/` — Perf ≥ 90, Accessibility ≥ 90, Best Practices
  ≥ 95. Phase records the scores in the handoff (not a CI gate yet —
  pages are empty, so scores should be easy; real gate applies in
  feature phases)
- Tests:
  - `web/tests/smoke.spec.ts` (Playwright) — app boots, `/`
    responds 200, sidebar renders, theme toggle works, command
    palette opens on ⌘K
- All quality gates green.

## File plan
- `web/package.json` — rewrite (replaces dependabot placeholder)
- `web/pnpm-lock.yaml`
- `web/tsconfig.json`, `web/next.config.ts`, `web/tailwind.config.ts`
  (v4 CSS-first; minimal)
- `web/src/app/layout.tsx`, `web/src/app/page.tsx`
- `web/src/app/(live)/`, `(research)/`, `(ops)/`, `(trading)/` with
  empty index pages
- `web/src/app/internal/design/page.tsx` — placeholder (phase 23b
  populates)
- `web/src/components/ui/*.tsx` — shadcn primitives + custom
- `web/src/components/shell/{Sidebar,TopBar,CommandPalette,ThemeToggle,
   EnvBadge}.tsx`
- `web/src/lib/{api,query,auth,fonts,shortcuts}.ts`
- `web/src/auth/` — Auth.js config + GitHub provider
- `web/src/styles/globals.css` — Tailwind v4 + design tokens (CSS vars
  for theme)
- `web/tests/smoke.spec.ts`
- `web/playwright.config.ts`
- `web/.env.example` — `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_APP_ENV`,
  `NEXTAUTH_SECRET`, `GITHUB_ID`, `GITHUB_SECRET`
- `docs/adrs/0017-frontend-scaffold.md` — NEW ADR: Next.js 15 + App
  Router, Tailwind v4, shadcn/ui, Auth.js v5, TanStack Query, Zustand,
  OSS Maplibre over Mapbox
- `CLAUDE.md` — add `web/` commands (pnpm dev, build, test) to useful
  commands; note design-token location
- `.github/workflows/web-ci.yml` — Node 20, pnpm install, typecheck,
  lint, build, Playwright smoke — ONLY on paths touching `web/**`

## Non-goals
- Any feature page content — phases 18-23 own that
- Vercel deploy config — phase 24
- Component library extraction (web/ internal only for now)
- i18n / localization — candidate
- Progressive Web App manifest — candidate

## Quality gates
- `pnpm run -C web lint` clean
- `pnpm run -C web typecheck` clean
- `pnpm run -C web build` succeeds
- Playwright smoke green
- Lighthouse scores met (recorded in handoff)
- Pre-commit meta-review full loop (multi-file)
- ADR 0017 committed
- Security-review subagent mandatory (Auth.js + GitHub OAuth + admin gate). Checks: session handling, CSRF, OAuth callback URL validation, admin-only route enforcement, secret handling in NEXT_PUBLIC_* vars (no secrets leak to client bundle).

## Git workflow
1. Branch `feat/phase-17-frontend-scaffold`
2. Commits:
   - `feat(web): Next.js 15 app shell + Tailwind v4 + shadcn`
   - `feat(web): Auth.js v5 + GitHub provider + dev stub`
   - `feat(web): command palette + theme toggle + env badge`
   - `feat(web): shared UI primitives + DataTable + Sparkline`
   - `feat(web): typed API client + TanStack Query`
   - `ci(web): pnpm install + typecheck + lint + build + Playwright smoke`
   - `docs: ADR 0017 frontend scaffold; CLAUDE.md updates`
3. PR, CI green, squash-merge

## Handoff
Lighthouse scores. Playwright smoke output. Any Next.js 15 /
Tailwind v4 / shadcn compatibility friction encountered. Note any
component that wasn't easily available in shadcn and was implemented
custom (future candidate: upstream back).
