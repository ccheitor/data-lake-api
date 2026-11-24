# API reference

Base URL in local Compose: `http://localhost:8000`.

Interactive docs generated from the code:

- Swagger UI: `GET /docs`
- ReDoc: `GET /redoc`
- OpenAPI document: `GET /openapi.json`

Keep this page and those routes together. FastAPI builds the OpenAPI document from the function signatures, so parameters, required fields, and many status codes match the code. Two handlers (`GET /v1/transactions` and the customer lookup) return a raw JSON `Response` so that timestamps and money use a custom encoder. Their response bodies are not fully described in the generated schema. The shapes below are what those handlers write.

All errors use FastAPI's default body, `{"detail": ...}`, except `/health`, which always returns the check object.

## Authentication

Protected routes require the header `X-API-Key`. There is no query-parameter key and no `Authorization` header.

Keys come from the `API_KEYS` environment variable: a comma-separated list of `key:role`. A key with no role is treated as `analyst`. The default in `.env.example` is:

| Key | Role | What the role can see |
|---|---|---|
| `dev-analyst-key` | `analyst` | Card number and CPF masked in SQL |
| `dev-admin-key` | `admin` | `card_number` and `customer_cpf` selected in the clear. Can also read another key's export job |

Only `admin` skips masking (`Principal.can_see_pii`). Any other role, including a custom name, is masked.

A missing or unknown key is `401`:

```json
{"detail": "missing or invalid X-API-Key"}
```

The audit log stores a 12-character SHA-256 prefix of the key, not the key. See [Security](security.md).

Every response from the app, including errors that pass through the middleware, gets an `X-Request-ID` header. Send one yourself and the same value is echoed; otherwise the API generates a hex id.

## `GET /health`

No API key. Checks Trino (`SELECT 1`), Redis (`PING`), and object storage (`HeadBucket` on the exports bucket).

`200` when every check is the string `ok`:

```json
{"status": "ok", "checks": {"trino": "ok", "redis": "ok", "object_storage": "ok"}}
```

`503` when any check throws. The failing value is `error:` plus the exception class name, not the message:

```json
{"status": "degraded", "checks": {"trino": "error: TrinoConnectionError", "redis": "ok", "object_storage": "ok"}}
```

## `GET /v1/transactions`

Filtered read, newest first, keyset pagination. Trino runs every call. There is no cache.

### Parameters

| Name | In | Required | Rules |
|---|---|---|---|
| `date_from` | query | yes | ISO date `YYYY-MM-DD` |
| `date_to` | query | yes | ISO date, inclusive |
| `merchant_category` | query | no | One of the category values below |
| `status` | query | no | `approved`, `declined`, or `reversed` |
| `min_amount` | query | no | Decimal, `>= 0` |
| `limit` | query | no | Integer 1–1000, default 100 |
| `cursor` | query | no | Value of `next_cursor` from the previous page |

Categories: `grocery`, `restaurants`, `fuel`, `pharmacy`, `electronics`, `travel`, `apparel`, `entertainment`, `utilities`, `health`, `education`, `transport`.

The timestamp predicate is `ts >= date_from at 00:00:00` and `ts < the day after date_to`, so `date_to` is inclusive and the predicate matches `day(ts)` partitions. The inclusive length of the range must be `<= MAX_RANGE_DAYS` (default 31). Longer ranges are rejected with a message that points at `/v1/exports`.

### Response `200`

```json
{
  "data": [
    {
      "transaction_id": "tx_000000001234",
      "ts": "2026-11-23T23:45:01.530896",
      "customer_id": 2,
      "merchant_id": 18,
      "merchant_name": "Example Shop",
      "merchant_category": "travel",
      "city": "São Paulo",
      "state": "SP",
      "amount": "778.57",
      "currency": "BRL",
      "channel": "pos",
      "status": "approved",
      "card_number": "**** **** **** 7844",
      "customer_cpf": "***.***.***-25"
    }
  ],
  "count": 1,
  "next_cursor": "WyIyMDI2LTExLTIzVDIzOjQ1OjAxLjUzMDg5NiIsICJ0eF8wMDAwMDAwMDEyMzQiXQ=="
}
```

`count` is the number of rows in this page, not the size of the whole filter. `next_cursor` is JSON `null` on the last page. `amount` is a string, never a JSON number. `ts` is ISO-8601 from a naive timestamp (no offset). Column order follows the select list in `api/app/security.py`.

An admin key returns the same fields, with `card_number` and `customer_cpf` unmasked.

### Example

```bash
curl -s -H 'X-API-Key: dev-analyst-key' \
  'localhost:8000/v1/transactions?date_from=2026-11-17&date_to=2026-11-24&merchant_category=travel&min_amount=500&limit=2'
```

Next page: repeat every filter and append `&cursor=` plus the previous `next_cursor`. The cursor is URL-safe Base64; still URL-encode it when you put it in a query string (`=` becomes `%3D`).

### Errors

| Status | When | `detail` |
|---|---|---|
| 401 | Missing or unknown API key | `missing or invalid X-API-Key` |
| 400 | `date_to` before `date_from` | `date_to must be >= date_from` |
| 400 | Inclusive day count greater than `MAX_RANGE_DAYS` | `date range limited to 31 days (protects the engine; use /v1/exports for more)` |
| 400 | Cursor is not decodable | `invalid cursor` |
| 422 | A parameter fails validation (bad date, unknown category or status, negative amount, `limit` outside 1–1000) | Pydantic / FastAPI validation list |

An expired-looking but well-formed cursor that simply points at a timestamp you did not generate returns an empty page and `next_cursor: null`, not 400.

## `GET /v1/customers/{customer_id}/transactions`

Latest transactions for one customer. This is the cache-aside path.

### Parameters

| Name | In | Required | Rules |
|---|---|---|---|
| `customer_id` | path | yes | Integer |
| `limit` | query | no | Integer 1–500, default 50 |
| `cache` | query | no | Boolean, default `true`. `false` always queries Trino |

There is no date filter. The SQL is:

```sql
SELECT ... FROM iceberg.payments.transactions
WHERE customer_id = ?
ORDER BY ts DESC
LIMIT {limit}
```

### Response `200`

Headers:

| `X-Cache` | Meaning |
|---|---|
| `HIT` | Body came from Redis |
| `MISS` | Trino ran, and the body was stored |
| `BYPASS` | `cache=false`. Redis was not read or written |

```json
{
  "customer_id": 1234,
  "data": [ ],
  "count": 0
}
```

`data` items have the same columns as the paginated endpoint, including masking by role. `count` is `len(data)`.

### Example

```bash
curl -si -H 'X-API-Key: dev-analyst-key' \
  'localhost:8000/v1/customers/1234/transactions?limit=20' | head -n 20
```

The first call is typically `X-Cache: MISS`. The same URL again, before the TTL, is `HIT`. Add `cache=false` to force Trino.

### Errors

| Status | When |
|---|---|
| 401 | Missing or unknown API key |
| 422 | `customer_id` is not an integer, or `limit` is outside 1–500 |

A customer with no rows is `200` and an empty `data` array.

## `POST /v1/exports`

Start a bulk export. Body is JSON.

### Body

| Field | Required | Rules |
|---|---|---|
| `date_from` | yes | ISO date |
| `date_to` | yes | ISO date |
| `merchant_category` | no | Same category enum as the paginated query |
| `status` | no | `approved`, `declined`, or `reversed` |
| `min_amount` | no | Decimal, `>= 0` |

The inclusive range must be `<= MAX_EXPORT_DAYS` (default 366). There is no 31-day cap on this route. The SQL has the same timestamp predicate and the same role-based select list as the paginated query, without `ORDER BY` or `LIMIT`.

### Response `202`

```json
{
  "job_id": "ff7c0c0e0b1a4f0a9c0e0b1a4f0a9c0e",
  "status": "queued",
  "status_url": "/v1/exports/ff7c0c0e0b1a4f0a9c0e0b1a4f0a9c0e"
}
```

`job_id` is a UUID hex string. `status` on this response is `queued` even if a worker starts immediately after. Poll `status_url`.

### Errors

| Status | When | `detail` |
|---|---|---|
| 401 | Missing or unknown API key | `missing or invalid X-API-Key` |
| 400 | Inclusive range longer than `MAX_EXPORT_DAYS` | `date range limited to 366 days (protects the engine; use /v1/exports for more)` |
| 422 | Body is not the schema: bad types, unknown category, negative amount, or `date_to` before `date_from` | Validation error |

Inverted dates are `422` here and `400` on `GET /v1/transactions`. The export model rejects them in a Pydantic validator; the GET path rejects them in the handler. Both are client errors. The 366-day cap is still `400`, because it is checked in the shared filter helper after the body parses.

### Example

```bash
curl -s -X POST -H 'X-API-Key: dev-analyst-key' \
  -H 'Content-Type: application/json' \
  localhost:8000/v1/exports \
  -d '{"date_from":"2026-11-01","date_to":"2026-11-24","merchant_category":"travel"}'
```

## `GET /v1/exports/{job_id}`

Poll a job. The caller must own it (`key_id` of the key that created it) unless the caller's role is `admin`. Anyone else, including a well-formed id that does not exist, gets `404` with `detail` `job not found`. The owner id is not included in the response.

While the worker runs, `status` moves from `queued` to `running`. `rows_written` updates as chunks land. Terminal values are `succeeded` and `failed`.

Succeeded:

```json
{
  "job_id": "ff7c0c0e0b1a4f0a9c0e0b1a4f0a9c0e",
  "status": "succeeded",
  "request": {
    "date_from": "2026-11-01",
    "date_to": "2026-11-24",
    "merchant_category": "travel",
    "status": null,
    "min_amount": null
  },
  "created_at": "2026-11-24T19:00:00.000000+00:00",
  "started_at": "2026-11-24T19:00:00.100000+00:00",
  "finished_at": "2026-11-24T19:00:30.000000+00:00",
  "rows_written": 427,
  "object_key": "2026/11/24/ff7c0c0e0b1a4f0a9c0e0b1a4f0a9c0e.parquet",
  "size_bytes": 18259,
  "duration_s": 1.2,
  "download_url": "http://localhost:9000/exports/2026/11/24/ff7c0c0e0b1a4f0a9c0e0b1a4f0a9c0e.parquet?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=...",
  "download_expires_in_s": 3600
}
```

`download_url` is minted on this GET, not stored. Call again after it expires. `rows_written` can be `0` for a filter that matched nothing; that is still `succeeded`, and the object is a Parquet file with the schema and no data rows.

Failed:

```json
{
  "job_id": "ff7c0c0e0b1a4f0a9c0e0b1a4f0a9c0e",
  "status": "failed",
  "error": "short exception text",
  "finished_at": "2026-11-24T19:00:05.000000+00:00"
}
```

`error` is truncated to 500 characters. There is no `download_url`.

The Redis record lives for 7 days (`export:{job_id}`). After that, GET is 404.

### Example

```bash
curl -s -H 'X-API-Key: dev-analyst-key' \
  localhost:8000/v1/exports/ff7c0c0e0b1a4f0a9c0e0b1a4f0a9c0e
curl -s -o export.parquet '<download_url>'
python3 -c "import pyarrow.parquet as pq; print(pq.read_table('export.parquet').num_rows)"
```

## Pagination behavior

Ordering is `ts DESC, transaction_id DESC`. The cursor encodes that pair. The next query adds `ts < cursor_ts OR (ts = cursor_ts AND transaction_id < cursor_tx)`.

Do not edit the cursor. Do not reuse a cursor with a different `date_from`, `date_to`, category, status, or `min_amount`: the predicate is still applied, but the page boundary belonged to another filter, so you can skip or repeat rows.

There is no previous-page cursor and no total count.

## Masking behavior

For every role other than `admin`, the select list replaces the two sensitive columns:

```sql
concat('**** **** **** ', substr(card_number, -4)) AS card_number
regexp_replace(customer_cpf, '^\d{3}\.\d{3}\.\d{3}', '***.***.***') AS customer_cpf
```

A generated CPF looks like `123.456.789-00`. After the replace it looks like `***.***.***-00`. A card becomes `**** **** **** ` plus the last four characters stored in the column. Values that do not match the CPF pattern are returned unchanged by `regexp_replace`. Admin SQL selects the raw columns, so the JSON and the Parquet file contain whatever the generator wrote (digits, no spaces, for cards).

Masking does not depend on the cache. The cache key includes the role, and the SQL that fills a miss is already masked. `cache=false` uses the same select list.

## Audit line

Each request appends one JSON object to the API process log and to `AUDIT_LOG_PATH`:

```json
{
  "ts": "2026-11-24T19:12:34.446442+00:00",
  "request_id": "5407ff75abcd",
  "key_id": "ddd2c99c9421",
  "client": "172.18.0.1",
  "method": "GET",
  "path": "/v1/transactions",
  "query": "date_from=2026-11-17&date_to=2026-11-24&limit=2",
  "status": 200,
  "duration_ms": 2.7
}
```

`key_id` is null when no key was sent. `query` is null when the query string is empty. The path is not allowed to carry the secret: the key is a header.
