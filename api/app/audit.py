"""Audit trail: one JSON line per request (who, what, when, result, latency)."""
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .config import settings
from .security import fingerprint

audit_logger = logging.getLogger("audit")
audit_logger.setLevel(logging.INFO)
audit_logger.propagate = False
_fmt = logging.Formatter("%(message)s")
for h in (logging.StreamHandler(), logging.FileHandler(settings.audit_log_path)):
    h.setFormatter(_fmt)
    audit_logger.addHandler(h)


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        t0 = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            audit_logger.info(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "request_id": rid,
                "key_id": fingerprint(request.headers.get("X-API-Key")),
                "client": request.client.host if request.client else None,
                "method": request.method,
                "path": request.url.path,
                "query": str(request.url.query) or None,
                "status": status_code,
                "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            }))
