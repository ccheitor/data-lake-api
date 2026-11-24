[Português](#portugues) | [English](#english)

<a id="portugues"></a>

# Como contribuir

Issues e pull requests são bem-vindos. A mudança útil é a que mantém o caminho do laptop funcionando: `make up`, `make seed`, `make test`.

## Organização

| Caminho | Conteúdo |
|---|---|
| `api/app/` | Serviço FastAPI: rotas, autenticação, mascaramento, exportações |
| `api/seed/generate.py` | Dado sintético e a definição da tabela Iceberg |
| `docker-compose.yml` | Armazenamento, catálogo, Trino, Redis, API, perfil de seed |
| `trino/` | Configuração do coordinator e o arquivo de catálogo Iceberg |
| `loadtest/` | k6, Locust e os resultados guardados no repositório |
| `scripts/` | Espera de saúde e o teste de fumaça |
| `docs/` | Guias. Comece em [docs/README.md](docs/README.md) |

O contrato HTTP em português está em [docs/pt-BR/api-reference.md](docs/pt-BR/api-reference.md). O mesmo contrato em inglês está em [docs/en/api-reference.md](docs/en/api-reference.md). Se você mudar um parâmetro, um código de status ou um campo de resposta, atualize as duas páginas e confira de novo `GET /docs`. O esquema OpenAPI é gerado; várias respostas ainda estão documentadas à mão porque os handlers devolvem uma resposta JSON crua.

## Checagem local

Docker é necessário para o teste de ponta a ponta. A partir de um checkout limpo:

```bash
make up
make seed ROWS=20000 DAYS=30
make test
```

`make seed` derruba a tabela. Use um `ROWS` pequeno enquanto itera.

Sem Docker ainda dá para compilar e analisar o Python:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r api/requirements.txt ruff pyflakes
ruff check --select F api loadtest
pyflakes api loadtest
python -m compileall -q api loadtest
docker compose --profile tools config --quiet
```

`docker compose config` precisa da CLI do Compose. Não sobe contêineres.

Não faça commit de `.env`, de arquivos Parquet nem de `loadtest/results/*.json`. `.env.example` é o modelo.

## Escopo

Mantenha segredos fora do histórico do git. As chaves de exemplo em `.env.example` são falsas e locais. Não acrescente números de cartão reais, CPFs reais nem endpoints de produção.

Acompanhe o estilo do arquivo que você tocar. As mensagens de commit seguem um prefixo convencional curto: `feat:`, `fix:`, `docs:`, `test:`, `chore:`.

A documentação existe inteira em português (`docs/pt-BR/`) e inteira em inglês (`docs/en/`). Comportamento novo precisa de um parágrafo no guia correspondente dos dois idiomas, não só de um comentário.

<a id="english"></a>

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

The HTTP contract in Portuguese is [docs/pt-BR/api-reference.md](docs/pt-BR/api-reference.md). The same contract in English is [docs/en/api-reference.md](docs/en/api-reference.md). If you change a parameter, status code, or response field, update both pages and re-check `GET /docs` against them. The OpenAPI schema is generated; several responses are still documented by hand because the handlers return a raw JSON response.

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

Documentation exists in full in Portuguese (`docs/pt-BR/`) and in full in English (`docs/en/`). New behavior needs a paragraph in the matching guide in both languages, not only a comment.
