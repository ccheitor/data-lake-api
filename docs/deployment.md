# Deployment

Four ways to run the same idea, from the laptop outward. Only the first one is fully specified by this repository. The others are deployment shapes that reuse the API and the table format.

## Local Compose

This is the default, and the one `make test` covers. [Getting started](getting-started.md) is the procedure: Docker, `make up`, `make seed`, `make test`.

Keep it local unless you have changed `.env`. The published API keys and the MinIO password are in the repository. Compose publishes Trino, Redis, the catalog, and the storage console on the host.

Data lives in three Docker volumes (`minio-data`, `catalog-data`, `audit-logs`). `make down` keeps them. `make clean` deletes them. There is no backup.

## Oracle Cloud Always Free

The stack fits a free Ampere A1 VM because every image has an arm64 build and the container limits sum to about 5.2 GB, under the 12 GB that current Always Free A1 capacity is described as. The walkthrough, the limits table, the idle-reclamation warning, and the option to point S3 at OCI Object Storage are in [oracle-cloud-free.md](oracle-cloud-free.md).

Read that page before you size the VM. The free-tier CPU and RAM allowance has changed over time; the note in that guide is dated 2026-11-24 and tells you to re-check Oracle's Always Free page. Use `make loadtest-all` on the VM and append a row to [loadtest/RESULTS.md](../loadtest/RESULTS.md) rather than assuming the x86 numbers carry over.

On that VM, do not leave 8080, 6379, 8181, or 9001 open to the internet. Set `S3_PUBLIC_ENDPOINT` to an address the downloader can reach if exports are fetched from outside.

## A small VPS

Any Linux VPS with 4 vCPUs and 16 GB of RAM is enough for the Compose file as written (Trino's 3 GB limit plus the rest of the stack and the OS). Two vCPUs will run it and will show lower Trino throughput; measure rather than guessing.

1. Install Docker and Compose v2, plus `make` and `python3`.
2. Clone the repo, copy `.env.example` to `.env`, and replace `MINIO_ROOT_PASSWORD` and `API_KEYS`.
3. Set `S3_PUBLIC_ENDPOINT` to the public base URL clients will use for downloads.
4. Open SSH from your IP, and open only the API port (8000, or 443 behind a reverse proxy). Leave Trino, Redis, the catalog, and the console bound to localhost. You can drop the host ports in `docker-compose.yml` for those services and keep the Docker network.
5. Put TLS in front of the API (Caddy or a similar proxy). Presigned URLs need TLS too if the storage port is reachable; otherwise keep downloads on a private network.
6. `make up`, then `make seed` with a row count the disk can hold. Parquet of 2 million rows in the published export was 47.7 MB; the lake files are larger than that single export because of partitioning, but they are still small relative to a 50 GB disk. Watch the Silo volume.
7. Enable Docker on boot (`systemctl enable docker`). Services already use `restart: unless-stopped`.

Backups are your problem on a single VPS: snapshot the disk, or accept that `make clean` and a dead volume are data loss. The catalog SQLite file and the warehouse bucket must be backed up together. A catalog without its Parquet files, or files without the catalog, is not a readable table.

This layout is the Compose file on a rented machine. It will not scale past one Trino process, and the export workers still die with the API process.

## AWS, cost-oriented sketch

Two sketches. Neither is a module in this repo. Both keep Iceberg (or at least Parquet) as the table and HTTP as the interface. Prices move; the comparison is about what you pay for, not a quote.

### S3, Athena, and Lambda

Use this when queries are bursty and you can tolerate Athena's latency.

- **S3** holds the warehouse and the export bucket. You drop Silo. The generator's PyIceberg settings (`s3.endpoint`, path-style access) change to real AWS endpoints, or you write with the AWS SDK and register the table in Glue.
- **Glue Data Catalog** (or another Iceberg catalog Athena supports) replaces the REST fixture.
- **Athena** replaces Trino for the paginated query and the export scan. You pay per data scanned. Partitioning by day, which this table already does, is what keeps that bill tied to the request's date range instead of the whole lake. A point lookup that touches every partition is an expensive Athena query; do not put it on this path.
- **Lambda** (or a small container behind API Gateway) can implement the same three routes: build the SQL, start an Athena query, store the query id the way this API stores a job, and return a presigned S3 URL when the query finishes. Athena's own output location is already an object you can presign.
- **Redis or DynamoDB** is still required if you want the warm-cache lookup rate from the load test. Athena will not do thousands of requests per second at millisecond latency. The cache-aside behavior in `api/app/main.py` is the piece to keep; only the miss path changes from Trino to Athena.

Cost shape: almost nothing while idle, then a charge per query based on bytes read, plus S3 storage and API requests, plus Lambda duration. A load test like `make loadtest MODE=direct` against Athena would scan the same partitions over and over and is the wrong tool. The warm-cache scenario is the one that stays cheap, because it never reaches Athena.

What you give up: the Compose-shaped Trino UI, the exact SQL dialect quirks (Athena is Trino-derived, but not this Trino 484 image), and sub-second misses. What you gain: no instance to patch when nobody is querying.

### EC2, or a single ECS task

Use this when you want the repo's behavior, including Trino misses, and you will query often enough that an always-on node is simpler than per-query pricing.

- One EC2 instance (or one ECS/Fargate task with a persistent volume) runs this Compose file, with S3 instead of Silo if you would rather not operate a disk. Swapping Silo for S3 is the same endpoint change as in the Oracle object-storage section of [oracle-cloud-free.md](oracle-cloud-free.md).
- ElastiCache Redis replaces the Redis container if you want the cache to outlive a task restart. Point `REDIS_URL` at it.
- Put an Application Load Balancer or a small TLS proxy on port 443 in front of the API only.

Cost shape: you pay for the instance and the disk or S3 every hour, including overnight. A 4 vCPU / 16 GB instance is the class that matches the Compose limits. That is more expensive than idle Athena and more predictable than a scan-heavy Athena workload. Add data-transfer charges if presigned downloads leave the region.

Do not run the published load test against a production Athena warehouse to "see the TPS". The direct-Trino column in the results is already the warning: 4 requests/s at multi-second latency, on a dataset of 2 million rows, with the generator on the same box.

## What stays the same everywhere

- The HTTP contract in [API reference](api-reference.md): keys, roles, cursors, export jobs, presigned downloads.
- Iceberg partitioning by `day(ts)` and the ban on unbounded interactive scans.
- A low-latency store in front of point lookups.
- Masking in the query that produces both JSON and Parquet, until a catalog policy replaces it.
