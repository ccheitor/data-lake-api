import trino

from .config import settings


def connect():
    return trino.dbapi.connect(
        host=settings.trino_host, port=settings.trino_port, user="data-api",
        catalog=settings.trino_catalog, schema=settings.trino_schema,
        http_scheme="http", source="data-lake-api",
    )


def query(sql: str, params: list | None = None) -> tuple[list[str], list[tuple]]:
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params or [])
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return cols, rows
    finally:
        conn.close()
