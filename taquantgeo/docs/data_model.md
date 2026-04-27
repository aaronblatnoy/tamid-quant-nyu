# Data model

Proposed tables for the operational Postgres. Parquet schemas mirror these where applicable. **This is a draft** — finalized when Phase 0 closes and we run the first Alembic migration.

## Conventions

- Primary keys are `bigserial` unless noted.
- All timestamps are `TIMESTAMPTZ`, always UTC.
- All money values are integer cents (`bigint`). No floats for money.
- Soft-delete via `deleted_at` only where listed.
- Foreign keys with `ON DELETE RESTRICT` unless noted (we want to know if something is referenced before deleting).

## Tables

### `vessels`

Vessel registry sourced from AIS static (type 5) messages. One row per vessel.

| Column | Type | Notes |
|---|---|---|
| id | bigserial PK | |
| imo | bigint UNIQUE | International Maritime Organization number; the durable identity |
| mmsi | bigint UNIQUE | Maritime Mobile Service Identity; can change over a vessel's life |
| name | text | Mutable; latest seen |
| ship_type | int | AIS code; 80–89 = tankers; we filter to 80 (oil) |
| dwt | int | Deadweight tons; VLCCs ≥ 200,000 |
| length_m | int | Length overall |
| beam_m | int | |
| flag | text | ISO 3166-1 alpha-2 country |
| first_seen_at | timestamptz | |
| last_seen_at | timestamptz | |

### `positions` (parquet only — too high-volume for Postgres)

Raw AIS position messages. Stored in R2 as `s3://taquantgeo-archive/positions/year=YYYY/month=MM/day=DD/*.parquet`. Queried via DuckDB.

| Column | Type | Notes |
|---|---|---|
| mmsi | bigint | Join to `vessels.mmsi` (latest) or via cross-walk table |
| imo | bigint | |
| ts | timestamptz | UTC always |
| lat | double | |
| lon | double | |
| sog | float | Speed over ground, knots |
| cog | float | Course over ground, degrees |
| heading | int | 0–359 |
| nav_status | smallint | AIS code (0=under way using engine, 1=at anchor, 5=moored, etc.) |
| draft | float | Reported draft, meters; key signal for loaded/ballast |
| destination | text | AIS-reported destination string |

### `voyages`

A voyage = one VLCC's trip from a loading region to a discharge region. Built by the voyage state machine.

| Column | Type | Notes |
|---|---|---|
| id | bigserial PK | |
| vessel_id | bigint FK → vessels.id | |
| state | text | `in_progress` \| `completed` \| `aborted` |
| direction | text | `laden` \| `ballast` |
| origin_region | text | e.g. `persian_gulf` |
| destination_region | text | e.g. `china` |
| route | text | e.g. `td3c` (NULL until classified) |
| started_at | timestamptz | First position inside origin region |
| ended_at | timestamptz | First position inside destination region (or signal lost > 7d) |
| ton_miles_total | bigint | Cargo tons × great-circle distance, end-to-end |
| ton_miles_remaining | bigint | Snapshotted daily for in-progress voyages |
| confidence | float | 0–1; classifier confidence in laden/ballast call |
| created_at | timestamptz | |
| updated_at | timestamptz | |

### `signals`

Daily snapshot of computed tightness on each tracked route. Shipped in alembic 0002; math defined in [ADR 0007](adrs/0007-tightness-signal-definition.md).

| Column | Type | Notes |
|---|---|---|
| id | bigserial PK | |
| as_of | date | Trading day this signal applies to |
| route | text | e.g. `td3c` |
| forward_demand_ton_miles | bigint | Sum of cargo_tons × remaining_distance_nm over in-progress laden VLCC voyages |
| forward_supply_count | int | Ballast VLCCs arriving in origin region within 15 days |
| dark_fleet_supply_adjustment | int | SAR dark-fleet candidates at PG loading terminals in prior 7 days (subtracted from supply, supply floored to 1) |
| tightness | double | demand ÷ effective_supply (units = ton-miles per ballast vessel) |
| tightness_z | double NULL | Z-score vs 90-day rolling baseline; NULL during warmup (<30 prior samples) or zero-variance window |
| components | jsonb | Audit dict: `vlcc_vessels_considered`, `in_progress_laden_voyages`, `cargo_tons_fallback_used`, `great_circle_fallbacks`, `avg_sog_fallback_used`, `route_total_distance_nm`, `dark_fleet_candidates_used`, `effective_supply_raw`, `supply_floor_clamped`, `z_score_sample_size`, `ballast_in_progress` |
| created_at | timestamptz | |

UNIQUE `(as_of, route)` via `uq_signals_as_of_route` index. Secondary index `ix_signals_route_as_of_desc` on `(route, as_of)` for backtest scans.

### `prices`

Daily OHLCV for the shipping-equity basket.

| Column | Type | Notes |
|---|---|---|
| id | bigserial PK | |
| ticker | text | `FRO` / `DHT` / `INSW` / `EURN` / `TNK` |
| as_of | date | Trading day |
| open_cents | bigint | |
| high_cents | bigint | |
| low_cents | bigint | |
| close_cents | bigint | |
| volume | bigint | |

UNIQUE `(ticker, as_of)`.

### `positions_book` (different from AIS `positions` — naming TBD)

Open trading positions held by us.

| Column | Type | Notes |
|---|---|---|
| id | bigserial PK | |
| ticker | text | |
| qty | int | Negative for short |
| avg_price_cents | bigint | |
| opened_at | timestamptz | |
| last_updated_at | timestamptz | |
| strategy | text | `td3c_tightness_v1` etc. |

### `orders`

Every order we submit to the broker. Lives forever. **Append-only.**

| Column | Type | Notes |
|---|---|---|
| id | bigserial PK | |
| client_order_id | text UNIQUE | Idempotency key we generate |
| broker_order_id | text | Assigned by IBKR |
| ticker | text | |
| side | text | `buy` / `sell` |
| qty | int | |
| order_type | text | `market` / `limit` / `stop` |
| limit_cents | bigint NULL | |
| status | text | `submitted` / `partial` / `filled` / `cancelled` / `rejected` |
| submitted_at | timestamptz | |
| filled_at | timestamptz NULL | |
| risk_check | jsonb | Snapshot of risk gate state at submission time |
| reason | text | Why we placed it (signal ref, manual, recon-fix) |

### `fills`

Per-fill records. **Append-only.**

| Column | Type | Notes |
|---|---|---|
| id | bigserial PK | |
| order_id | bigint FK → orders.id | |
| qty | int | |
| price_cents | bigint | |
| commission_cents | bigint | |
| filled_at | timestamptz | |

### `audit_log`

Generic append-only log of every state-changing event in the trade module.

| Column | Type | Notes |
|---|---|---|
| id | bigserial PK | |
| occurred_at | timestamptz | |
| actor | text | `system` / `operator:<email>` |
| event_type | text | `order_submitted` / `kill_switch_engaged` / `recon_mismatch` / etc. |
| payload | jsonb | Event-specific data |

### `reconciliations`

Daily snapshot of expected vs actual positions.

| Column | Type | Notes |
|---|---|---|
| id | bigserial PK | |
| as_of | date | |
| expected | jsonb | Map ticker → qty per our system |
| actual | jsonb | Map ticker → qty per IBKR |
| matched | bool | true iff expected == actual |
| diff | jsonb NULL | Differences, if any |
| created_at | timestamptz | |

## Indexes (initial)

- `vessels (imo)`, `vessels (mmsi)`, `vessels (ship_type, dwt)`
- `voyages (vessel_id, state)`, `voyages (state, route, started_at)`
- `signals (route, as_of DESC)`
- `prices (ticker, as_of DESC)`
- `orders (status, submitted_at DESC)`, `orders (client_order_id)`
- `audit_log (occurred_at DESC)`, `audit_log (event_type, occurred_at DESC)`

## Open questions

- Cross-walk between MMSI (changeable) and IMO (durable) — need a `vessel_aliases` table?
- Multi-route support: `signals.route` is text now; if we add many routes, normalize to a `routes` table.
- Partitioning `audit_log` and `orders` by month once volume warrants.
