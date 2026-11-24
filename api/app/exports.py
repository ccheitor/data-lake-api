"""Async bulk export: big query -> streamed Parquet -> object storage -> presigned URL.

The HTTP request only enqueues a job and returns 202 + job_id. A small worker pool
(EXPORT_WORKERS) streams the Trino result in chunks into a Parquet file, so memory
stays flat no matter how many rows are exported, then uploads it to the exports bucket.
Job state lives in Redis, so any API worker can answer GET /v1/exports/{id}.
"""
import json
import logging
import os
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from .cache import redis_client
from .config import settings
from .db import connect
from .storage import s3

log = logging.getLogger("exports")
_pool = ThreadPoolExecutor(max_workers=settings.export_workers, thread_name_prefix="export")
JOB_TTL = 7 * 86400
CHUNK = 50_000

ARROW_TYPES = {
    "ts": pa.timestamp("us"), "customer_id": pa.int64(), "merchant_id": pa.int64(),
    "amount": pa.decimal128(12, 2),
}


def _key(job_id: str) -> str:
    return f"export:{job_id}"


def _save(jid: str, /, **fields):
    r = redis_client()
    cur = json.loads(r.get(_key(jid)) or "{}")
    cur.update(fields)
    r.set(_key(jid), json.dumps(cur, default=str), ex=JOB_TTL)
    return cur


def get_job(job_id: str) -> dict | None:
    raw = redis_client().get(_key(job_id))
    return json.loads(raw) if raw else None


def submit(sql: str, params: list, columns: list[str], owner: str, request: dict) -> dict:
    job_id = uuid.uuid4().hex
    job = _save(job_id, job_id=job_id, status="queued", owner=owner, request=request,
                created_at=datetime.now(timezone.utc).isoformat())
    _pool.submit(_run, job_id, sql, params, columns)
    return job


def _run(job_id: str, sql: str, params: list, columns: list[str]):
    t0 = time.time()
    _save(job_id, status="running", started_at=datetime.now(timezone.utc).isoformat())
    schema = pa.schema([(c, ARROW_TYPES.get(c, pa.string())) for c in columns])
    fd, path = tempfile.mkstemp(suffix=".parquet")
    os.close(fd)
    rows = 0
    try:
        conn = connect()
        cur = conn.cursor()
        cur.execute(sql, params)
        with pq.ParquetWriter(path, schema, compression="zstd") as writer:
            while True:
                chunk = cur.fetchmany(CHUNK)
                if not chunk:
                    break
                cols = list(zip(*chunk))
                writer.write_table(pa.table([pa.array(c, schema.field(i).type) for i, c in enumerate(cols)], schema=schema))
                rows += len(chunk)
                _save(job_id, rows_written=rows)
        conn.close()
        key = f"{datetime.now(timezone.utc):%Y/%m/%d}/{job_id}.parquet"
        s3.upload_file(path, settings.exports_bucket, key)
        _save(job_id, status="succeeded", rows_written=rows, object_key=key,
              size_bytes=os.path.getsize(path), duration_s=round(time.time() - t0, 2),
              finished_at=datetime.now(timezone.utc).isoformat())
    except Exception as e:  # noqa: BLE001 - surface any failure in the job status
        log.exception("export %s failed", job_id)
        _save(job_id, status="failed", error=str(e)[:500], finished_at=datetime.now(timezone.utc).isoformat())
    finally:
        if os.path.exists(path):
            os.remove(path)
