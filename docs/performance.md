# Performance

There is no single transactions-per-second number for this stack. A data API serves two different jobs, and they need different machinery. The figures below are the run recorded in [loadtest/RESULTS.md](../loadtest/RESULTS.md). They are not a benchmark of your laptop. The hardware is part of the result.

## Why the hot path is not Trino

Trino is built to scan a lot of data per query: plan, list Iceberg files, schedule splits, read Parquet. That fixed cost dominates when the query returns twenty rows for one customer. The customer filter is not the partition column (`day(ts)`), so a lookup touches every day in the table. Sorting files by `customer_id` lets Trino skip some row groups. It does not remove the planning cost.

The API therefore uses cache-aside Redis for `GET /v1/customers/{id}/transactions`:

1. Build the key `cust:{id}:{role}:{limit}`.
2. On hit, return the stored JSON (`X-Cache: HIT`).
3. On miss, query Trino, store the JSON with `CACHE_TTL_SECONDS`, return it (`X-Cache: MISS`).

`cache=false` skips both steps and is how the load test calls Trino directly (`X-Cache: BYPASS`).

A paginated query stays on Trino. The date range is required and capped at 31 days so the scan is partition-pruned, and the cursor keeps deep pages from turning into `OFFSET` scans. Throughput there is "interactive", not "high TPS".

A bulk extract is not a TPS problem. `POST /v1/exports` returns as soon as the job is queued. One job moves millions of rows into one Parquet object. Few requests, large bytes, and the API does not hold the full result.

If the cache is cold, the first call for each customer pays Trino and the following calls do not, until the TTL. This demo does not invalidate keys when data changes, because nothing streams changes in. A production low-latency store (Redis, Cassandra, Postgres, or similar) would be filled by a consumer next to the ingestion path. That split is the point of the architecture, described in [Architecture](architecture.md).

## How to run k6

On Linux, from the repo root, with the stack up and data loaded:

```bash
make loadtest MODE=direct  VUS=10 DURATION=60s
make loadtest MODE=cached  VUS=10 DURATION=60s
make loadtest MODE=warmup  HOT=200
make loadtest MODE=cached  HOT=200
make loadtest MODE=page
make loadtest-all HOT=200
```

`make loadtest-all` flushes Redis, then runs direct, cached (cold), warmup, cached (warm), and page, in that order. `HOT` is the customer id range k6 samples (`HOT_CUSTOMERS`). The published run used `HOT=200`. The Makefile default is `1000` if you omit it.

| `MODE` | What k6 requests |
|---|---|
| `direct` | `GET /v1/customers/{id}/transactions?limit=20&cache=false` |
| `cached` | Same path with `cache=true`. Ids are random in `1..HOT` |
| `warmup` | One request per id from 1 through `HOT`, so the following cached run is hot |
| `page` | `GET /v1/transactions` over the last 7 days, `limit=100` |

k6 runs as `grafana/k6:latest` with `--network host` and writes a one-line JSON summary plus a file under `loadtest/results/`. Those JSON files are gitignored. The script counts a response as a cache hit only when the `X-Cache` header equals `HIT`.

`--network host` reaches `localhost:8000` on a Linux daemon. Docker Desktop on macOS and Windows does not attach that container to the host network, so k6 would call localhost inside the container and miss the API. On those machines install k6 locally and run `loadtest/k6-lookup.js` against `http://localhost:8000`, passing the same `MODE`, `VUS`, `DURATION`, `API_KEY`, and `HOT_CUSTOMERS` environment variables.

Locust is the host-side alternative (it does not use `--network host`):

```bash
pip install locust
locust -f loadtest/locustfile.py --host http://localhost:8000 --headless -u 20 -r 5 -t 60s --tags cached
```

`--tags direct` switches to `cache=false`. The file has no `page` task. `HOT_CUSTOMERS` and `API_KEY` are environment variables, defaulting to 1000 and `dev-analyst-key`.

Record the machine, the row count, and the command next to any numbers you keep. The template at the bottom of [loadtest/RESULTS.md](../loadtest/RESULTS.md) is there for that. One 60-second pass is not a capacity curve. The results file says to repeat each scenario at least twice, and to raise `VUS` until p95 or errors move, if you want a knee.

## Measured results

The only measured run checked into the repo is **Run 1** in [loadtest/RESULTS.md](../loadtest/RESULTS.md), dated 2026-11-24.

Conditions, copied from that file:

- Shared 8 vCPU x86_64 cloud VM (Intel Xeon), about 16 GB RAM, other workloads on the host.
- k6 on the same host, so the generator competes with the stack for CPU.
- Trino 484, one node, 1.5 GB heap, 3 GB container limit. API with 2 uvicorn workers. Redis maxmemory 128 MB.
- Dataset: 2,000,000 rows, 90 days, 50,000 customers, partitioned by `day(ts)`.
- Command: `make loadtest-all HOT=200 VUS=10 DURATION=60s`.

| Scenario | VUs | Duration | Requests | Req/s | p50 | p95 | p99 | Errors | Cache hits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct (lookup, Trino every time) | 10 | 60 s | 244 | 4.0 | 2,530.3 ms | 3,045.2 ms | 3,166.3 ms | 0% | 0 |
| cached, cold (cache empty at start) | 10 | 60 s | 5,689 | 94.8 | 2.9 ms | 6.6 ms | 2,722.7 ms | 0% | 5,450 |
| warmup (ids 1..200, already cached) | 10 | — | 200 | — | 3.5 ms | 5.6 ms | 6.8 ms | 0% | 200 |
| cached, warm (100% hits) | 10 | 60 s | 157,964 | 2,632.6 | 3.7 ms | 5.2 ms | 6.1 ms | 0% | 157,964 |
| page (7-day window, 100 rows, Trino) | 10 | 60 s | 2,281 | 37.9 | 272.8 ms | 340.1 ms | 361.5 ms | 0% | 0 |

One async export, not part of the k6 loop: 2,000,000 rows became a 47.7 MB ZSTD Parquet object in 30.9 seconds from job start until the upload finished.

Observations from that same note:

- A single uncached point lookup took about 0.8 seconds with an idle engine. With 10 concurrent users the queries queued in Trino and latency moved to about 2.5 seconds.
- The cold-cache run is bimodal: hits in milliseconds, misses in seconds (see p99).
- An earlier attempt limited the Trino container to 2 GB and left the image default heap (80% of RAM). Under load the container was OOM-killed and requests failed. Setting a 1.5 GB heap inside a 3 GB limit fixed it. Size the heap and the container limit together.

Read those as an order-of-magnitude comparison on that VM: direct lookup about 4 requests/s, warm cache about 2,600 requests/s at a p95 of 5.2 ms, paginated scan about 38 requests/s. A quieter machine, a different row count, or k6 on another host will not reproduce the same figures. Run it and write the new row into `loadtest/RESULTS.md`.

## Storage: files vs lake

The same generator was also written out as the file formats a partner would still receive, and as Parquet / Iceberg. The full note (10 million row check, bytes a query actually reads, linear projections, S3 list-price sketch, and caveats) is [loadtest/STORAGE.md](../loadtest/STORAGE.md). Reproduce it from the repo root with `pip install -r loadtest/requirements.txt` and `python loadtest/storage_compare.py --rows 2000000 --rows-large 10000000`.

Measured on 2026-11-24, 2,000,000 rows, 90 days, seed 42. Hardware in that note: shared 8-core Intel Xeon, 16 GiB RAM.

| Format | Bytes | Size | Bytes/row | vs uncompressed CSV |
|---|---:|---:|---:|---:|
| CSV, uncompressed (UTF-8, header, Excel-style quoting) | 292,630,765 | 279.1 MiB | 146.32 | 1.000× |
| Fixed-width / positional TXT (CNAB-style) | 554,505,021 | 528.8 MiB | 277.25 | 1.895× |
| JSON Lines (one object per row; what a naive API would return) | 690,630,624 | 658.6 MiB | 345.32 | 2.360× |
| CSV gziped at level 6 (GNU gzip default) | 65,139,015 | 62.12 MiB | 32.57 | 0.223× |
| Iceberg table (zstd, day(ts) partitions, metadata included) | 67,355,337 | 64.24 MiB | 33.68 | 0.230× |
| Single Parquet file (zstd, dictionary, statistics) | 37,867,262 | 36.11 MiB | 18.93 | 0.129× |

The Iceberg figure includes metadata. Data files alone were 66,836,438 bytes across 720 Parquet files. A day-partitioned 7-day slice of those files was 5,180,671 bytes; the same 7 days limited to `customer_id` and `amount` was 754,415 bytes. Do not treat the 1-billion-row and 1 TiB projections in the note as measurements.
