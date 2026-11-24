# Security

This demo authenticates callers, masks two columns, and writes an audit line. It does not encrypt the network, rate-limit clients, or hide the storage and Trino ports. Treat the defaults as local-only.

Change `MINIO_ROOT_PASSWORD` and `API_KEYS` in `.env` before the host accepts traffic from anywhere but your own machine. The values in `.env.example` are published in the repository.

## API keys

`API_KEYS` is a comma-separated list of `secret:role`. The process reads it once, at import, into a dictionary. A key without a colon-separated role is stored as `analyst`.

The header is `X-API-Key`. Comparison is an exact dictionary lookup. There is no hashing at check time: the process holds the raw keys in memory, which is normal for a static dev list and wrong for a large production set.

On failure the response is `401` with `detail` `missing or invalid X-API-Key`. The body does not say whether the header was missing or wrong.

The id written to logs is `SHA-256(key)` truncated to 12 hex characters (`api/app/security.py`). Two different keys can theoretically share a prefix; 12 hex chars is a fingerprint for a demo log, not a primary key you should build authorization on. Authorization uses the raw key for the lookup and then the role on the `Principal`.

Export ownership uses that same fingerprint. `GET /v1/exports/{id}` returns the job when `job.owner == caller key_id` or `role == admin`. Otherwise it is `404`, including when the job exists and belongs to someone else. The response omits `owner`.

## Roles and masking

`can_see_pii` is `role == "admin"`. Every other role is masked, even a role you invent in `API_KEYS`.

Masking is SQL, not a Python post-process. Analyst select list:

```sql
concat('**** **** **** ', substr(card_number, -4)) AS card_number,
regexp_replace(customer_cpf, '^\d{3}\.\d{3}\.\d{3}', '***.***.***') AS customer_cpf
```

Admin select list uses the column names. The same list is used for paginated queries, point lookups, and exports, so a Parquet download cannot bypass the mask. The Redis key includes the role, so the two payloads are never stored under one key.

What masking does not do:

- It does not cover merchant name, city, or customer id. Those stay visible to every role.
- It does not apply a row filter. An analyst can request any `customer_id`.
- It does not apply to a Trino client that connects to port 8080 with the same catalog. Trino in this Compose file has no password and no system access control. The mask exists only in SQL this API chooses to send.

A production rule belongs in the engine (Trino file-based access control, Apache Ranger, or OPA), so every client gets it. This service would then stop duplicating the expression.

## Audit log

`AuditMiddleware` writes one JSON line per request, including `/health` and 401s, to the `audit` logger (stdout and `AUDIT_LOG_PATH`). Propagation is off, so the line is not also emitted by the root logger.

Fields: `ts` (UTC), `request_id`, `key_id`, `client` (the immediate TCP peer), `method`, `path`, `query`, `status`, `duration_ms`.

The query string is logged in full. Do not put secrets in it. The API key is only read from the header, and the header value is not a log field.

The file in Compose is on the `audit-logs` volume at `/var/log/api/audit.log`. It is not shipped anywhere. It rotates only if you add a rotator; the code uses a plain `FileHandler` in append mode. Two uvicorn workers both append to that file.

`client` is the Docker bridge address when you call the published port, not the caller's real address, unless you put a proxy in front that the app is taught to trust. The middleware does not read `X-Forwarded-For`.

## What a production version would add

The runnable demo stops at the list above. A deployment that holds real card data would add at least:

- **TLS** on the API, and on the storage endpoint used in presigned URLs. This Compose file is HTTP.
- **A private network.** Do not publish 8080 (Trino), 6379 (Redis), 8181 (catalog), or 9001 (console). Port 9000 is only needed for presigned downloads; prefer a separate HTTPS endpoint or a short-lived gateway path over leaving the S3 port open. The Oracle guide says the same thing.
- **Real identities.** Replace `API_KEYS` with OAuth2/OIDC client credentials, scopes per dataset, and per-client quotas at a gateway. The roadmap item is [Roadmap](roadmap.md).
- **Secret storage.** Keys and the MinIO password come from `.env`. A production process should read them from a secret manager, not from a file next to the clone.
- **Authorization beyond two roles.** Row filters (this customer, this merchant, this region) enforced in Trino, plus column masks in the catalog policy, so a leaked Trino port does not skip the API.
- **Rate limits and payload limits** at the gateway. Nothing in the API counts calls per key. Export workers are a small pool; they are not a quota.
- **Durable audit.** Append-only storage with retention, and a review of the query string so filters that are themselves sensitive can be hashed or dropped.
- **Job isolation.** Export state in Redis is not authenticated at the Redis layer (no password in this Compose file). Anyone who can reach Redis can read job records and cached rows. The cache holds the already-masked or clear JSON, depending on the role that filled it.

None of those are implemented here. The code is organized so the SQL select list and the `Principal` are the two places a later policy check would replace.
