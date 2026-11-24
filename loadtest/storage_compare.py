#!/usr/bin/env python3
"""Compare on-disk size of the same synthetic transactions in file-exchange
formats vs Parquet / Iceberg (the lake).

Reuses the generator in api/seed/generate.py (same schema, pools, Zipf
customers, log-normal amounts, sort order, seed). Writes each format in
streaming batches so a 10M-row run stays within a few GB of RAM.

Usage (from the repo root, in a venv with faker, numpy, pyarrow, pyiceberg,
duckdb):

    python loadtest/storage_compare.py --rows 2000000 --rows-large 10000000
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import platform
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

from seed.generate import (  # noqa: E402
    ARROW_SCHEMA,
    SCHEMA,
    PARTITION_SPEC,
    SORT_ORDER,
    build_pools,
    make_batch,
)

# ---------------------------------------------------------------------------
# Fixed-width / positional layout (CNAB-style: one record per line).
# Widths are in Unicode characters; the file is UTF-8 (accents in names/cities
# may use more than one byte). Numeric fields are zero-padded; text is
# space-padded on the right and truncated if needed.
# ---------------------------------------------------------------------------
FIELD_SPEC: list[tuple[str, int, str]] = [
    ("transaction_id", 15, "L"),
    ("ts", 26, "L"),
    ("customer_id", 12, "R0"),
    ("customer_cpf", 14, "L"),
    ("card_number", 19, "L"),
    ("merchant_id", 12, "R0"),
    ("merchant_name", 80, "L"),
    ("merchant_category", 16, "L"),
    ("city", 40, "L"),
    ("state", 2, "L"),
    ("amount", 15, "R0"),
    ("currency", 3, "L"),
    ("channel", 12, "L"),
    ("status", 10, "L"),
]
RECORD_CHARS = sum(width for _, width, _ in FIELD_SPEC)  # plus "\n" per row

CSV_COLUMNS = [name for name, *_ in [
    ("transaction_id",), ("ts",), ("customer_id",), ("customer_cpf",),
    ("card_number",), ("merchant_id",), ("merchant_name",),
    ("merchant_category",), ("city",), ("state",), ("amount",),
    ("currency",), ("channel",), ("status",),
]]

# GNU gzip's default is -6; Python's gzip default is 9. Use 6 to match the
# command people actually run on an SFTP drop ("gzip extract.csv").
GZIP_LEVEL = 6


def utcnow() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def dir_bytes(path: Path) -> dict:
    total = 0
    n_files = 0
    by_kind = {"parquet_data": 0, "metadata": 0, "other": 0}
    n_parquet = 0
    for p in path.rglob("*"):
        if not p.is_file():
            continue
        size = p.stat().st_size
        total += size
        n_files += 1
        suffix = p.suffix.lower()
        if suffix == ".parquet":
            by_kind["parquet_data"] += size
            n_parquet += 1
        elif suffix in {".json", ".avro", ".db", ".sql"} or "metadata" in p.parts:
            by_kind["metadata"] += size
        else:
            by_kind["other"] += size
    return {
        "bytes": total,
        "files": n_files,
        "parquet_files": n_parquet,
        "parquet_data_bytes": by_kind["parquet_data"],
        "metadata_bytes": by_kind["metadata"],
        "other_bytes": by_kind["other"],
    }


def pad_field(value: str, width: int, align: str) -> str:
    if len(value) > width:
        return value[:width]
    if align == "R0":
        return value.rjust(width, "0")
    if align == "R":
        return value.rjust(width, " ")
    return value.ljust(width, " ")



def table_to_text_rows(table: pa.Table) -> list[dict[str, str]]:
    """Convert a batch to plain-text records once, as Python lists (much faster
    than calling as_py() per cell)."""
    ts_fmt = []
    for ts in table["ts"].to_pylist():
        ts_fmt.append(ts.strftime("%Y-%m-%dT%H:%M:%S.%f") if ts is not None else "")
    amounts = []
    for amount in table["amount"].to_pylist():
        amounts.append(f"{amount:.2f}" if amount is not None else "")
    cols = {
        "transaction_id": table["transaction_id"].to_pylist(),
        "ts": ts_fmt,
        "customer_id": [str(v) for v in table["customer_id"].to_pylist()],
        "customer_cpf": table["customer_cpf"].to_pylist(),
        "card_number": table["card_number"].to_pylist(),
        "merchant_id": [str(v) for v in table["merchant_id"].to_pylist()],
        "merchant_name": [v or "" for v in table["merchant_name"].to_pylist()],
        "merchant_category": table["merchant_category"].to_pylist(),
        "city": [v or "" for v in table["city"].to_pylist()],
        "state": [v or "" for v in table["state"].to_pylist()],
        "amount": amounts,
        "currency": table["currency"].to_pylist(),
        "channel": table["channel"].to_pylist(),
        "status": table["status"].to_pylist(),
    }
    n = table.num_rows
    return [{k: cols[k][i] for k in cols} for i in range(n)]


def format_row_text(table: pa.Table, i: int) -> dict[str, str]:
    ts = table["ts"][i].as_py()
    amount = table["amount"][i].as_py()
    return {
        "transaction_id": table["transaction_id"][i].as_py(),
        "ts": ts.strftime("%Y-%m-%dT%H:%M:%S.%f") if ts is not None else "",
        "customer_id": str(table["customer_id"][i].as_py()),
        "customer_cpf": table["customer_cpf"][i].as_py(),
        "card_number": table["card_number"][i].as_py(),
        "merchant_id": str(table["merchant_id"][i].as_py()),
        "merchant_name": table["merchant_name"][i].as_py() or "",
        "merchant_category": table["merchant_category"][i].as_py(),
        "city": table["city"][i].as_py() or "",
        "state": table["state"][i].as_py() or "",
        "amount": f"{amount:.2f}" if amount is not None else "",
        "currency": table["currency"][i].as_py(),
        "channel": table["channel"][i].as_py(),
        "status": table["status"][i].as_py(),
    }


def write_fixed_width(fh, rows: list[dict[str, str]]) -> None:
    for row in rows:
        rec = "".join(pad_field(row[name], width, align) for name, width, align in FIELD_SPEC)
        fh.write(rec + "\n")


def parquet_column_compressed_bytes(files: list[Path], columns: list[str] | None = None) -> int:
    """Sum total_compressed_size of column chunks (what a column-projection
    scan actually has to read, ignoring footer/magic which is tiny)."""
    total = 0
    wanted = set(columns) if columns is not None else None
    for path in files:
        pf = pq.ParquetFile(path)
        for i in range(pf.num_row_groups):
            rg = pf.metadata.row_group(i)
            for j in range(rg.num_columns):
                col = rg.column(j)
                name = col.path_in_schema
                if wanted is None or name in wanted:
                    total += col.total_compressed_size
    return total


def list_parquet_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*.parquet") if p.is_file())


def iceberg_partition_bytes(data_root: Path, days: list[str]) -> dict:
    """Sum Parquet files whose path contains a day partition value.

    PyIceberg/Hive-style paths look like .../ts_day=2026-11-01/... or
    include the date in the partition directory name.
    """
    wanted = set(days)
    total = 0
    n = 0
    matched_days: set[str] = set()
    for p in data_root.rglob("*.parquet"):
        parts = p.parts
        day = None
        for part in parts:
            if part.startswith("ts_day="):
                day = part.split("=", 1)[1]
                break
        if day is None:
            # fallback: any YYYY-MM-DD in the path
            for part in parts:
                if len(part) >= 10 and part[:10] in wanted:
                    day = part[:10]
                    break
        if day in wanted:
            total += p.stat().st_size
            n += 1
            matched_days.add(day)
    return {"bytes": total, "files": n, "days_found": sorted(matched_days)}


def hardware_info() -> dict:
    mem_kb = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                mem_kb = int(line.split()[1])
                break
    except OSError:
        pass
    cpu = None
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
        cores = os.cpu_count()
    except OSError:
        cores = os.cpu_count()
    return {
        "hostname": platform.node(),
        "system": platform.system(),
        "machine": platform.machine(),
        "cpu": cpu,
        "cores": cores,
        "ram_bytes": mem_kb * 1024 if mem_kb else None,
        "python": sys.version.split()[0],
        "pyarrow": getattr(pa, "__version__", None),
        "duckdb": getattr(duckdb, "__version__", None),
    }


def open_iceberg_table(warehouse: Path, replace: bool = True):
    from pyiceberg.catalog.sql import SqlCatalog
    from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError

    warehouse.mkdir(parents=True, exist_ok=True)
    catalog = SqlCatalog(
        "local",
        **{
            "uri": f"sqlite:///{warehouse / 'catalog.db'}",
            "warehouse": f"file://{warehouse}",
        },
    )
    try:
        catalog.create_namespace("payments")
    except NamespaceAlreadyExistsError:
        pass
    ident = "payments.transactions"
    if replace:
        try:
            catalog.drop_table(ident)
        except NoSuchTableError:
            pass
    table = catalog.create_table(
        ident,
        schema=SCHEMA,
        partition_spec=PARTITION_SPEC,
        sort_order=SORT_ORDER,
        properties={"write.parquet.compression-codec": "zstd", "format-version": "2"},
    )
    return table


def duckdb_projection_check(parquet_path: Path) -> dict:
    """Run a 2-column aggregation in DuckDB as a sanity check (row count).

    Bytes-read come from Parquet footers (see parquet_column_compressed_bytes);
    DuckDB 2.x does not reliably expose physical bytes read in a stable API.
    """
    con = duckdb.connect()
    start = time.time()
    row = con.execute(
        "SELECT count(*) AS n, sum(CAST(amount AS DOUBLE)) AS s "
        "FROM read_parquet(?) ",
        [str(parquet_path)],
    ).fetchone()
    full_s = time.time() - start
    start = time.time()
    row2 = con.execute(
        "SELECT count(*) AS n, sum(CAST(amount AS DOUBLE)) AS s "
        "FROM (SELECT customer_id, amount FROM read_parquet(?)) ",
        [str(parquet_path)],
    ).fetchone()
    proj_s = time.time() - start
    con.close()
    return {
        "full_scan_rows": int(row[0]),
        "full_scan_seconds": round(full_s, 4),
        "projection_rows": int(row2[0]),
        "projection_seconds": round(proj_s, 4),
    }


def human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    units = ["KiB", "MiB", "GiB", "TiB"]
    x = float(n)
    for u in units:
        x /= 1024.0
        if x < 1024 or u == units[-1]:
            if x >= 100:
                return f"{x:.1f} {u}"
            if x >= 10:
                return f"{x:.2f} {u}"
            return f"{x:.3f} {u}"
    return f"{n} B"


def run_one(rows: int, args, out_root: Path) -> dict:
    run_dir = out_root / f"rows-{rows}"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)

    csv_path = run_dir / "transactions.csv"
    gzip_path = run_dir / "transactions.csv.gz"
    fixed_path = run_dir / "transactions.fixed.txt"
    jsonl_path = run_dir / "transactions.jsonl"
    parquet_path = run_dir / "transactions.parquet"
    iceberg_dir = run_dir / "iceberg-warehouse"

    from faker import Faker
    import numpy as np

    rng = np.random.default_rng(args.seed)
    fake = Faker("pt_BR")
    Faker.seed(args.seed)
    pools = build_pools(fake, rng, args.customers, args.merchants)
    # Fixed calendar window so the run is reproducible (the project generator
    # uses datetime.now(); we pin the end date instead).
    end = datetime.strptime(args.end_date, "%Y-%m-%d")
    start = (end - timedelta(days=args.days)).replace(hour=0, minute=0, second=0, microsecond=0)

    iceberg_table = open_iceberg_table(iceberg_dir, replace=True)

    csv_f = csv_path.open("w", newline="", encoding="utf-8")
    csv_w = csv.writer(csv_f, lineterminator="\n")
    csv_w.writerow(CSV_COLUMNS)
    gz_raw = gzip.open(gzip_path, "wt", encoding="utf-8", newline="", compresslevel=GZIP_LEVEL)
    gz_w = csv.writer(gz_raw, lineterminator="\n")
    gz_w.writerow(CSV_COLUMNS)
    fixed_f = fixed_path.open("w", encoding="utf-8", newline="\n")
    jsonl_f = jsonl_path.open("w", encoding="utf-8")
    pq_writer = None

    truncated = {name: 0 for name, _, _ in FIELD_SPEC}
    done = 0
    t0 = time.time()
    id_offset = 0
    while done < rows:
        n = min(args.batch, rows - done)
        table = make_batch(rng, n, start, args.days, pools, id_offset + done)
        # text formats
        text_rows = table_to_text_rows(table)
        for row in text_rows:
            for name, width, _ in FIELD_SPEC:
                if len(row[name]) > width:
                    truncated[name] += 1
            csv_w.writerow([row[c] for c in CSV_COLUMNS])
            gz_w.writerow([row[c] for c in CSV_COLUMNS])
            jsonl_f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        write_fixed_width(fixed_f, text_rows)
        # parquet (single file, zstd — same codec as the lake)
        if pq_writer is None:
            pq_writer = pq.ParquetWriter(
                parquet_path,
                ARROW_SCHEMA,
                compression="zstd",
                use_dictionary=True,
                write_statistics=True,
            )
        pq_writer.write_table(table)
        iceberg_table.append(table)
        done += n
        print(f"  [{rows:,}] {done:,}/{rows:,} rows ({done / (time.time() - t0):,.0f} rows/s)", flush=True)

    csv_f.close()
    gz_raw.close()
    fixed_f.close()
    jsonl_f.close()
    if pq_writer is not None:
        pq_writer.close()

    elapsed = time.time() - t0

    sizes = {}
    for key, path in [
        ("csv", csv_path),
        ("csv_gzip", gzip_path),
        ("fixed_width_txt", fixed_path),
        ("jsonl", jsonl_path),
        ("parquet_zstd", parquet_path),
    ]:
        b = path.stat().st_size
        sizes[key] = {
            "path": str(path),
            "bytes": b,
            "human": human_bytes(b),
            "bytes_per_row": b / rows,
        }

    ice = dir_bytes(iceberg_dir)
    sizes["iceberg_table"] = {
        "path": str(iceberg_dir),
        "bytes": ice["bytes"],
        "human": human_bytes(ice["bytes"]),
        "bytes_per_row": ice["bytes"] / rows,
        "files": ice["files"],
        "parquet_files": ice["parquet_files"],
        "parquet_data_bytes": ice["parquet_data_bytes"],
        "parquet_data_human": human_bytes(ice["parquet_data_bytes"]),
        "metadata_bytes": ice["metadata_bytes"],
        "metadata_human": human_bytes(ice["metadata_bytes"]),
        "other_bytes": ice["other_bytes"],
    }

    pq_files = list_parquet_files(parquet_path)
    ice_files = list_parquet_files(iceberg_dir)
    two_cols = ["customer_id", "amount"]
    scan = {
        "parquet_single_file": {
            "all_columns_compressed_bytes": parquet_column_compressed_bytes(pq_files, None),
            "two_columns_compressed_bytes": parquet_column_compressed_bytes(pq_files, two_cols),
            "two_columns": two_cols,
        },
        "iceberg_data_files": {
            "all_columns_compressed_bytes": parquet_column_compressed_bytes(ice_files, None) if ice_files else None,
            "two_columns_compressed_bytes": parquet_column_compressed_bytes(ice_files, two_cols) if ice_files else None,
            "two_columns": two_cols,
        },
    }
    # 7-day window vs 90-day full table (partition pruning), aligned with the
    # API's paginated query which requires a date range capped at 31 days.
    # Timestamps fall in [end - days, end), so the last populated day is end-1.
    last_day = (end - timedelta(days=1)).date()
    week = [(last_day - timedelta(days=i)).isoformat() for i in range(7)]
    scan["iceberg_partition_pruning_7_of_90_days"] = {
        "days": week,
        **iceberg_partition_bytes(iceberg_dir, week),
        "full_parquet_data_bytes": ice["parquet_data_bytes"],
    }
    try:
        scan["duckdb_sanity"] = duckdb_projection_check(parquet_path)
    except Exception as exc:  # noqa: BLE001 — report, don't fail the run
        scan["duckdb_sanity"] = {"error": str(exc)}

    for side in ("parquet_single_file", "iceberg_data_files"):
        block = scan[side]
        all_b = block.get("all_columns_compressed_bytes") or 0
        two_b = block.get("two_columns_compressed_bytes") or 0
        block["two_column_fraction"] = (two_b / all_b) if all_b else None

    prune = scan["iceberg_partition_pruning_7_of_90_days"]
    full_b = prune.get("full_parquet_data_bytes") or 0
    prune["fraction_of_data_files"] = (prune["bytes"] / full_b) if full_b else None

    result = {
        "rows": rows,
        "days": args.days,
        "customers": args.customers,
        "merchants": args.merchants,
        "batch": args.batch,
        "seed": args.seed,
        "end_date": args.end_date,
        "gzip_level": GZIP_LEVEL,
        "parquet_compression": "zstd",
        "fixed_width": {
            "fields": [
                {"name": n, "width": w, "align": a} for n, w, a in FIELD_SPEC
            ],
            "record_chars_without_newline": RECORD_CHARS,
            "truncated_values": {k: v for k, v in truncated.items() if v},
        },
        "elapsed_seconds": round(elapsed, 2),
        "sizes": sizes,
        "scan": scan,
    }
    (run_dir / "result.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    return result


# Official S3 Standard (us-east-1) first-tier price, filled in by the operator
# and verified against the Price List API when STORAGE.md is written.
S3_STANDARD_USD_PER_GB_MONTH = 0.023
S3_PRICING_PAGE = "https://aws.amazon.com/s3/pricing/"
S3_PRICE_LIST = (
    "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/"
    "20261121172919/us-east-1/index.json"
)
S3_PRICE_LIST_VERSION = "20261121172919"
S3_PRICE_LIST_PUBLISHED = "2026-11-21T17:29:19Z"
S3_SKU = "WP9ANXZGBYYSGJEA"  # TimedStorage-ByteHrs, volumeType=Standard
GIB = 1024 ** 3
TIB = 1024 ** 4


def project(sizes: dict, rows: int) -> dict:
    """Linear projections. Clearly not a measurement."""
    def from_bpr(bytes_per_row: float, n: int) -> int:
        return int(round(bytes_per_row * n))

    one_billion = 1_000_000_000
    proj_1b = {
        name: {
            "bytes": from_bpr(info["bytes_per_row"], one_billion),
            "human": human_bytes(from_bpr(info["bytes_per_row"], one_billion)),
        }
        for name, info in sizes.items()
    }
    # 1 TiB of uncompressed CSV (AWS definition: 1 TB = 1024 GB = 2^40 bytes)
    csv_bpr = sizes["csv"]["bytes_per_row"]
    rows_in_1tib_csv = TIB / csv_bpr
    proj_1tib_csv = {
        "rows_equivalent": rows_in_1tib_csv,
        "formats": {
            name: {
                "bytes": from_bpr(info["bytes_per_row"], rows_in_1tib_csv),
                "human": human_bytes(from_bpr(info["bytes_per_row"], rows_in_1tib_csv)),
            }
            for name, info in sizes.items()
        },
    }

    def monthly_cost(n_bytes: float) -> float:
        gb = n_bytes / (1024 ** 3)  # AWS binary GB
        return gb * S3_STANDARD_USD_PER_GB_MONTH

    costs_1tib = {
        name: {
            "usd_per_month": monthly_cost(info["bytes"]),
        }
        for name, info in proj_1tib_csv["formats"].items()
    }
    costs_1b = {
        name: {"usd_per_month": monthly_cost(info["bytes"])}
        for name, info in proj_1b.items()
    }
    return {
        "method": "linear: bytes_per_row * N, from the measured run",
        "one_billion_rows": proj_1b,
        "one_billion_rows_s3_standard_usd_per_month": costs_1b,
        "one_tib_uncompressed_csv": proj_1tib_csv,
        "one_tib_uncompressed_csv_s3_standard_usd_per_month": costs_1tib,
        "s3_price": {
            "usd_per_gb_month": S3_STANDARD_USD_PER_GB_MONTH,
            "tier": "first 50 TB / month, S3 Standard, US East (N. Virginia)",
            "gb_definition": "1 GB = 2^30 bytes, 1 TB = 1024 GB (AWS S3 pricing page)",
            "pricing_page": S3_PRICING_PAGE,
            "price_list_url": S3_PRICE_LIST,
            "price_list_version": S3_PRICE_LIST_VERSION,
            "price_list_publication_date": S3_PRICE_LIST_PUBLISHED,
            "sku": S3_SKU,
            "rate_description": "$0.023 per GB - first 50 TB / month of storage used",
            "excludes": "requests, data transfer, tax, and any other storage class",
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", type=int, default=2_000_000, help="primary measured size (default: 2M, same as the load test)")
    ap.add_argument("--rows-large", type=int, default=0, help="optional second run (e.g. 10000000). 0 skips it.")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--customers", type=int, default=50_000)
    ap.add_argument("--merchants", type=int, default=5_000)
    ap.add_argument("--batch", type=int, default=250_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--end-date", default="2026-11-24", help="inclusive end of the 90-day window (YYYY-MM-DD)")
    ap.add_argument("--out", default="/tmp/storage-compare", help="directory for the bulky generated files")
    args = ap.parse_args()

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"start {utcnow()}  hardware: {hardware_info()}", flush=True)
    runs = {}
    print(f"\n=== measuring {args.rows:,} rows ===", flush=True)
    runs[str(args.rows)] = run_one(args.rows, args, out_root)
    if args.rows_large and args.rows_large != args.rows:
        print(f"\n=== measuring {args.rows_large:,} rows ===", flush=True)
        try:
            runs[str(args.rows_large)] = run_one(args.rows_large, args, out_root)
        except Exception as exc:  # noqa: BLE001
            print(f"large run failed: {exc!r}", flush=True)
            runs[str(args.rows_large)] = {"error": repr(exc)}

    primary = runs[str(args.rows)]
    summary = {
        "generated_at": utcnow(),
        "hardware": hardware_info(),
        "runs": runs,
        "projection_from_primary_run": project(primary["sizes"], primary["rows"]),
        "caveats": [
            "Compression ratios were measured on synthetic Faker/NumPy data (pt_BR names, CPFs, card numbers, categories). Real production data has different entropy and will compress differently — treat the ratios as order-of-magnitude, not a guarantee.",
            "CSV uses Python's csv module (Excel-compatible quoting). gzip uses compresslevel=6, the GNU gzip default.",
            "The positional TXT layout is a documented CNAB-style record invented for this comparison, not a specific bank's official layout.",
            "The single Parquet file is zstd + dictionary + statistics, written with PyArrow. The Iceberg table uses the same schema, day(ts) partition, (customer_id, ts) sort order and write.parquet.compression-codec=zstd as api/seed/generate.py. Iceberg size includes data files, manifests, metadata JSON and the local SQLite catalog.",
            "Projections (1 billion rows, 1 TiB of raw CSV) are linear (bytes per row × N) and are not measurements.",
            "S3 costs are storage only, at the public S3 Standard list price for us-east-1, first 50 TB tier. They exclude requests, transfer, tax, and discounts.",
        ],
    }
    summary_path = out_root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str) + "\n")
    print(f"\nwrote {summary_path}", flush=True)
    # also drop a copy next to this script for the human-readable report step
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
