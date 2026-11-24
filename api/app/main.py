"""Data Lake API: serve large-volume data over HTTP instead of exchanging files.

Three access patterns, each matched to the right engine:
  * GET  /v1/transactions                    filtered, keyset-paginated reads (Trino over Iceberg)
  * GET  /v1/customers/{id}/transactions     point lookup with Redis cache (high TPS path)
  * POST /v1/exports + GET /v1/exports/{id}  async bulk export to Parquet + presigned URL
"""
import base64
import json
import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

from . import exports
from .audit import AuditMiddleware
from .cache import redis_client
from .config import settings
from .db import query
from .security import COLUMN_NAMES, Principal, require_api_key, select_list
from .storage import presign, s3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(
    title="Data Lake API",
    version="1.0.0",
    description="Query a lakehouse (Iceberg + Trino) over HTTP: paginated reads, cached point lookups and async Parquet exports.",
)
app.add_middleware(AuditMiddleware)

Category = Literal["grocery", "restaurants", "fuel", "pharmacy", "electronics", "travel",
                   "apparel", "entertainment", "utilities", "health", "education", "transport"]
Status = Literal["approved", "declined", "reversed"]


def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)  # keep exact money values; never float
    raise TypeError(type(o))


def _rows(cols, rows):
    return [dict(zip(cols, r)) for r in rows]


def _dump(obj) -> str:
    return json.dumps(obj, default=_json_default)


# ---------- cursor helpers (keyset pagination) ----------
def encode_cursor(ts: datetime, tx_id: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([ts.isoformat(), tx_id]).encode()).decode()


def decode_cursor(c: str) -> tuple[datetime, str]:
    try:
        ts, tx = json.loads(base64.urlsafe_b64decode(c.encode()))
        return datetime.fromisoformat(ts), tx
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, "invalid cursor") from e


def _filters(date_from, date_to, merchant_category, status, min_amount, max_days):
    if date_to < date_from:
        raise HTTPException(400, "date_to must be >= date_from")
    if (date_to - date_from).days + 1 > max_days:
        raise HTTPException(400, f"date range limited to {max_days} days (protects the engine; use /v1/exports for more)")
    # ts range on the partition column -> Iceberg prunes all other days' files
    where = ["ts >= ?", "ts < ?"]
    params: list = [datetime.combine(date_from, time.min), datetime.combine(date_to + timedelta(days=1), time.min)]
    if merchant_category:
        where.append("merchant_category = ?"); params.append(merchant_category)
    if status:
        where.append("status = ?"); params.append(status)
    if min_amount is not None:
        where.append("amount >= CAST(? AS DECIMAL(12,2))"); params.append(str(min_amount))
    return where, params


# ---------- endpoints ----------
@app.get("/health", tags=["ops"])
def health():
    checks = {}
    for name, fn in {
        "trino": lambda: query("SELECT 1"),
        "redis": lambda: redis_client().ping(),
        "object_storage": lambda: s3.head_bucket(Bucket=settings.exports_bucket),
    }.items():
        try:
            fn(); checks[name] = "ok"
        except Exception as e:  # noqa: BLE001
            checks[name] = f"error: {type(e).__name__}"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)


@app.get("/v1/transactions", tags=["query"])
def list_transactions(
    date_from: date,
    date_to: date,
    merchant_category: Category | None = None,
    status: Status | None = None,
    min_amount: Decimal | None = Query(None, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    cursor: str | None = None,
    p: Principal = Depends(require_api_key),
):
    """Filtered read with keyset (cursor) pagination, newest first.

    Unlike OFFSET, the cursor (last ts + transaction_id) lets the engine seek directly
    to the next page, so page 10,000 costs the same as page 1."""
    where, params = _filters(date_from, date_to, merchant_category, status, min_amount, settings.max_range_days)
    if cursor:
        c_ts, c_id = decode_cursor(cursor)
        where.append("(ts < ? OR (ts = ? AND transaction_id < ?))")
        params += [c_ts, c_ts, c_id]
    sql = (f"SELECT {select_list(p)} FROM {settings.fq_table} WHERE {' AND '.join(where)} "
           f"ORDER BY ts DESC, transaction_id DESC LIMIT {limit + 1}")
    cols, rows = query(sql, params)
    data = _rows(cols, rows[:limit])
    next_cursor = encode_cursor(data[-1]["ts"], data[-1]["transaction_id"]) if len(rows) > limit else None
    return Response(_dump({"data": data, "count": len(data), "next_cursor": next_cursor}), media_type="application/json")


@app.get("/v1/customers/{customer_id}/transactions", tags=["query"])
def customer_transactions(
    customer_id: int,
    response: Response,
    limit: int = Query(50, ge=1, le=500),
    cache: bool = Query(True, description="false = always hit Trino (used by the load test to compare)"),
    p: Principal = Depends(require_api_key),
):
    """Point lookup: latest transactions of one customer. Cache-aside with Redis.

    Point lookups are the high-TPS pattern: they must not pay Trino's per-query
    planning/scheduling cost every time, so results are cached per (customer, role, limit)."""
    key = f"cust:{customer_id}:{p.role}:{limit}"
    r = redis_client()
    if cache:
        hit = r.get(key)
        if hit is not None:
            return Response(hit, media_type="application/json", headers={"X-Cache": "HIT"})
    sql = (f"SELECT {select_list(p)} FROM {settings.fq_table} WHERE customer_id = ? "
           f"ORDER BY ts DESC LIMIT {limit}")
    cols, rows = query(sql, [customer_id])
    body = _dump({"customer_id": customer_id, "data": _rows(cols, rows), "count": len(rows)})
    if cache:
        r.set(key, body, ex=settings.cache_ttl)
    return Response(body, media_type="application/json", headers={"X-Cache": "MISS" if cache else "BYPASS"})


class ExportRequest(BaseModel):
    date_from: date
    date_to: date
    merchant_category: Category | None = None
    status: Status | None = None
    min_amount: Decimal | None = Field(None, ge=0)

    @model_validator(mode="after")
    def _range(self):
        if self.date_to < self.date_from:
            raise ValueError("date_to must be >= date_from")
        return self


@app.post("/v1/exports", status_code=202, tags=["export"])
def create_export(req: ExportRequest, p: Principal = Depends(require_api_key)):
    """Start an async bulk export. Poll GET /v1/exports/{job_id} for the download link."""
    where, params = _filters(req.date_from, req.date_to, req.merchant_category, req.status,
                             req.min_amount, settings.max_export_days)
    sql = f"SELECT {select_list(p)} FROM {settings.fq_table} WHERE {' AND '.join(where)}"
    job = exports.submit(sql, params, COLUMN_NAMES, owner=p.key_id, request=req.model_dump(mode="json"))
    return {"job_id": job["job_id"], "status": job["status"], "status_url": f"/v1/exports/{job['job_id']}"}


@app.get("/v1/exports/{job_id}", tags=["export"])
def get_export(job_id: str, p: Principal = Depends(require_api_key)):
    job = exports.get_job(job_id)
    if not job or (job["owner"] != p.key_id and p.role != "admin"):
        raise HTTPException(404, "job not found")
    out = {k: v for k, v in job.items() if k != "owner"}
    if job["status"] == "succeeded":
        out["download_url"] = presign(job["object_key"])  # fresh short-lived link on every poll
        out["download_expires_in_s"] = settings.presign_seconds
    return out
