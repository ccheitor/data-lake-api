# Configuration

Local values live in `.env`, copied from `.env.example` by `make up` when the file is missing. Compose interpolates `.env`. The API process reads its own environment, which Compose sets from those values plus a few fixed hostnames.

Restart the API container after changing a variable it reads. `docker compose up -d api` recreates it. Trino reads `trino/catalog/iceberg.properties` and `trino/etc/*` from the bind mounts; a change there needs `docker compose up -d trino`.

## `.env` and the API process

| Variable | Default | Who reads it | Effect |
|---|---|---|---|
| `MINIO_ROOT_USER` | `minioadmin` | Compose, mapped to `AWS_ACCESS_KEY_ID` for Trino, the catalog, the API, and the seed | S3 access key. Also the console user |
| `MINIO_ROOT_PASSWORD` | `minioadmin123` | Same, as `AWS_SECRET_ACCESS_KEY` | S3 secret. Also the console password |
| `WAREHOUSE_BUCKET` | `warehouse` | Compose, for the bucket name, `CATALOG_WAREHOUSE`, and the seed `WAREHOUSE` | Lake bucket. See the Trino caveat below |
| `EXPORTS_BUCKET` | `exports` | Compose and `Settings.exports_bucket` | Export bucket. Lifecycle in `minio-init` expires objects after 1 day |
| `API_KEYS` | `dev-analyst-key:analyst,dev-admin-key:admin` | API | Comma-separated `key:role`. Empty role means `analyst` |
| `S3_PUBLIC_ENDPOINT` | `http://localhost:9000` | API presigner | Host written into presigned URLs |
| `CACHE_TTL_SECONDS` | `300` | API | Redis TTL for point lookups, in seconds |
| `AWS_REGION` | `us-east-1` | API, Trino properties (`us-east-1` is also hardcoded in the properties file) | SigV4 region |
| `S3_ENDPOINT` | `http://localhost:9000` outside Compose; `http://minio:9000` inside the API and seed services | API uploads, seed, catalog | S3 API the process calls. Not the presign host |
| `TRINO_HOST` | `localhost`; Compose sets `trino` | API | Trino coordinator host |
| `TRINO_PORT` | `8080` | API | Trino HTTP port |
| `TRINO_CATALOG` | `iceberg` | API | Catalog name in SQL |
| `TRINO_SCHEMA` | `payments` | API and seed | Iceberg namespace. The generator reads this |
| `TABLE_NAME` | `transactions` | API only | Table name in SQL. The generator always writes `transactions` |
| `REDIS_URL` | `redis://localhost:6379/0`; Compose sets `redis://redis:6379/0` | API | Cache and export job store |
| `PRESIGN_SECONDS` | `3600` | API | Lifetime of each presigned URL |
| `EXPORT_WORKERS` | `2` | API | Threads in the export pool of each API process |
| `MAX_RANGE_DAYS` | `31` | API | Inclusive cap for `GET /v1/transactions` |
| `MAX_EXPORT_DAYS` | `366` | API | Inclusive cap for `POST /v1/exports` |
| `AUDIT_LOG_PATH` | `audit.log`; Compose sets `/var/log/api/audit.log` | API, at import | File handler for the audit logger. The parent directory must exist |
| `ICEBERG_REST_URI` | `http://localhost:8181`; Compose sets `http://iceberg-rest:8181` | Seed | REST catalog URL. The API does not call it; Trino does, via its properties file |
| `WAREHOUSE` | `s3://warehouse/` | Seed | Warehouse URI passed to PyIceberg |
| `SEED_ROWS` | `1000000` | Seed, only if you run `python -m seed.generate` without `--rows` | `make seed` passes `--rows` and ignores this |

`TABLE_NAME` and `TRINO_SCHEMA` on the API must match what the generator created. `make seed` uses the Compose value of `TRINO_SCHEMA` (`payments`) and a fixed table name `transactions`.

### Warehouse bucket caveat

`WAREHOUSE_BUCKET` changes the bucket that `minio-init`, the REST catalog, and the seed use. It does **not** change Trino's connector file. `trino/catalog/iceberg.properties` contains:

```
iceberg.rest-catalog.warehouse=s3://warehouse/
s3.endpoint=http://minio:9000
```

Leave the bucket named `warehouse`, or edit that file so the warehouse URI matches, then recreate the Trino container. Otherwise the generator and Trino point at different warehouses and queries see an empty catalog.

## Makefile

Variables can be overridden on the command line (`make seed ROWS=100000`).

| Variable | Default | Used by |
|---|---|---|
| `DOCKER` | `docker` | Every target. Set `sudo docker` when the socket is root-only |
| `COMPOSE` | `$(DOCKER) compose` | Up, seed, logs, ps, down, clean, and the Redis flush in `loadtest-all` |
| `ROWS` | `2000000` | `make seed` |
| `DAYS` | `90` | `make seed` |
| `MODE` | `cached` | `make loadtest`. One of `cached`, `direct`, `page`, `warmup` |
| `VUS` | `10` | `make loadtest` |
| `DURATION` | `60s` | `make loadtest`, except `warmup`, which ignores it and runs `HOT` iterations |
| `API_KEY` | `dev-analyst-key` | `make loadtest`, passed through to k6 |
| `HOT` | `1000` | `make loadtest`, as `HOT_CUSTOMERS` |

| Target | What it does |
|---|---|
| `make up` | Copy `.env.example` to `.env` if needed, `compose up -d --build`, run `scripts/wait-healthy.sh` |
| `make seed` | `compose --profile tools run --rm seed --rows $(ROWS) --days $(DAYS) --replace` |
| `make test` | `scripts/smoke-test.sh` against `BASE_URL` (default `http://localhost:8000`) |
| `make loadtest` | k6 in `grafana/k6:latest` with `--network host` |
| `make loadtest-all` | `FLUSHALL` on Redis, then direct, cached, warmup, cached again, then page |
| `make logs` | Follow the API logs |
| `make ps` | `compose ps` |
| `make down` | Stop containers, keep volumes |
| `make clean` | Stop containers and delete volumes (`down -v`) |

`make seed` always drops the table (`--replace`). Customer count, merchant count, batch size, and RNG seed are not Makefile variables. Pass them by running the seed service yourself:

```bash
docker compose --profile tools run --rm seed \
  --rows 100000 --days 30 --customers 5000 --merchants 500 --batch 50000 --seed 7 --replace
```

Flags are documented in [Data model](data-model.md).

Smoke-test environment, read by the shell script rather than Compose:

| Variable | Default |
|---|---|
| `BASE_URL` | `http://localhost:8000` |
| `API_KEY` | `dev-analyst-key` |
| `ADMIN_KEY` | `dev-admin-key` |

k6 also honors `BASE_URL` inside the container. The Makefile does not pass it, so the default `http://localhost:8000` applies, which is correct only with host networking.

## Compose services

`docker-compose.yml` sets `name: data-lake-api`, so volumes and the network are prefixed with that name.

| Service | Purpose | Ports on the host |
|---|---|---|
| `minio` | Silo, S3 API and console | 9000, 9001 |
| `minio-init` | Create `warehouse` and `exports`, add a 1-day expiry rule on exports, then exit | none |
| `iceberg-rest` | REST catalog, SQLite file on the `catalog-data` volume | 8181 |
| `trino` | SQL engine | 8080 |
| `redis` | Cache, 128 MB `allkeys-lru`, persistence off (`--save ""`) | 6379 |
| `api` | FastAPI, two uvicorn workers, no access log | 8000 |
| `seed` | Profile `tools` only | none |

Volumes: `minio-data`, `catalog-data`, `audit-logs`. `make clean` removes them.

The API waits until Trino is healthy and Redis has started. Trino waits until the catalog container has started, not until a catalog healthcheck passes (the fixture image does not define one). `minio-init` waits until Silo is healthy.

## Trino files

`trino/etc/config.properties` runs a single node (`coordinator=true`, `node-scheduler.include-coordinator=true`) with `query.max-memory` and `query.max-memory-per-node` at 800 MB. Those caps sit under the 1.5 GB heap.

`trino/etc/jvm.config` sets `-Xmx1536M`, a 256 MB direct-memory cap, and G1. The container `mem_limit` is 3 GB so the heap, metaspace, code cache, and direct buffers fit. Change heap and `mem_limit` as a pair. The [performance guide](performance.md) records what happened when they were not.

`trino/catalog/iceberg.properties` points the Iceberg connector at `http://iceberg-rest:8181`, enables the native S3 file system, and turns on path-style access. Credentials are `${ENV:AWS_ACCESS_KEY_ID}` and `${ENV:AWS_SECRET_ACCESS_KEY}`, which Compose fills from the MinIO root account.

## Generator environment

Inside the seed container the generator also reads the standard AWS variables and:

| Variable | Default in code | Compose value |
|---|---|---|
| `ICEBERG_REST_URI` | `http://localhost:8181` | `http://iceberg-rest:8181` |
| `WAREHOUSE` | `s3://warehouse/` | `s3://${WAREHOUSE_BUCKET}/` |
| `S3_ENDPOINT` | `http://localhost:9000` | `http://minio:9000` |
| `AWS_REGION` | `us-east-1` | `us-east-1` |
| `TRINO_SCHEMA` | `payments` | `payments` |

Path-style access is forced in code (`s3.path-style-access=true`), which Silo and MinIO require.
