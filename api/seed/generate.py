"""Generate fake card transactions and load them into an Apache Iceberg table.

Writes straight to Iceberg with PyIceberg + PyArrow (no Spark needed), so it runs
on a laptop or a small Arm VM. Faker builds realistic pools (customers, merchants);
NumPy samples from them so millions of rows are generated in seconds, not hours.

Usage (inside the api container):
    python -m seed.generate --rows 1000000 --days 90 --customers 50000
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timedelta
from decimal import Decimal

import numpy as np
import pyarrow as pa
from faker import Faker
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table.sorting import SortField, SortOrder
from pyiceberg.transforms import DayTransform, IdentityTransform
from pyiceberg.types import (
    DecimalType, LongType, NestedField, StringType, TimestampType,
)

NAMESPACE = os.getenv("TRINO_SCHEMA", "payments")
TABLE = "transactions"

SCHEMA = Schema(
    NestedField(1, "transaction_id", StringType(), required=True),
    NestedField(2, "ts", TimestampType(), required=True),
    NestedField(3, "customer_id", LongType(), required=True),
    NestedField(4, "customer_cpf", StringType(), required=True),   # sensitive -> masked by API
    NestedField(5, "card_number", StringType(), required=True),    # sensitive -> masked by API
    NestedField(6, "merchant_id", LongType(), required=True),
    NestedField(7, "merchant_name", StringType(), required=True),
    NestedField(8, "merchant_category", StringType(), required=True),
    NestedField(9, "city", StringType(), required=True),
    NestedField(10, "state", StringType(), required=True),
    NestedField(11, "amount", DecimalType(12, 2), required=True),
    NestedField(12, "currency", StringType(), required=True),
    NestedField(13, "channel", StringType(), required=True),
    NestedField(14, "status", StringType(), required=True),
)

# Partition by day(ts): date-range queries only open the files of the requested days.
PARTITION_SPEC = PartitionSpec(
    PartitionField(source_id=2, field_id=1000, transform=DayTransform(), name="ts_day")
)
SORT_ORDER = SortOrder(
    SortField(source_id=3, transform=IdentityTransform()),
    SortField(source_id=2, transform=IdentityTransform()),
)

ARROW_SCHEMA = pa.schema([
    pa.field("transaction_id", pa.string(), nullable=False),
    pa.field("ts", pa.timestamp("us"), nullable=False),
    pa.field("customer_id", pa.int64(), nullable=False),
    pa.field("customer_cpf", pa.string(), nullable=False),
    pa.field("card_number", pa.string(), nullable=False),
    pa.field("merchant_id", pa.int64(), nullable=False),
    pa.field("merchant_name", pa.string(), nullable=False),
    pa.field("merchant_category", pa.string(), nullable=False),
    pa.field("city", pa.string(), nullable=False),
    pa.field("state", pa.string(), nullable=False),
    pa.field("amount", pa.decimal128(12, 2), nullable=False),
    pa.field("currency", pa.string(), nullable=False),
    pa.field("channel", pa.string(), nullable=False),
    pa.field("status", pa.string(), nullable=False),
])

CATEGORIES = np.array([
    "grocery", "restaurants", "fuel", "pharmacy", "electronics", "travel",
    "apparel", "entertainment", "utilities", "health", "education", "transport",
])
CHANNELS = np.array(["pos", "ecommerce", "contactless", "recurring"])
CHANNEL_P = [0.45, 0.30, 0.20, 0.05]
STATUSES = np.array(["approved", "declined", "reversed"])
STATUS_P = [0.93, 0.05, 0.02]


def catalog():
    return load_catalog(
        "rest",
        **{
            "type": "rest",
            "uri": os.getenv("ICEBERG_REST_URI", "http://localhost:8181"),
            "warehouse": os.getenv("WAREHOUSE", "s3://warehouse/"),
            "s3.endpoint": os.getenv("S3_ENDPOINT", "http://localhost:9000"),
            "s3.access-key-id": os.getenv("AWS_ACCESS_KEY_ID", "minioadmin"),
            "s3.secret-access-key": os.getenv("AWS_SECRET_ACCESS_KEY", "minioadmin123"),
            "s3.region": os.getenv("AWS_REGION", "us-east-1"),
            "s3.path-style-access": "true",
        },
    )


def build_pools(fake: Faker, rng: np.random.Generator, n_customers: int, n_merchants: int):
    t = time.time()
    # unique() guarantees no duplicate CPFs/cards inside the pool
    cpfs = np.array([fake.unique.cpf() for _ in range(n_customers)])
    cards = np.array([fake.credit_card_number(card_type=None) for _ in range(n_customers)])
    m_names = np.array([fake.company() for _ in range(n_merchants)])
    m_city = np.array([fake.city() for _ in range(n_merchants)])
    m_state = np.array([fake.estado_sigla() for _ in range(n_merchants)])
    m_cat = rng.choice(CATEGORIES, n_merchants)
    # log-normal ticket size per category (fuel/grocery small, travel/electronics large)
    base = {"travel": 6.2, "electronics": 6.0, "education": 5.6, "health": 5.0, "utilities": 5.0}
    m_mu = np.array([base.get(c, 4.0) for c in m_cat])
    print(f"pools: {n_customers:,} customers, {n_merchants:,} merchants in {time.time()-t:.1f}s")
    return cpfs, cards, m_names, m_city, m_state, m_cat, m_mu


def make_batch(rng, n, start, days, pools, id_offset):
    cpfs, cards, m_names, m_city, m_state, m_cat, m_mu = pools
    cust_idx = rng.zipf(1.3, n) % len(cpfs)          # few heavy customers, long tail
    merch_idx = rng.integers(0, len(m_names), n)
    secs = rng.integers(0, days * 86400, n)
    micros = rng.integers(0, 1_000_000, n)
    ts = (np.datetime64(start, "us") + secs.astype("timedelta64[s]") + micros.astype("timedelta64[us]"))
    cents = np.round(rng.lognormal(m_mu[merch_idx], 0.9) * 100).astype(np.int64).clip(100, 5_000_000)
    # Sort by (customer_id, ts) inside each batch: Parquet min/max stats on customer_id
    # then let Trino skip most row groups for "transactions of customer X" lookups.
    order = np.lexsort((ts, cust_idx))
    ids = np.char.add("tx_", np.char.zfill((np.arange(n) + id_offset).astype(str), 12))
    cols = {
        "transaction_id": ids,
        "ts": ts[order],
        "customer_id": (cust_idx + 1)[order],
        "customer_cpf": cpfs[cust_idx][order],
        "card_number": cards[cust_idx][order],
        "merchant_id": (merch_idx + 1)[order],
        "merchant_name": m_names[merch_idx][order],
        "merchant_category": m_cat[merch_idx][order],
        "city": m_city[merch_idx][order],
        "state": m_state[merch_idx][order],
        "amount": pa.array([Decimal(int(c)).scaleb(-2) for c in cents[order]], pa.decimal128(12, 2)),
        "currency": np.full(n, "BRL"),
        "channel": rng.choice(CHANNELS, n, p=CHANNEL_P)[order],
        "status": rng.choice(STATUSES, n, p=STATUS_P)[order],
    }
    return pa.table({k: (v if isinstance(v, pa.Array) else pa.array(v, ARROW_SCHEMA.field(k).type))
                     for k, v in cols.items()}, schema=ARROW_SCHEMA)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", type=int, default=int(os.getenv("SEED_ROWS", 1_000_000)))
    ap.add_argument("--days", type=int, default=90, help="history window ending today")
    ap.add_argument("--customers", type=int, default=50_000)
    ap.add_argument("--merchants", type=int, default=5_000)
    ap.add_argument("--batch", type=int, default=250_000, help="rows per Iceberg commit (memory ~ batch size)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--replace", action="store_true", help="drop and recreate the table first")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    fake = Faker("pt_BR")
    Faker.seed(args.seed)
    cat = catalog()
    try:
        cat.create_namespace(NAMESPACE)
    except NamespaceAlreadyExistsError:
        pass
    ident = f"{NAMESPACE}.{TABLE}"
    if args.replace:
        try:
            cat.drop_table(ident)
            print(f"dropped {ident}")
        except NoSuchTableError:
            pass
    try:
        table = cat.load_table(ident)
    except NoSuchTableError:
        table = cat.create_table(
            ident, schema=SCHEMA, partition_spec=PARTITION_SPEC, sort_order=SORT_ORDER,
            properties={"write.parquet.compression-codec": "zstd", "format-version": "2"},
        )
        print(f"created {ident} partitioned by day(ts)")

    pools = build_pools(fake, rng, args.customers, args.merchants)
    start = (datetime.now() - timedelta(days=args.days)).replace(hour=0, minute=0, second=0, microsecond=0)
    id_offset = int(time.time() * 1000) % 10**11 * 10  # avoid id collisions across runs
    done, t0 = 0, time.time()
    while done < args.rows:
        n = min(args.batch, args.rows - done)
        batch = make_batch(rng, n, start, args.days, pools, id_offset + done)
        table.append(batch)
        done += n
        print(f"  appended {done:,}/{args.rows:,} rows ({done / (time.time() - t0):,.0f} rows/s)")
    print(f"done: {args.rows:,} rows in {time.time() - t0:.1f}s -> {ident}")


if __name__ == "__main__":
    main()
