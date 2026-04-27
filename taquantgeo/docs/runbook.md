# Runbook

Operational procedures. Populated as we deploy each component.

## Daily checklist (when live)

- [ ] AIS ingestion latency < 60s (check Sentry / Better Stack)
- [ ] Voyage classifier output count within 2σ of 30-day mean
- [ ] Signal computed by 23:30 UTC
- [ ] Reconciliation matched (expected == actual at IBKR)
- [ ] No unacknowledged Discord critical alerts
- [ ] P&L within daily loss limit

## Incident response

### Kill switch
```bash
# Halts all new orders without restart.
# Existing orders + open positions are unaffected.
ssh hetzner "cd /opt/taquantgeo && echo 'KILL_SWITCH=true' >> .env"
docker compose restart app
```

### Reconciliation mismatch
1. Check `audit_log` for the date.
2. Diff `positions_book` vs IBKR portfolio.
3. If unexplained, **engage kill switch first**, then investigate.
4. Manual reconciliation: insert `audit_log` entry `manual_recon` with full justification.

### AIS feed dead
1. Check AISStream.io status page.
2. Restart streamer container: `docker compose restart streamer`.
3. If down > 1h, switch to AISHub fallback (TODO once configured).

## Deploy

```bash
# Tag a release; CI builds + ships to Hetzner.
git tag v0.1.0
git push origin v0.1.0
# Watch the deploy workflow on GitHub Actions.
```

## Rollback

```bash
ssh hetzner "cd /opt/taquantgeo && git checkout v0.0.9 && docker compose up -d --build"
```

## Known limitations

### CodeQL unavailable

CodeQL static analysis on **private** repos requires GitHub Advanced Security. On free private repos the scan runs but can't upload results, so the workflow always fails. The workflow file was removed. Re-add when:
- The repo goes public, OR
- The account upgrades to a plan with Advanced Security.

Re-enable by recreating `.github/workflows/codeql.yml` (see git history for the file).

### Branch protection unavailable

Server-side branch protection rules and rulesets require **GitHub Pro** (or making the repo public) for personal-account private repos. Currently neither is in place.

**Mitigations**:
- Pre-commit hooks (ruff, gitleaks, conventional-commit) catch most issues client-side.
- CI runs on every push to `main` (`.github/workflows/ci.yml`).
- Self-discipline: use `feat/*` and `fix/*` branches + PRs for non-trivial changes even though it isn't enforced.

If the project ever becomes a multi-developer effort or starts trading meaningful capital, upgrade to GitHub Pro ($4/mo) and run:

```bash
gh api -X PUT repos/sn12-dev/taquantgeo/branches/main/protection --input - <<EOF
{
  "required_status_checks": {"strict": true, "contexts": ["lint-typecheck", "test"]},
  "enforce_admins": false,
  "required_pull_request_reviews": {"required_approving_review_count": 0, "dismiss_stale_reviews": true},
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
```
