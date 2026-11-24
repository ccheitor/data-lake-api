# Roadmap

The repo is a single machine on purpose. The items below are what you would add to make the same API survive real ingestion, real identities, and more than one node. None of them are implemented. Each one replaces a shortcut called out in [Architecture](architecture.md) or [Security](security.md).

## Streaming ingestion with Kafka

Replace `make seed` as the way rows arrive. Producers write card events to Kafka. A sink (Flink, Spark Structured Streaming, or Kafka Connect with an Iceberg sink) appends them to the same table, partitioned by `day(ts)`.

A second consumer updates the low-latency store that backs `GET /v1/customers/{id}/transactions`. Point lookups then serve data that is seconds old, and the cache can drop a key when an event for that customer arrives, instead of waiting for `CACHE_TTL_SECONDS`. The HTTP contract does not change. The generator can stay as a producer of fake events for tests.

## OAuth2 and OIDC

Replace `API_KEYS` with client-credentials tokens (Keycloak, Auth0, Entra ID, or any OIDC provider). Scopes map to what `role` does now: one scope sees masked columns, another sees clear columns, and export reads are a separate scope. An API gateway (Kong, Envoy, APISIX) enforces rate limits and quotas per client. The audit log's `key_id` becomes the token subject, still not the secret.

Until that exists, the header and the two roles in [Security](security.md) are the whole identity system.

## Policy in the engine

Move the `concat` / `regexp_replace` expressions out of `api/app/security.py` and into Trino access control (file-based rules, Apache Ranger, or OPA). Add row filters there too, so a client cannot request an arbitrary `customer_id`. A catalog (DataHub or OpenMetadata) and a data contract per dataset sit next to that, so the 14-column schema is not documented only in this repository.

The API would send `SELECT` of the logical columns and trust the engine to mask. Exports stay correct because they run the same SQL.

## Kubernetes and Helm

Trino's Helm chart with autoscaled workers, the API as a Deployment with a horizontal pod autoscaler, Redis from an operator, and object storage that is not a single container. The export thread pool does not survive a new pod: replace it with a real queue (Celery, RQ, or a Kafka topic of jobs) whose workers are a separate Deployment. Prometheus and Grafana take the latency and request-rate charts that `make loadtest` prints once and forgets.

On a 12 GB Always Free node this is tight. The [Oracle guide](oracle-cloud-free.md) already says k3s or a Basic OKE cluster fits, and that Compose leaves more RAM for Trino. Use Kubernetes here when you are practicing the packaging, not because the laptop demo needs it.

## A UI

Metabase or Streamlit on top of Trino, for people who will not call the API. Point Metabase at the Trino coordinator and at `iceberg.payments.transactions`. It will issue its own SQL, which is another reason the mask has to live in Trino rather than only in this API. Streamlit is the lighter option for a single page of filters that calls `GET /v1/transactions` and shows the cursor.

Either UI is a client. It should use an API key or, later, a token. It should not be given the MinIO root password.

## Arrow Flight

For callers that want columns without writing a Parquet file first, serve the export with Arrow Flight or Flight SQL. The job model can stay: the request still returns quickly, and the worker still reads Trino in chunks, but the client streams batches instead of downloading an object. Presigned URLs remain the right answer for callers that just want a file.

## Table maintenance

The generator appends a file per batch and never compacts. Over time, small files and snapshots accumulate. A scheduled job (Airflow or a cron on the VM) should:

- Compact data files.
- Expire snapshots you no longer need to time-travel to.
- Rewrite files to honor the sort order after later appends, so point-lookup statistics stay tight.

A second partition transform, `bucket(customer_id)`, would make the uncached lookup open fewer files. That changes the table spec (drop and recreate, or an Iceberg partition evolution) and is worth doing when the cache is not allowed to absorb the latency. It does not remove the reason for the cache.

## Also still open

- TLS and a network policy that unpublishes Trino, Redis, and the console. [Deployment](deployment.md) and [Security](security.md) describe the target; the Compose file does not enforce it.
- Audit retention beyond a file on a Docker volume.
- A real catalog (JDBC/PostgreSQL or Glue) instead of the REST fixture and its SQLite file.
