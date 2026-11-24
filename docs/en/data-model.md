# Data model

One Iceberg table, `payments.transactions`, in the `iceberg` catalog. The namespace comes from `TRINO_SCHEMA` (default `payments`). The generator always names the table `transactions`. The API's `TABLE_NAME` defaults to the same.

The table is Iceberg format version 2, Parquet, compression ZSTD. Writers are PyIceberg (`python -m seed.generate`). Readers are Trino and, for files the API has already exported, anything that reads Parquet.

## Columns

| # | Column | Iceberg type | Nullable | Notes |
|---|---|---|---|---|
| 1 | `transaction_id` | string | no | `tx_` plus a zero-padded id. Unique per row |
| 2 | `ts` | timestamp | no | Event time, no time zone. Partition source |
| 3 | `customer_id` | long | no | 1-based index into the customer pool |
| 4 | `customer_cpf` | string | no | Brazilian CPF from Faker, `000.000.000-00`. Sensitive |
| 5 | `card_number` | string | no | Digits from Faker. Sensitive |
| 6 | `merchant_id` | long | no | 1-based index into the merchant pool |
| 7 | `merchant_name` | string | no | Company name |
| 8 | `merchant_category` | string | no | One of 12 categories, fixed per merchant |
| 9 | `city` | string | no | Merchant city |
| 10 | `state` | string | no | Merchant state code (`estado_sigla`) |
| 11 | `amount` | decimal(12, 2) | no | Ticket size. The API returns it as a string |
| 12 | `currency` | string | no | Always `BRL` in this generator |
| 13 | `channel` | string | no | `pos`, `ecommerce`, `contactless`, `recurring` |
| 14 | `status` | string | no | `approved`, `declined`, `reversed` |

`customer_cpf` and `card_number` are the only columns the API masks. The other twelve are selected for every role. Channel is stored and returned, and it is not a filter on the HTTP API. Category, status, and a minimum amount are.

Field ids are stable (1–14). Do not reuse an id for a different column if you evolve the schema; Iceberg identifies columns by id.

## Partitioning

```text
partition by day(ts)   -- partition field name ts_day
```

A query with `ts >= date_from` and `ts < the day after date_to` only opens the partitions for those dates. That is why `GET /v1/transactions` requires a date range and refuses more than 31 days. A point lookup filters on `customer_id` alone, so it does not get this pruning. The cache exists because of that.

The generator draws each timestamp uniformly across the requested number of days, ending at the container's current time, with the window starting at local midnight `DAYS` ago. Rows at the edges can fall on the first or last calendar day depending on the clock.

## Sort order

Each data file is written sorted by `customer_id`, then `ts` (both identity transforms, ascending). PyIceberg records that sort order on the table. Inside a batch the generator also sorts the Arrow table the same way before `append`, so Parquet row-group statistics on `customer_id` are tight.

Trino can then skip row groups whose min/max `customer_id` does not contain the lookup. It still has to consider every day partition. The published load test measured that remaining cost: a direct lookup under 10 concurrent users sat around 2.5 seconds. Numbers and hardware are in [Performance](performance.md).

## How rows are generated

`api/seed/generate.py` builds pools once, then samples them with NumPy. Faker is `pt_BR`, so names, cities, CPFs, and state codes look Brazilian. The RNG seed and Faker's seed both default to 42, so a given `--rows`, `--customers`, `--merchants`, and `--seed` repeat the same pools. Transaction ids also include a time-based offset, so ids are not stable across runs. `make seed` passes `--replace`, which drops the table first.

Pools:

- Customers: unique CPF (`fake.unique.cpf()`) and a card number each.
- Merchants: company name, city, state, and one category chosen uniformly from the 12 names.
- Each merchant gets a log-normal mean for the ticket. Travel, electronics, education, health, and utilities sit higher; everything else uses 4.0. Amounts are in cents, clipped to the range 1.00 through 50,000.00, then stored as `decimal(12,2)`.

Sampling per row:

- Customer index is Zipf with exponent 1.3, modulo the pool size, so a few customers are heavy.
- Merchant index is uniform.
- Channel probabilities: pos 0.45, ecommerce 0.30, contactless 0.20, recurring 0.05.
- Status probabilities: approved 0.93, declined 0.05, reversed 0.02.

Commits are one Iceberg append per batch. The default batch is 250,000 rows, which is what keeps memory flat when `ROWS` is in the millions. The seed container limit is 2 GB.

## Generator options

`make seed ROWS=2000000 DAYS=90` becomes:

```text
python -m seed.generate --rows 2000000 --days 90 --replace
```

| Flag | Default | Meaning |
|---|---|---|
| `--rows` | `SEED_ROWS` or 1,000,000 | How many transactions to append |
| `--days` | 90 | History window ending now |
| `--customers` | 50000 | Size of the customer pool |
| `--merchants` | 5000 | Size of the merchant pool |
| `--batch` | 250000 | Rows per Iceberg commit |
| `--seed` | 42 | NumPy and Faker seed |
| `--replace` | off | Drop the table if it exists, then create it |

`make seed` always sets `--replace` and does not pass the pool sizes. To change them, run the service directly. The command is in [Configuration](configuration.md).

Creating the table sets properties `write.parquet.compression-codec=zstd` and `format-version=2`. If the table already exists and you do not pass `--replace`, the generator appends and does not alter the spec. A changed partition spec requires `--replace` or a manual drop.

## What the export file contains

An export is not the Iceberg table. It is a new Parquet object in the `exports` bucket, path `YYYY/MM/DD/{job_id}.parquet` in UTC, ZSTD, with the 14 API columns. Types in that file are timestamp (microseconds), int64 for the two ids, decimal(12, 2) for `amount`, and string for the rest. Sensitive columns are already masked or not, matching the caller's role, because the worker writes whatever Trino returned.

The exports bucket is not an Iceberg table. Nothing in the catalog points at it. The one-day lifecycle rule on that bucket deletes the object; it does not affect `warehouse`.
