"""API-key auth + role-based column masking.

Keys come from the API_KEYS env var ("key:role,..."). Only a short SHA-256 fingerprint
of the key is ever logged. Swap this for OAuth2/OIDC (client credentials) in production.
"""
import hashlib
from dataclasses import dataclass

from fastapi import Header, HTTPException, status

from .config import settings


@dataclass(frozen=True)
class Principal:
    key_id: str
    role: str

    @property
    def can_see_pii(self) -> bool:
        return self.role == "admin"


def fingerprint(api_key: str | None) -> str | None:
    return hashlib.sha256(api_key.encode()).hexdigest()[:12] if api_key else None


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> Principal:
    role = settings.api_keys.get(x_api_key or "")
    if not role:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid X-API-Key")
    return Principal(key_id=fingerprint(x_api_key), role=role)


# Masking happens INSIDE the SQL, so clear-text PII never leaves the query engine
# for callers without the right role.
PUBLIC_COLUMNS = [
    "transaction_id", "ts", "customer_id", "merchant_id", "merchant_name",
    "merchant_category", "city", "state", "amount", "currency", "channel", "status",
]
MASKED = {
    "card_number": "concat('**** **** **** ', substr(card_number, -4)) AS card_number",
    "customer_cpf": "regexp_replace(customer_cpf, '^\\d{3}\\.\\d{3}\\.\\d{3}', '***.***.***') AS customer_cpf",
}


def select_list(p: Principal) -> str:
    sensitive = ["card_number", "customer_cpf"] if p.can_see_pii else list(MASKED.values())
    return ", ".join(PUBLIC_COLUMNS + sensitive)


COLUMN_NAMES = PUBLIC_COLUMNS + ["card_number", "customer_cpf"]
