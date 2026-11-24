# Referência da API

URL de base no Compose local: `http://localhost:8000`.

Documentação interativa gerada a partir do código:

- Swagger UI: `GET /docs`
- ReDoc: `GET /redoc`
- Documento OpenAPI: `GET /openapi.json`

Mantenha esta página e essas rotas juntas. O FastAPI monta o documento OpenAPI a partir das assinaturas das funções, então parâmetros, campos obrigatórios e muitos códigos de status batem com o código. Dois handlers (`GET /v1/transactions` e o lookup de cliente) devolvem um `Response` JSON cru para que timestamps e dinheiro usem um encoder próprio. Os corpos dessas respostas não estão descritos por completo no esquema gerado. As formas abaixo são o que esses handlers escrevem.

Todos os erros usam o corpo padrão do FastAPI, `{"detail": ...}`, exceto `/health`, que sempre devolve o objeto de checagem.

Os textos de `detail` abaixo são as strings em inglês que o servidor devolve. Não são traduzidos em tempo de execução.

## Autenticação

As rotas protegidas exigem o cabeçalho `X-API-Key`. Não há chave em parâmetro de query nem cabeçalho `Authorization`.

As chaves vêm da variável `API_KEYS`: uma lista separada por vírgulas de `chave:papel`. Uma chave sem papel é tratada como `analyst`. O padrão de `.env.example` é:

| Chave | Papel | O que o papel vê |
|---|---|---|
| `dev-analyst-key` | `analyst` | Número do cartão e CPF mascarados no SQL |
| `dev-admin-key` | `admin` | `card_number` e `customer_cpf` selecionados claros. Também lê o job de exportação de outra chave |

Só `admin` pula o mascaramento (`Principal.can_see_pii`). Qualquer outro papel, inclusive um nome inventado, é mascarado.

Chave ausente ou desconhecida é `401`:

```json
{"detail": "missing or invalid X-API-Key"}
```

O log de auditoria guarda um prefixo SHA-256 de 12 caracteres da chave, não a chave. Veja [Segurança](security.md).

Toda resposta da aplicação, inclusive erros que passam pelo middleware, recebe um cabeçalho `X-Request-ID`. Envie um e o mesmo valor volta; senão a API gera um id hex.

## `GET /health`

Sem chave de API. Confere Trino (`SELECT 1`), Redis (`PING`) e armazenamento de objetos (`HeadBucket` no bucket de exports).

`200` quando toda checagem é a string `ok`:

```json
{"status": "ok", "checks": {"trino": "ok", "redis": "ok", "object_storage": "ok"}}
```

`503` quando qualquer checagem lança exceção. O valor que falha é `error:` mais o nome da classe da exceção, não a mensagem:

```json
{"status": "degraded", "checks": {"trino": "error: TrinoConnectionError", "redis": "ok", "object_storage": "ok"}}
```

## `GET /v1/transactions`

Leitura filtrada, da mais nova para a mais antiga, paginação por cursor. O Trino roda toda chamada. Não há cache.

### Parâmetros

| Nome | Onde | Obrigatório | Regras |
|---|---|---|---|
| `date_from` | query | sim | Data ISO `YYYY-MM-DD` |
| `date_to` | query | sim | Data ISO, inclusiva |
| `merchant_category` | query | não | Um dos valores de categoria abaixo |
| `status` | query | não | `approved`, `declined` ou `reversed` |
| `min_amount` | query | não | Decimal, `>= 0` |
| `limit` | query | não | Inteiro 1–1000, padrão 100 |
| `cursor` | query | não | Valor de `next_cursor` da página anterior |

Categorias: `grocery`, `restaurants`, `fuel`, `pharmacy`, `electronics`, `travel`, `apparel`, `entertainment`, `utilities`, `health`, `education`, `transport`.

O predicado de timestamp é `ts >= date_from às 00:00:00` e `ts < o dia seguinte a date_to`, então `date_to` é inclusivo e o predicado combina com as partições `day(ts)`. O comprimento inclusivo do intervalo tem de ser `<= MAX_RANGE_DAYS` (padrão 31). Intervalos maiores são recusados com uma mensagem que aponta para `/v1/exports`.

### Resposta `200`

```json
{
  "data": [
    {
      "transaction_id": "tx_000000001234",
      "ts": "2026-11-23T23:45:01.530896",
      "customer_id": 2,
      "merchant_id": 18,
      "merchant_name": "Example Shop",
      "merchant_category": "travel",
      "city": "São Paulo",
      "state": "SP",
      "amount": "778.57",
      "currency": "BRL",
      "channel": "pos",
      "status": "approved",
      "card_number": "**** **** **** 7844",
      "customer_cpf": "***.***.***-25"
    }
  ],
  "count": 1,
  "next_cursor": "WyIyMDI2LTExLTIzVDIzOjQ1OjAxLjUzMDg5NiIsICJ0eF8wMDAwMDAwMDEyMzQiXQ=="
}
```

`count` é o número de linhas desta página, não o tamanho do filtro inteiro. `next_cursor` é JSON `null` na última página. `amount` é string, nunca um número JSON. `ts` é ISO-8601 de um timestamp sem fuso (sem offset). A ordem das colunas segue a lista de select em `api/app/security.py`.

Uma chave admin devolve os mesmos campos, com `card_number` e `customer_cpf` sem máscara.

### Exemplo

```bash
curl -s -H 'X-API-Key: dev-analyst-key' \
  'localhost:8000/v1/transactions?date_from=2026-11-17&date_to=2026-11-24&merchant_category=travel&min_amount=500&limit=2'
```

Página seguinte: repita todo filtro e acrescente `&cursor=` mais o `next_cursor` anterior. O cursor é Base64 seguro para URL; ainda assim faça o encode na query string (`=` vira `%3D`).

### Erros

| Status | Quando | `detail` |
|---|---|---|
| 401 | Chave de API ausente ou desconhecida | `missing or invalid X-API-Key` |
| 400 | `date_to` anterior a `date_from` | `date_to must be >= date_from` |
| 400 | Contagem inclusiva de dias maior que `MAX_RANGE_DAYS` | `date range limited to 31 days (protects the engine; use /v1/exports for more)` |
| 400 | O cursor não pode ser decodificado | `invalid cursor` |
| 422 | Um parâmetro falha na validação (data ruim, categoria ou status desconhecidos, valor negativo, `limit` fora de 1–1000) | Lista de validação do Pydantic / FastAPI |

Um cursor bem formado que só aponta para um timestamp que você não gerou devolve página vazia e `next_cursor: null`, não 400.

## `GET /v1/customers/{customer_id}/transactions`

Últimas transações de um cliente. Este é o caminho de cache-aside.

### Parâmetros

| Nome | Onde | Obrigatório | Regras |
|---|---|---|---|
| `customer_id` | path | sim | Inteiro |
| `limit` | query | não | Inteiro 1–500, padrão 50 |
| `cache` | query | não | Booleano, padrão `true`. `false` sempre consulta o Trino |

Não há filtro de data. O SQL é:

```sql
SELECT ... FROM iceberg.payments.transactions
WHERE customer_id = ?
ORDER BY ts DESC
LIMIT {limit}
```

### Resposta `200`

Cabeçalhos:

| `X-Cache` | Significado |
|---|---|
| `HIT` | O corpo veio do Redis |
| `MISS` | O Trino rodou, e o corpo foi guardado |
| `BYPASS` | `cache=false`. O Redis não foi lido nem escrito |

```json
{
  "customer_id": 1234,
  "data": [ ],
  "count": 0
}
```

Os itens de `data` têm as mesmas colunas do endpoint paginado, inclusive o mascaramento por papel. `count` é `len(data)`.

### Exemplo

```bash
curl -si -H 'X-API-Key: dev-analyst-key' \
  'localhost:8000/v1/customers/1234/transactions?limit=20' | head -n 20
```

A primeira chamada em geral é `X-Cache: MISS`. A mesma URL de novo, antes do TTL, é `HIT`. Acrescente `cache=false` para forçar o Trino.

### Erros

| Status | Quando |
|---|---|
| 401 | Chave de API ausente ou desconhecida |
| 422 | `customer_id` não é inteiro, ou `limit` está fora de 1–500 |

Um cliente sem linhas é `200` e um array `data` vazio.

## `POST /v1/exports`

Inicia uma exportação em massa. O corpo é JSON.

### Corpo

| Campo | Obrigatório | Regras |
|---|---|---|
| `date_from` | sim | Data ISO |
| `date_to` | sim | Data ISO |
| `merchant_category` | não | O mesmo enum de categoria da consulta paginada |
| `status` | não | `approved`, `declined` ou `reversed` |
| `min_amount` | não | Decimal, `>= 0` |

O intervalo inclusivo tem de ser `<= MAX_EXPORT_DAYS` (padrão 366). Não há teto de 31 dias nesta rota. O SQL tem o mesmo predicado de timestamp e a mesma lista de select por papel da consulta paginada, sem `ORDER BY` nem `LIMIT`.

### Resposta `202`

```json
{
  "job_id": "ff7c0c0e0b1a4f0a9c0e0b1a4f0a9c0e",
  "status": "queued",
  "status_url": "/v1/exports/ff7c0c0e0b1a4f0a9c0e0b1a4f0a9c0e"
}
```

`job_id` é um UUID em hex. `status` nesta resposta é `queued` mesmo se um worker começar logo em seguida. Consulte `status_url`.

### Erros

| Status | Quando | `detail` |
|---|---|---|
| 401 | Chave de API ausente ou desconhecida | `missing or invalid X-API-Key` |
| 400 | Intervalo inclusivo maior que `MAX_EXPORT_DAYS` | `date range limited to 366 days (protects the engine; use /v1/exports for more)` |
| 422 | O corpo não é o esquema: tipos ruins, categoria desconhecida, valor negativo, ou `date_to` anterior a `date_from` | Erro de validação |

Datas invertidas são `422` aqui e `400` em `GET /v1/transactions`. O modelo de exportação as recusa num validador Pydantic; o caminho GET as recusa no handler. Os dois são erros do cliente. O teto de 366 dias continua `400`, porque é conferido no helper de filtro compartilhado depois de o corpo parsear.

### Exemplo

```bash
curl -s -X POST -H 'X-API-Key: dev-analyst-key' \
  -H 'Content-Type: application/json' \
  localhost:8000/v1/exports \
  -d '{"date_from":"2026-11-01","date_to":"2026-11-24","merchant_category":"travel"}'
```

## `GET /v1/exports/{job_id}`

Consulta um job. Quem chama precisa ser o dono (`key_id` da chave que criou) a menos que o papel seja `admin`. Qualquer outro, inclusive um id bem formado que não existe, recebe `404` com `detail` `job not found`. O id do dono não entra na resposta.

Enquanto o worker roda, `status` vai de `queued` para `running`. `rows_written` atualiza conforme os blocos chegam. Os valores finais são `succeeded` e `failed`.

Sucesso:

```json
{
  "job_id": "ff7c0c0e0b1a4f0a9c0e0b1a4f0a9c0e",
  "status": "succeeded",
  "request": {
    "date_from": "2026-11-01",
    "date_to": "2026-11-24",
    "merchant_category": "travel",
    "status": null,
    "min_amount": null
  },
  "created_at": "2026-11-24T19:00:00.000000+00:00",
  "started_at": "2026-11-24T19:00:00.100000+00:00",
  "finished_at": "2026-11-24T19:00:30.000000+00:00",
  "rows_written": 427,
  "object_key": "2026/11/24/ff7c0c0e0b1a4f0a9c0e0b1a4f0a9c0e.parquet",
  "size_bytes": 18259,
  "duration_s": 1.2,
  "download_url": "http://localhost:9000/exports/2026/11/24/ff7c0c0e0b1a4f0a9c0e0b1a4f0a9c0e.parquet?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=...",
  "download_expires_in_s": 3600
}
```

`download_url` é emitida neste GET, não guardada. Chame de novo depois que expirar. `rows_written` pode ser `0` para um filtro que não casou nada; isso ainda é `succeeded`, e o objeto é um Parquet com o esquema e sem linhas de dados.

Falha:

```json
{
  "job_id": "ff7c0c0e0b1a4f0a9c0e0b1a4f0a9c0e",
  "status": "failed",
  "error": "short exception text",
  "finished_at": "2026-11-24T19:00:05.000000+00:00"
}
```

`error` é truncado em 500 caracteres. Não há `download_url`.

O registro no Redis vive 7 dias (`export:{job_id}`). Depois disso, o GET é 404.

### Exemplo

```bash
curl -s -H 'X-API-Key: dev-analyst-key' \
  localhost:8000/v1/exports/ff7c0c0e0b1a4f0a9c0e0b1a4f0a9c0e
curl -s -o export.parquet '<download_url>'
python3 -c "import pyarrow.parquet as pq; print(pq.read_table('export.parquet').num_rows)"
```

## Comportamento da paginação

A ordem é `ts DESC, transaction_id DESC`. O cursor codifica esse par. A consulta seguinte acrescenta `ts < cursor_ts OR (ts = cursor_ts AND transaction_id < cursor_tx)`.

Não edite o cursor. Não reutilize um cursor com outro `date_from`, `date_to`, categoria, status ou `min_amount`: o predicado ainda se aplica, mas a fronteira da página pertencia a outro filtro, então você pode pular ou repetir linhas.

Não há cursor de página anterior nem contagem total.

## Comportamento do mascaramento

Para todo papel que não seja `admin`, a lista de select substitui as duas colunas sensíveis:

```sql
concat('**** **** **** ', substr(card_number, -4)) AS card_number
regexp_replace(customer_cpf, '^\d{3}\.\d{3}\.\d{3}', '***.***.***') AS customer_cpf
```

Um CPF gerado parece `123.456.789-00`. Depois do replace parece `***.***.***-00`. Um cartão vira `**** **** **** ` mais os quatro últimos caracteres gravados na coluna. Valores que não casam o padrão de CPF voltam inalterados do `regexp_replace`. O SQL do admin seleciona as colunas cruas, então o JSON e o Parquet contêm o que o gerador escreveu (dígitos, sem espaços, nos cartões).

O mascaramento não depende do cache. A chave do cache inclui o papel, e o SQL que preenche um miss já está mascarado. `cache=false` usa a mesma lista de select.

## Linha de auditoria

Cada requisição acrescenta um objeto JSON ao log do processo da API e a `AUDIT_LOG_PATH`:

```json
{
  "ts": "2026-11-24T19:12:34.446442+00:00",
  "request_id": "5407ff75abcd",
  "key_id": "ddd2c99c9421",
  "client": "172.18.0.1",
  "method": "GET",
  "path": "/v1/transactions",
  "query": "date_from=2026-11-17&date_to=2026-11-24&limit=2",
  "status": 200,
  "duration_ms": 2.7
}
```

`key_id` é null quando nenhuma chave foi enviada. `query` é null quando a query string está vazia. O caminho não deve carregar o segredo: a chave é um cabeçalho.
