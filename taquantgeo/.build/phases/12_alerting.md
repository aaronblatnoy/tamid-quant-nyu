# Phase 12 — Alerting

## Metadata
- Effort: `standard`
- Depends on phases: 11
- Applies security-review: `no`
- Max phase runtime (minutes): 90
- External services:
  - `DISCORD_WEBHOOK_URL (optional)` — feature builds in disabled mode if
    missing; `FileSink` + `StdoutSink` always work

## Mission
A trading system that notices a data pipeline failure at 23:30 UTC but
can't tell anyone is worse than useless — the signal is corrupted and
will drive bad trades. This phase ships a pluggable alert-sink interface
so the existing data-quality / signal / risk / reconciliation code can
`emit(Alert)` without caring which destinations are configured. A
`FileSink` is always active (writes to `data/alerts.log`) so we never
lose an alert. Discord is the primary human channel; it auto-activates
when `DISCORD_WEBHOOK_URL` is in env. Later phases can add SMS / Slack /
PagerDuty behind the same protocol with no caller changes.

## Orientation
- `.build/handoffs/10_handoff.md`, `11_handoff.md`
- `packages/jobs/src/taquantgeo_jobs/data_quality.py` — first caller
- `packages/core/src/taquantgeo_core/config.py` — `discord_webhook_url`
- `packages/ais/src/taquantgeo_ais/gfw/events.py` — quality bar
- `docs/runbook.md` — define which alert maps to which runbook section

## Service preflight
- `DISCORD_WEBHOOK_URL` optional — if unset, log a WARN and omit
  DiscordSink from `make_sinks()`. FileSink + StdoutSink remain.

## Acceptance criteria
- New module `packages/core/src/taquantgeo_core/alerting.py` (alerting is
  cross-cutting; lives in core so every package can import it) with:
  - `@dataclass(frozen=True) class Alert: severity:
     Literal["info","warn","critical"]; component: str; title: str;
     body: str; run_id: str | None; context: dict[str, object]`
  - `class AlertSink(Protocol): def emit(self, alert: Alert) -> None: ...`
  - `class FileSink(AlertSink)` — writes JSON-lines to `data/alerts.log`
  - `class StdoutSink(AlertSink)` — prints formatted line
  - `class DiscordSink(AlertSink)` — posts to webhook via httpx; 5s
    timeout; on failure logs but does NOT raise
  - `make_sinks(settings) -> list[AlertSink]` factory — picks sinks
    based on configured env vars
  - `emit_all(alert, sinks)` — delivers to all sinks; any sink failure
    is logged, never raises
  - Rate-limit wrapper: `DedupSink(wrapped, window_minutes=30)` — drops
    repeat emits of `(component, title, severity)` unless severity
    escalated (e.g., warn→critical replays)
- Tests:
  - `test_file_sink_writes_json_line`
  - `test_stdout_sink_formats_line`
  - `test_discord_sink_posts_with_body_payload` (mocked httpx)
  - `test_discord_sink_swallows_error_on_timeout_does_not_raise`
  - `test_make_sinks_excludes_discord_when_url_unset`
  - `test_make_sinks_includes_file_stdout_always`
  - `test_emit_all_delivers_to_every_sink_even_if_one_fails`
  - `test_dedup_drops_repeat_within_window`
  - `test_dedup_allows_severity_escalation`
  - `test_dedup_releases_after_window_elapses` (time-mocked)
- Data-quality hookup: phase 10's `run_all_checks()` CLI adds
  `--alert-on-fail` flag — when set, failed checks emit via
  `emit_all(..., make_sinks(settings))`
- All quality gates green.

## File plan
- `packages/core/src/taquantgeo_core/alerting.py` — new
- `packages/core/tests/test_alerting.py` — new
- `packages/jobs/src/taquantgeo_jobs/data_quality.py` — add alert
  emission behind the new flag (do NOT change check logic)
- `packages/cli/src/taquantgeo_cli/jobs.py` — add `--alert-on-fail`
- `docs/runbook.md` — add "Alerts" section: how to locate alerts.log,
  how to silence Discord temporarily, how to acknowledge
- `CLAUDE.md` — note alerting module lives in core
- `data/alerts.log` — path reserved (no file committed; log is
  ephemeral, data/ is gitignored)

## Non-goals
- SMS / Twilio — candidate (the env vars exist, but SMS is critical-
  severity only and adds test complexity; separate phase)
- PagerDuty, Slack, email — candidate
- Alert escalation ladders (warn → critical after N minutes) — candidate
- Alert history UI — phase 23 (alerts feed page)

## Quality gates
- Format + lint + typecheck clean
- ≥10 new tests
- Pre-commit meta-review: multi-file, cross-cutting → full loop
- Runbook updated

## Git workflow
1. Branch `feat/phase-12-alerting`
2. Commits:
   - `feat(core): AlertSink protocol + File/Stdout/Discord sinks`
   - `feat(core): DedupSink for rate-limiting repeat alerts`
   - `feat(jobs): data-quality --alert-on-fail integration`
   - `test(core): sink + dedup + factory coverage`
   - `docs: runbook alerts section`
3. PR, CI green, squash-merge

## Handoff
Which sinks are active. A smoke test: emit a synthetic test alert to
verify end-to-end to Discord (if configured) and to alerts.log (always).
Record the success/failure.
