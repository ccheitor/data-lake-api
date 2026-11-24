import os
from dataclasses import dataclass, field


def _keys(raw: str) -> dict[str, str]:
    out = {}
    for item in filter(None, (x.strip() for x in raw.split(","))):
        key, _, role = item.partition(":")
        out[key] = role or "analyst"
    return out


@dataclass(frozen=True)
class Settings:
    trino_host: str = os.getenv("TRINO_HOST", "localhost")
    trino_port: int = int(os.getenv("TRINO_PORT", "8080"))
    trino_catalog: str = os.getenv("TRINO_CATALOG", "iceberg")
    trino_schema: str = os.getenv("TRINO_SCHEMA", "payments")
    table: str = os.getenv("TABLE_NAME", "transactions")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    cache_ttl: int = int(os.getenv("CACHE_TTL_SECONDS", "300"))
    s3_endpoint: str = os.getenv("S3_ENDPOINT", "http://localhost:9000")
    s3_public_endpoint: str = os.getenv("S3_PUBLIC_ENDPOINT", "http://localhost:9000")
    s3_region: str = os.getenv("AWS_REGION", "us-east-1")
    exports_bucket: str = os.getenv("EXPORTS_BUCKET", "exports")
    presign_seconds: int = int(os.getenv("PRESIGN_SECONDS", "3600"))
    export_workers: int = int(os.getenv("EXPORT_WORKERS", "2"))
    max_range_days: int = int(os.getenv("MAX_RANGE_DAYS", "31"))
    max_export_days: int = int(os.getenv("MAX_EXPORT_DAYS", "366"))
    audit_log_path: str = os.getenv("AUDIT_LOG_PATH", "audit.log")
    api_keys: dict = field(default_factory=lambda: _keys(os.getenv("API_KEYS", "dev-analyst-key:analyst,dev-admin-key:admin")))

    @property
    def fq_table(self) -> str:
        return f"{self.trino_catalog}.{self.trino_schema}.{self.table}"


settings = Settings()
