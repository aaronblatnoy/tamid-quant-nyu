# Phase 06 handoff

## Status
`completed`

## What shipped

- `packages/prices/pyproject.toml` — new workspace package with
  deps on `taquantgeo-core`, polars, pyarrow, sqlalchemy, psycopg,
  and `yfinance>=0.2.50`. Added `taquantgeo-prices` to root
  `[tool.uv.sources]` and `taquantgeo_prices` to ruff's
  `known-first-party` in the root `pyproject.toml`.
- `packages/prices/src/taquantgeo_prices/__init__.py` — exports
  `fetch_ohlcv`, `DEFAULT_TICKERS`, `upsert_prices`, `Price`.
- `packages/prices/src/taquantgeo_prices/yfinance_client.py`
  (~200 lines). Public surface: `DEFAULT_TICKERS` (frozen
  `("FRO", "DHT", "INSW", "EURN", "TNK")`), `fetch_ohlcv(ticker,
  start, end) -> pl.DataFrame`. Internal: `_yfinance_download`
  (lazy-imports yfinance so cold-path CLI isn't slowed by the
  transitive curl_cffi load), `_schema`, `_empty_frame`, `_is_nan`
  (`isinstance(float) and math.isnan`). Adjusted-close semantics:
  OHL scaled by `adj_close / close` ratio so intra-day bar
  integrity survives splits and the close is always the adjusted
  value. Tolerates four failure modes without raising: empty frame
  (delisting / rate-limit / unknown symbol), missing required
  column (`Adj Close` schema drift), NaN Volume (filled to 0 before
  `int64` cast — IntCastingNaNError would otherwise crash the
  fetch), and arbitrary `_yfinance_download` exceptions
  (try/except returns `_empty_frame()` + WARN). Module docstring
  is the executable summary of ADR 0008.
- `packages/prices/src/taquantgeo_prices/models.py` — SQLAlchemy
  `Price` registered on the shared `Base`. Columns:
  `id` bigserial (INTEGER via `with_variant(sqlite)`), `ticker`
  (String(16), indexed), `as_of` (Date, indexed), `open_cents`,
  `high_cents`, `low_cents`, `close_cents`, `volume` (all BigInteger,
  non-null), `created_at` (TIMESTAMPTZ server-default now()).
  Single composite unique index `ix_prices_ticker_as_of_uq` on
  `(ticker, as_of)` (round-1 review dropped the redundant
  non-unique composite). No `from __future__ import annotations`
  (SQLAlchemy Mapped resolver rule from CLAUDE.md).
- `packages/prices/src/taquantgeo_prices/persistence.py` —
  `upsert_prices(session, rows: pl.DataFrame | Iterable[Mapping])
  -> int`. Accepts either a polars DataFrame (duck-typed via
  `hasattr(iter_rows)`) or any iterable of mapping-likes with the
  seven canonical keys. Dedups by `(ticker, as_of)` keeping the
  last-seen row BEFORE dialect branching (round-1 fix —
  Postgres ON CONFLICT DO UPDATE cannot affect the same row twice,
  sqlite's delete-then-add would violate the unique index). Postgres
  path uses `pg_insert(...).on_conflict_do_update(index_elements=
  ["ticker", "as_of"], set_=<all-non-key-cols>)`; sqlite path
  deletes colliding `(ticker, as_of)` pairs via `or_(*conditions)`
  then `add_all` inserts. Returns post-dedup row count.
- `infra/alembic/versions/0003_prices_table.py` — migration.
  Creates `prices` with the exact column set matching `Price`;
  creates `ix_prices_ticker`, `ix_prices_as_of`, and the unique
  composite `ix_prices_ticker_as_of_uq` (round-1 fix dropped the
  redundant non-unique composite). `uv run alembic upgrade head`
  is clean against local docker-compose Postgres; `alembic
  downgrade 0002 → upgrade head` round-trip verified.
- `packages/cli/src/taquantgeo_cli/prices.py` — new typer subapp
  (`prices_app`). Three commands:
  - `taq prices backfill --since 2017-01-01 [--until YYYY-MM-DD]
    [--ticker FRO ...]` — bulk historical fetch, defaults to the
    five-ticker basket.
  - `taq prices update [--ticker FRO ...]` — incremental. Looks up
    `MAX(as_of)` per ticker, fetches `MAX+1 day → today_UTC`,
    upserts. Fresh DB defaults to 2017-01-01.
  - `taq prices show --ticker FRO --tail 10` — diagnostic tail.
- `packages/cli/src/taquantgeo_cli/main.py` — registers
  `prices_app` under `app.add_typer(prices_app, name="prices")`.
- `packages/cli/pyproject.toml` — adds `taquantgeo-prices` dep.
- `packages/prices/tests/conftest.py` — `fake_yfinance` fixture
  monkeypatches `_yfinance_download` (NOT `yfinance.download`
  itself — the real import is lazy inside the wrapper, so patching
  at `yfinance.download` would miss the path). Deterministic
  weekday-bar generator; per-ticker behaviour controlled via
  `configure(...)` kwargs: `empty`, `adjust_ratio`, `base_close`,
  `raise_exc`, `multi_index`, `missing_col`, `nan_rows`,
  `volume_nan_rows`.
- `packages/prices/tests/test_yfinance_client.py` — 8 unit tests.
  Pins: canonical schema (`Utf8, Date, Int64 x 5`), delisted-empty
  + WARN, adjusted-close integer-cent math with 2:1 split,
  missing-column schema-drift WARN path, OHLC-NaN row-skipping,
  Volume-only-NaN-coerces-to-zero without dropping the row
  (round-1 coverage addition), multi-index column flattening,
  default-basket tuple identity.
- `packages/prices/tests/test_prices_persistence.py` — 6 unit
  tests + 2 integration. Unit: insert, polars-DataFrame input,
  empty-rows no-op, idempotency on re-upsert, expected-columns
  pin, two-ticker coexistence on same day. Integration: alembic
  upgrade head creates `prices` with the unique `(ticker, as_of)`
  index; Postgres ON CONFLICT idempotency hermetic against a
  reserved ticker label.
- `packages/prices/tests/test_prices_cli.py` — 4 CLI tests via
  `typer.testing.CliRunner`. Runs `taq prices backfill | update
  | show | --help` against an in-memory sqlite (session_scope
  monkeypatched at the import site in `taquantgeo_cli.prices`).
  Round-1 tightened: `show` pins reverse-chronological ordering
  (not just row count); `update` now also reads back the DB to
  prove rows persisted (not just the call arguments).
- `docs/adrs/0008-equity-price-source.md` — new ADR pinning the
  source choice (yfinance for v0, Polygon on the candidate list),
  the adjusted-close semantics, and the delisting / empty-frame
  contract. Three alternatives considered: Polygon.io, Alpha
  Vantage, Baltic TD3C spot.
- `CLAUDE.md` — registers `packages/prices/` in Repo layout,
  bumps stack description to 9 packages, and documents the
  three new CLI commands (backfill / update / show) in the
  useful-commands block.
- `uv.lock` — regenerated (yfinance + its transitive deps).

## PR

- URL: https://github.com/sn12-dev/taquantgeo/pull/11
- CI status at merge: **green** (label 5s, lint-typecheck 25s,
  test 53s — all passing first run)
- Merge sha: `c58d10c`

## Surprises / findings

**Tests-dir basename collision.** Initially created
`packages/prices/tests/test_persistence.py` and `test_cli.py`,
both of which collide with identical basenames already under
`packages/signals/tests/`. pytest (when the test dirs have no
`__init__.py`) uses module-basename resolution and errors out
with `import file mismatch`. Two workable fixes: (a) add
`__init__.py` to both directories to namespace them properly —
which is the pattern `packages/ais/tests/` uses — or (b) rename
the new files to unique basenames. Chose (b)
(`test_prices_persistence.py`, `test_prices_cli.py`) because it
is one-line-per-file and leaves the sibling `signals/` tree
unchanged. Worth flagging for phase 08 (backtest tests) — if
those tests replicate the persistence / CLI test pattern, they
must either pick unique basenames or add `__init__.py` locally.

**SQLite + autoincrement quirk**, already known from phase 04,
re-applies here. `BigInteger` on sqlite does NOT auto-increment
(rowid aliasing only fires for plain `INTEGER`), so the `Price`
model uses `BigInteger().with_variant(Integer, "sqlite")` for
the `id` column. Without that variant, unit tests on sqlite fail
with `NOT NULL constraint failed: prices.id`. Every new model
needs the same variant until we drop sqlite test support.

**`ruff TC002/TC003` noise around Pydantic-style annotations in
tests.** `pytest.LogCaptureFixture` and `Path` in test fixtures
need a `TYPE_CHECKING` guard or ruff flags them as unused
runtime imports. Same story phase 04 had with typer `Path`
defaults. Resolved by putting the `pytest` and `date` imports
under `if TYPE_CHECKING` in `conftest.py` and
`test_yfinance_client.py`.

**yfinance's volume column silently NaN-laden on half-days.**
The first implementation did
`raw["Volume"].astype("int64").to_list()`; this crashes with
`pandas.errors.IntCastingNaNError` the first time yfinance
returns a half-trading-day bar with NaN volume (common on U.S.
early-close days like the day after Thanksgiving). Would have
been a silent time bomb — tests didn't hit it because the mock
generator didn't plant Volume NaN. Round-1 meta-review surfaced
it as a CRITICAL finding; fix is `.fillna(0)` before `.astype`,
and `test_fetch_ohlcv_volume_only_nan_coerces_to_zero` now pins
the post-fix contract. Phase 08's backtest should filter
`volume == 0` rows if it wants to drop half-day bars rather
than treat them as normal.

**Intra-batch `(ticker, as_of)` duplicates are a real failure
mode.** Postgres's `ON CONFLICT DO UPDATE` cannot affect the
same row twice in a single statement (CardinalityViolation);
sqlite's delete-then-add branch would similarly violate the
unique index on flush. Round-1 meta-review caught this; fix is
a dict-based dedup before dialect branching, keeping the last
occurrence. This matters practically because the `update` CLI's
resume logic uses `(latest + 1 day) → today_UTC`, and a
subsequent `backfill --since 2017-01-01` call would re-include
every day overlapping with persisted state — the dedup keeps
that benign.

**Single session for the backfill loop was flagged as a MAJOR
resilience gap** but remains unchanged post-round-1 because the
try/except fix in `fetch_ohlcv` makes the exception path
unreachable in practice (every failure now surfaces as an empty
frame, which the CLI handles row-by-ticker). If phase 11
(scheduler) ever needs per-ticker transactional isolation, the
fix is trivial — move `session_scope()` inside the ticker loop
— but it's not load-bearing for v0.

**Redundant composite index caught by the style review.** Initial
model had both `ix_prices_ticker_as_of` (non-unique) and
`ix_prices_ticker_as_of_uq` (unique) on the same `(ticker,
as_of)` columns. Since a unique index serves all the equality
and range lookups a non-unique index would, the non-unique one
was dead write-cost. Dropped in both `models.py` and the 0003
migration. Signals has both indexes but on DIFFERENT column
orders (`route,as_of` vs `as_of,route`), so the pattern there
is intentional; ours was accidental.

**`show` CLI prints a bespoke ASCII table** rather than JSON
like the other `taq` commands. Flagged in style review as a minor
inconsistency; left as-is because a diagnostic `show --tail 10`
is meant for eyeballing, not piping to jq. If a future phase
needs machine-readable output, easy swap to the phase-04 JSON
shape.

**Round-2 meta-review returned empty findings** across all four
fix targets (volume NaN, exception resilience, intra-batch
dedup, test tightening). Loop converged on round 2 — no round 3
needed for this `standard` effort phase.

**`test_update_cli_resumes_from_latest_as_of` manually
monkeypatches `yfc._yfinance_download`** (not via
`monkeypatch.setattr`) because it needs to wrap the
already-patched fake with a call-logger. The manual restore in
`finally` is a KeyboardInterrupt window, but the test is
self-contained and the scope is a single function — judged
acceptable vs refactoring the fixture shape.

## Test count delta

- Before: 167 (phase-05 handoff's recorded after-count; driver's
  `build_state.json.test_count_baseline=53` is stale, unchanged
  since phase 00)
- After: 182 (delta **+15** non-integration, +2 integration = +17 total)
- New non-integration tests (by name):
  - `test_fetch_ohlcv_parses_yfinance_frame`
  - `test_fetch_ohlcv_empty_on_delisting_logs_warn`
  - `test_ohlcv_converted_to_integer_cents`
  - `test_fetch_ohlcv_missing_column_returns_empty_with_warn`
  - `test_fetch_ohlcv_skips_nan_bars`
  - `test_fetch_ohlcv_volume_only_nan_coerces_to_zero`
  - `test_fetch_ohlcv_handles_multi_index_columns`
  - `test_all_tickers_default_list_matches_docs`
  - `test_upsert_prices_inserts_rows`
  - `test_upsert_idempotent`
  - `test_upsert_accepts_polars_dataframe`
  - `test_upsert_empty_rows_is_noop`
  - `test_prices_table_has_expected_columns`
  - `test_upsert_different_tickers_coexist`
  - `test_backfill_cli_end_to_end`
  - `test_update_cli_resumes_from_latest_as_of`
  - `test_show_cli_prints_tail`
  - `test_backfill_cli_help_lists_prices`
- New integration tests:
  - `test_alembic_upgrade_head_creates_prices_table`
  - `test_upsert_prices_postgres_on_conflict_is_idempotent`
- Tests removed: none.

Phase contract required ≥7 new tests. Delivered **20** (18
non-integration in `packages/prices` + 2 integration-marked).
Driver should update
`build_state.json.test_count_baseline` to 182 (non-integration).

## Optional services not configured

None strictly required. yfinance is network-required for real
backfill but CI tests are 100% mocked (`fake_yfinance` patches
the wrapper). `DATABASE_URL` is set locally; the one phase-06
integration pair skips when it's unset.

## Deferred / open questions

- **Polygon backup.** The `POLYGON_API_KEY` env var is already
  in `taquantgeo_core.config.Settings` and the ADR lists
  Polygon as a drop-in replacement if Yahoo breaks. Not wired up
  in v0 — would need a `polygon_client.py` with the same
  `fetch_ohlcv` signature. Candidate for a later phase if
  Yahoo's public API breaks.
- **EURN → CMB.TECH rollover.** Once standalone EURN fully
  delists, the basket effectively drops to four names. Adding
  `CMB.TECH` (yfinance ticker `CMB.BR` on Euronext Brussels) is
  a one-line basket change in `DEFAULT_TICKERS` but it's a
  different instrument and would need its own IC validation in
  phase 07. Deliberately scoped out.
- **Intraday / real-time bars.** Out of v0 scope per ADR 0008.
  Phase 07 and 08 work on daily closes. If live trading needs
  intraday for execution, that's a separate phase.
- **`update` CLI does not re-fetch the latest persisted bar.**
  Resume starts at `latest + 1 day`, so a provisional bar on
  `latest` is never refreshed. Trade-off acknowledged — to
  revalidate, a future phase could change resume to `latest`
  (and rely on upsert idempotency), at the cost of one extra
  fetch per run per ticker.
- **Per-ticker session scope.** Left as single-transaction for
  simplicity (since `fetch_ohlcv` no longer raises). If phase 11
  wants strict per-ticker isolation, move `session_scope` into
  the loop.
- **`today` is UTC.** During UTC 00:00-04:00 the NY exchange
  date is one day behind; a daily job firing at those hours will
  see no new bar. Fine for APScheduler runs at EoD US time,
  flag if we ever schedule at UTC midnight.
- **Dividend vs price-return adjustment.** Yahoo's `Adj Close`
  adjusts for BOTH splits and dividends (total-return price),
  which is correct for backtest P&L but slightly inflated vs a
  pure price-return series. ADR 0008 calls this out. Phase 07's
  IC study should be explicit about which series it regresses.

## Ideas for future phases

Nothing new appended to `.build/candidate_phases.md` this run.
Two informal ideas surfaced in review but not promoted:

- **Polygon.io backup client.** Same `fetch_ohlcv` signature,
  different source. Only worth building if yfinance goes down.
- **Tests-dir `__init__.py` namespace convention.** The basename
  collision would be cleaner if every `packages/*/tests/` had
  an `__init__.py`. That's a repo-wide chore, not a vertical
  phase. Maybe bundle with a test-hygiene sweep later.

## For the next phase

- **Canonical paths** (stable after this phase):
  - Postgres `prices` table with columns
    `(ticker, as_of, open_cents, high_cents, low_cents,
    close_cents, volume, created_at)`.
  - Unique on `(ticker, as_of)` via
    `ix_prices_ticker_as_of_uq`.
- **`fetch_ohlcv` never raises.** Every consumer can treat an
  empty polars frame as "no data"; there is no need for
  try/except at the call site.
- **`upsert_prices` dedups by `(ticker, as_of)` keeping the
  last row.** If two rows in the same batch collide, the later
  one wins. Idempotent on re-run.
- **Adjusted close at ingest.** Phase 07 IC code and phase 08
  backtest can consume `close_cents` directly for return-based
  math without any further adjustment. The `close_cents` field
  equals yfinance's `Adj Close * 100` (rounded).
- **DEFAULT_TICKERS = ("FRO", "DHT", "INSW", "EURN", "TNK").**
  A test pins this tuple exactly; adding / removing a ticker
  breaks that test loudly (by design — the basket change is
  material to every downstream IC regression).
- **Float sensitivity.** `close_cents` is `round(adj_close * 100)`
  — an exact integer; ties resolve via banker's rounding. For
  per-bar absolute cents this is noise-free, but return series
  computed downstream (division of two integers) will carry
  ULP-level drift. Test tolerances should be `abs=1` for cents
  and `rel=1e-6` for returns.
- **Alembic 0003 is idempotent** via `alembic upgrade head`.
  Down → up round-trip verified locally; the Postgres
  integration test `test_alembic_upgrade_head_creates_prices_table`
  asserts the unique index exists by column set (not name), so
  a future migration renaming the index would still satisfy it.
- **Network-free CI.** yfinance is mocked at
  `taquantgeo_prices.yfinance_client._yfinance_download`.
  Future phases that depend on prices data should mirror this
  pattern: mock at the wrapper, not at the third-party entry
  point, so the lazy-import path is covered.
- **Log hygiene.** Each ticker's fetch emits one INFO
  (`fetching {sym} [...]`); any WARN implies a degradation
  (empty frame, schema drift, exception). Phase 07 can
  regression-filter days where any WARN fired if it wants to
  drop price-feed-uncertain days.
