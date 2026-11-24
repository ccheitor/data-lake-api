# Contributing

Issues and pull requests are welcome. The useful change is one that keeps the laptop path working: `make up`, `make seed`, `make test`.

## Layout

| Path | Contents |
|---|---|
| `api/app/` | FastAPI service: routes, auth, masking, exports |
| `api/seed/generate.py` | Synthetic data and the Iceberg table definition |
| `docker-compose.yml` | Storage, catalog, Trino, Redis, API, seed profile |
| `trino/` | Coordinator config and the Iceberg catalog file |
| `loadtest/` | k6, Locust, and the checked-in results |
| `scripts/` | Health wait and the smoke test |
| `docs/` | Guides. Start at [docs/README.md](docs/README.md) |

The HTTP contract is [docs/api-reference.md](docs/api-reference.md). If you change a parameter, status code, or response field, update that page and re-check `GET /docs` against it. The OpenAPI schema is generated; several responses are still documented by hand because the handlers return a raw JSON response.

## Local check

Docker is required for the end-to-end test. From a clean checkout:

```bash
make up
make seed ROWS=20000 DAYS=30
make test
```

`make seed` drops the table. Use a small `ROWS` while iterating.

Without Docker you can still compile and lint the Python:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r api/requirements.txt ruff pyflakes
ruff check --select F api loadtest
pyflakes api loadtest
python -m compileall -q api loadtest
docker compose --profile tools config --quiet
```

`docker compose config` needs the Compose CLI. It does not start containers.

Do not commit `.env`, Parquet files, or `loadtest/results/*.json`. `.env.example` is the template.

## Scope

Keep secrets out of the git history. The sample keys in `.env.example` are fake and local. Do not add real card numbers, real CPFs, or production endpoints.

Match the existing style in the file you touch. Commit messages follow a short conventional prefix: `feat:`, `fix:`, `docs:`, `test:`, `chore:`.

Docs are English, except the Portuguese summary on the front [README](README.md). New behavior needs a paragraph in the relevant guide under `docs/`, not only a comment.
