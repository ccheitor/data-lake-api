# Load test results

Always publish the hardware, dataset and settings next to the numbers. These are single runs;
repeat each scenario at least twice before drawing conclusions.

## Run 1 — shared cloud VM (reference run by the author)

* **Date:** 2026-11-24
* **Hardware:** shared 8 vCPU x86_64 cloud VM (Intel Xeon), ~16 GB RAM, other workloads running on the host
* **Load generator:** k6 (`grafana/k6:latest`) on the **same host** (competes for CPU with the stack)
* **Stack limits:** Trino 484 single node, 1.5 GB heap / 3 GB container; API 2 uvicorn workers; Redis 128 MB
* **Dataset:** 2,000,000 rows, 90 days, 50,000 customers, partitioned by `day(ts)`
* **Command:** `make loadtest-all HOT=200 VUS=10 DURATION=60s`

| Scenario | VUs | Duration | Requests | Req/s | p50 | p95 | p99 | Errors | Cache hits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct (lookup, Trino every time) | 10 | 60 s | 244 | 4.0 | 2,530.3 ms | 3,045.2 ms | 3,166.3 ms | 0% | 0 |
| cached, cold (cache empty at start) | 10 | 60 s | 5,689 | 94.8 | 2.9 ms | 6.6 ms | 2,722.7 ms | 0% | 5,450 |
| warmup (ids 1..200, already cached) | 10 | — | 200 | — | 3.5 ms | 5.6 ms | 6.8 ms | 0% | 200 |
| cached, warm (100% hits) | 10 | 60 s | 157,964 | 2,632.6 | 3.7 ms | 5.2 ms | 6.1 ms | 0% | 157,964 |
| page (7-day window, 100 rows, Trino) | 10 | 60 s | 2,281 | 37.9 | 272.8 ms | 340.1 ms | 361.5 ms | 0% | 0 |

Single async export (not a load test): 2,000,000 rows → 47.7 MB Parquet (zstd), 30.9 s from
job start to object uploaded.

Observations:

* A single uncached point lookup takes ~0.8 s with an idle engine; with 10 concurrent users the
  queries queue inside Trino and latency grows to ~2.5 s — the engine is throughput-oriented.
* The cold-cache run shows the classic bimodal latency: hits in milliseconds, misses in seconds (p99).
* In an earlier attempt Trino had a 2 GB container limit with the image default heap
  (80% of RAM); under load the container was OOM-killed and every request failed. The explicit
  1.5 GB heap inside a 3 GB limit fixed it — size JVM heap *and* container limit together.

## Your run (template)

* **Date:**
* **Hardware:** (CPU model / cores, RAM, laptop or VM, e.g. OCI VM.Standard.A1.Flex 2 OCPU / 12 GB)
* **Load generator:** (same host or separate machine)
* **Dataset:** (`make seed ROWS=...`)
* **Command:**

| Scenario | VUs | Duration | Requests | Req/s | p50 | p95 | p99 | Errors | Cache hits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct | | | | | | | | | |
| cached, cold | | | | | | | | | |
| cached, warm | | | | | | | | | |
| page | | | | | | | | | |
