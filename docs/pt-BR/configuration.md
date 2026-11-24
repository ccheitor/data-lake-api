# Configuração

Os valores locais ficam em `.env`, copiado de `.env.example` pelo `make up` quando o arquivo não existe. O Compose interpola o `.env`. O processo da API lê o próprio ambiente, que o Compose monta a partir desses valores mais alguns hostnames fixos.

Reinicie o contêiner da API depois de mudar uma variável que ela lê. `docker compose up -d api` recria. O Trino lê `trino/catalog/iceberg.properties` e `trino/etc/*` dos bind mounts; uma mudança ali exige `docker compose up -d trino`.

## `.env` e o processo da API

| Variável | Padrão | Quem lê | Efeito |
|---|---|---|---|
| `MINIO_ROOT_USER` | `minioadmin` | Compose, mapeado para `AWS_ACCESS_KEY_ID` do Trino, do catálogo, da API e do seed | Chave de acesso S3. Também o usuário do console |
| `MINIO_ROOT_PASSWORD` | `minioadmin123` | O mesmo, como `AWS_SECRET_ACCESS_KEY` | Segredo S3. Também a senha do console |
| `WAREHOUSE_BUCKET` | `warehouse` | Compose, para o nome do bucket, `CATALOG_WAREHOUSE` e o `WAREHOUSE` do seed | Bucket do lago. Veja a ressalva do Trino abaixo |
| `EXPORTS_BUCKET` | `exports` | Compose e `Settings.exports_bucket` | Bucket de exportação. O ciclo de vida em `minio-init` expira objetos depois de 1 dia |
| `API_KEYS` | `dev-analyst-key:analyst,dev-admin-key:admin` | API | Lista `chave:papel` separada por vírgulas. Papel vazio significa `analyst` |
| `S3_PUBLIC_ENDPOINT` | `http://localhost:9000` | Assinador da API | Host escrito nas URLs pré-assinadas |
| `CACHE_TTL_SECONDS` | `300` | API | TTL do Redis para lookups, em segundos |
| `AWS_REGION` | `us-east-1` | API; as propriedades do Trino também têm `us-east-1` fixo no arquivo | Região SigV4 |
| `S3_ENDPOINT` | `http://localhost:9000` fora do Compose; `http://minio:9000` dentro da API e do seed | Uploads da API, seed, catálogo | API S3 que o processo chama. Não é o host da pré-assinatura |
| `TRINO_HOST` | `localhost`; o Compose define `trino` | API | Host do coordinator do Trino |
| `TRINO_PORT` | `8080` | API | Porta HTTP do Trino |
| `TRINO_CATALOG` | `iceberg` | API | Nome do catálogo no SQL |
| `TRINO_SCHEMA` | `payments` | API e seed | Namespace Iceberg. O gerador lê isto |
| `TABLE_NAME` | `transactions` | Só a API | Nome da tabela no SQL. O gerador sempre grava `transactions` |
| `REDIS_URL` | `redis://localhost:6379/0`; o Compose define `redis://redis:6379/0` | API | Cache e armazenamento de jobs de exportação |
| `PRESIGN_SECONDS` | `3600` | API | Vida de cada URL pré-assinada |
| `EXPORT_WORKERS` | `2` | API | Threads no pool de exportação de cada processo da API |
| `MAX_RANGE_DAYS` | `31` | API | Teto inclusivo de `GET /v1/transactions` |
| `MAX_EXPORT_DAYS` | `366` | API | Teto inclusivo de `POST /v1/exports` |
| `AUDIT_LOG_PATH` | `audit.log`; o Compose define `/var/log/api/audit.log` | API, na importação | File handler do logger de auditoria. O diretório pai precisa existir |
| `ICEBERG_REST_URI` | `http://localhost:8181`; o Compose define `http://iceberg-rest:8181` | Seed | URL do catálogo REST. A API não chama; o Trino chama, pelo arquivo de propriedades |
| `WAREHOUSE` | `s3://warehouse/` | Seed | URI de warehouse passada ao PyIceberg |
| `SEED_ROWS` | `1000000` | Seed, só se você rodar `python -m seed.generate` sem `--rows` | `make seed` passa `--rows` e ignora isto |

`TABLE_NAME` e `TRINO_SCHEMA` na API têm de coincidir com o que o gerador criou. `make seed` usa o valor Compose de `TRINO_SCHEMA` (`payments`) e o nome fixo de tabela `transactions`.

### Ressalva do bucket de warehouse

`WAREHOUSE_BUCKET` muda o bucket que `minio-init`, o catálogo REST e o seed usam. **Não** muda o arquivo do conector do Trino. `trino/catalog/iceberg.properties` contém:

```
iceberg.rest-catalog.warehouse=s3://warehouse/
s3.endpoint=http://minio:9000
```

Deixe o bucket com o nome `warehouse`, ou edite esse arquivo para a URI de warehouse coincidir, e recrie o contêiner do Trino. Senão o gerador e o Trino apontam para warehouses diferentes e as consultas veem um catálogo vazio.

## Makefile

As variáveis podem ser sobrescritas na linha de comando (`make seed ROWS=100000`).

| Variável | Padrão | Usada por |
|---|---|---|
| `DOCKER` | `docker` | Todo alvo. Defina `sudo docker` quando o socket é só do root |
| `COMPOSE` | `$(DOCKER) compose` | Up, seed, logs, ps, down, clean e o flush do Redis em `loadtest-all` |
| `ROWS` | `2000000` | `make seed` |
| `DAYS` | `90` | `make seed` |
| `MODE` | `cached` | `make loadtest`. Um de `cached`, `direct`, `page`, `warmup` |
| `VUS` | `10` | `make loadtest` |
| `DURATION` | `60s` | `make loadtest`, exceto `warmup`, que ignora e roda `HOT` iterações |
| `API_KEY` | `dev-analyst-key` | `make loadtest`, repassada ao k6 |
| `HOT` | `1000` | `make loadtest`, como `HOT_CUSTOMERS` |

| Alvo | O que faz |
|---|---|
| `make up` | Copia `.env.example` para `.env` se precisar, `compose up -d --build`, roda `scripts/wait-healthy.sh` |
| `make seed` | `compose --profile tools run --rm seed --rows $(ROWS) --days $(DAYS) --replace` |
| `make test` | `scripts/smoke-test.sh` contra `BASE_URL` (padrão `http://localhost:8000`) |
| `make loadtest` | k6 em `grafana/k6:latest` com `--network host` |
| `make loadtest-all` | `FLUSHALL` no Redis, depois direct, cached, warmup, cached de novo, depois page |
| `make logs` | Acompanha os logs da API |
| `make ps` | `compose ps` |
| `make down` | Para os contêineres e conserva os volumes |
| `make clean` | Para os contêineres e apaga os volumes (`down -v`) |

`make seed` sempre derruba a tabela (`--replace`). Quantidade de clientes, de estabelecimentos, tamanho do lote e semente do RNG não são variáveis do Makefile. Passe-as rodando o serviço de seed você mesmo:

```bash
docker compose --profile tools run --rm seed \
  --rows 100000 --days 30 --customers 5000 --merchants 500 --batch 50000 --seed 7 --replace
```

As flags estão em [Modelo de dados](data-model.md).

Ambiente do teste de fumaça, lido pelo script de shell e não pelo Compose:

| Variável | Padrão |
|---|---|
| `BASE_URL` | `http://localhost:8000` |
| `API_KEY` | `dev-analyst-key` |
| `ADMIN_KEY` | `dev-admin-key` |

O k6 também honra `BASE_URL` dentro do contêiner. O Makefile não a passa, então vale o padrão `http://localhost:8000`, correto só com rede do host.

## Serviços do Compose

`docker-compose.yml` define `name: data-lake-api`, então volumes e a rede recebem esse prefixo.

| Serviço | Função | Portas no host |
|---|---|---|
| `minio` | Silo, API S3 e console | 9000, 9001 |
| `minio-init` | Cria `warehouse` e `exports`, acrescenta regra de expiração de 1 dia em exports e sai | nenhuma |
| `iceberg-rest` | Catálogo REST, arquivo SQLite no volume `catalog-data` | 8181 |
| `trino` | Motor SQL | 8080 |
| `redis` | Cache, 128 MB `allkeys-lru`, persistência desligada (`--save ""`) | 6379 |
| `api` | FastAPI, dois workers uvicorn, sem access log | 8000 |
| `seed` | Só o perfil `tools` | nenhuma |

Volumes: `minio-data`, `catalog-data`, `audit-logs`. `make clean` os remove.

A API espera o Trino saudável e o Redis iniciado. O Trino espera o contêiner do catálogo ter iniciado, não um healthcheck de catálogo passar (a imagem fixture não define um). `minio-init` espera o Silo saudável.

## Arquivos do Trino

`trino/etc/config.properties` roda um nó só (`coordinator=true`, `node-scheduler.include-coordinator=true`) com `query.max-memory` e `query.max-memory-per-node` em 800 MB. Esses tetos ficam abaixo do heap de 1.5 GB.

`trino/etc/jvm.config` define `-Xmx1536M`, um teto de 256 MB de memória direta e G1. O `mem_limit` do contêiner é 3 GB para caberem heap, metaspace, cache de código e buffers diretos. Mude heap e `mem_limit` juntos. O [guia de desempenho](performance.md) registra o que aconteceu quando não estavam juntos.

`trino/catalog/iceberg.properties` aponta o conector Iceberg para `http://iceberg-rest:8181`, liga o sistema de arquivos S3 nativo e liga o acesso path-style. As credenciais são `${ENV:AWS_ACCESS_KEY_ID}` e `${ENV:AWS_SECRET_ACCESS_KEY}`, que o Compose preenche com a conta raiz do MinIO.

## Ambiente do gerador

Dentro do contêiner de seed o gerador também lê as variáveis AWS padrão e:

| Variável | Padrão no código | Valor no Compose |
|---|---|---|
| `ICEBERG_REST_URI` | `http://localhost:8181` | `http://iceberg-rest:8181` |
| `WAREHOUSE` | `s3://warehouse/` | `s3://${WAREHOUSE_BUCKET}/` |
| `S3_ENDPOINT` | `http://localhost:9000` | `http://minio:9000` |
| `AWS_REGION` | `us-east-1` | `us-east-1` |
| `TRINO_SCHEMA` | `payments` | `payments` |

O acesso path-style é forçado no código (`s3.path-style-access=true`), o que Silo e MinIO exigem.
