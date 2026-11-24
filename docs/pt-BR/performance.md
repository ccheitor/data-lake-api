# Desempenho

Não há um único número de transações por segundo nesta stack. Uma API de dados serve dois trabalhos diferentes, e eles precisam de máquinas diferentes. Os números abaixo são a execução registrada em [loadtest/RESULTS.md](../../loadtest/RESULTS.md). Não são um benchmark do seu laptop. O hardware faz parte do resultado.

## Por que o caminho quente não é o Trino

O Trino foi feito para varrer muito dado por consulta: planejar, listar arquivos Iceberg, agendar splits, ler Parquet. Esse custo fixo domina quando a consulta devolve vinte linhas de um cliente. O filtro de cliente não é a coluna de partição (`day(ts)`), então um lookup toca cada dia da tabela. Ordenar os arquivos por `customer_id` deixa o Trino pular alguns row groups. Não remove o custo de planejamento.

A API portanto usa Redis em cache-aside para `GET /v1/customers/{id}/transactions`:

1. Monta a chave `cust:{id}:{role}:{limit}`.
2. No hit, devolve o JSON guardado (`X-Cache: HIT`).
3. No miss, consulta o Trino, guarda o JSON com `CACHE_TTL_SECONDS` e devolve (`X-Cache: MISS`).

`cache=false` pula os dois passos e é como o teste de carga chama o Trino direto (`X-Cache: BYPASS`).

A consulta paginada fica no Trino. O intervalo de datas é obrigatório e limitado a 31 dias para a varredura podar partições, e o cursor impede que páginas fundas virem varreduras `OFFSET`. A vazão ali é "interativa", não "alto TPS".

Um extrato em massa não é um problema de TPS. `POST /v1/exports` volta assim que o job entra na fila. Um job move milhões de linhas para um objeto Parquet. Poucas requisições, muitos bytes, e a API não segura o resultado inteiro.

Se o cache está frio, a primeira chamada de cada cliente paga o Trino e as seguintes não, até o TTL. Esta demonstração não invalida chaves quando o dado muda, porque nada transmite mudanças. Um armazenamento de baixa latência de produção (Redis, Cassandra, Postgres ou similar) seria preenchido por um consumidor ao lado da ingestão. Essa divisão é o ponto da arquitetura, descrita em [Arquitetura](architecture.md).

## Como rodar o k6

No Linux, a partir da raiz do repositório, com a stack no ar e os dados carregados:

```bash
make loadtest MODE=direct  VUS=10 DURATION=60s
make loadtest MODE=cached  VUS=10 DURATION=60s
make loadtest MODE=warmup  HOT=200
make loadtest MODE=cached  HOT=200
make loadtest MODE=page
make loadtest-all HOT=200
```

`make loadtest-all` esvazia o Redis e depois roda direct, cached (frio), warmup, cached (quente) e page, nessa ordem. `HOT` é a faixa de ids de cliente que o k6 amostra (`HOT_CUSTOMERS`). A execução publicada usou `HOT=200`. O padrão do Makefile é `1000` se você omitir.

| `MODE` | O que o k6 pede |
|---|---|
| `direct` | `GET /v1/customers/{id}/transactions?limit=20&cache=false` |
| `cached` | O mesmo caminho com `cache=true`. Os ids são aleatórios em `1..HOT` |
| `warmup` | Uma requisição por id de 1 até `HOT`, para a execução cached seguinte estar quente |
| `page` | `GET /v1/transactions` nos últimos 7 dias, `limit=100` |

O k6 roda como `grafana/k6:latest` com `--network host` e grava um resumo JSON de uma linha mais um arquivo em `loadtest/results/`. Esses JSON estão no gitignore. O script conta uma resposta como hit de cache só quando o cabeçalho `X-Cache` é `HIT`.

`--network host` alcança `localhost:8000` num daemon Linux. O Docker Desktop no macOS e no Windows não prende esse contêiner à rede do host, então o k6 chamaria localhost dentro do contêiner e perderia a API. Nessas máquinas instale o k6 localmente e rode `loadtest/k6-lookup.js` contra `http://localhost:8000`, passando as mesmas variáveis `MODE`, `VUS`, `DURATION`, `API_KEY` e `HOT_CUSTOMERS`.

O Locust é a alternativa no host (não usa `--network host`):

```bash
pip install locust
locust -f loadtest/locustfile.py --host http://localhost:8000 --headless -u 20 -r 5 -t 60s --tags cached
```

`--tags direct` muda para `cache=false`. O arquivo não tem tarefa `page`. `HOT_CUSTOMERS` e `API_KEY` são variáveis de ambiente, com padrão 1000 e `dev-analyst-key`.

Registre a máquina, a quantidade de linhas e o comando ao lado de qualquer número que você guardar. O modelo no fim de [loadtest/RESULTS.md](../../loadtest/RESULTS.md) existe para isso. Uma passagem de 60 segundos não é uma curva de capacidade. O arquivo de resultados pede para repetir cada cenário pelo menos duas vezes, e para subir `VUS` até o p95 ou os erros se moverem, se você quiser um joelho.

## Resultados medidos

A única execução medida guardada no repositório é a **Execução 1** de [loadtest/RESULTS.md](../../loadtest/RESULTS.md), datada de 2026-11-24.

Condições, copiadas daquele arquivo:

- VM de nuvem x86_64 compartilhada, 8 vCPU (Intel Xeon), cerca de 16 GB de RAM, outras cargas no host.
- k6 no mesmo host, então o gerador compete com a stack por CPU.
- Trino 484, um nó, heap de 1.5 GB, limite de contêiner de 3 GB. API com 2 workers uvicorn. Redis com maxmemory de 128 MB.
- Conjunto: 2,000,000 linhas, 90 dias, 50,000 clientes, particionado por `day(ts)`.
- Comando: `make loadtest-all HOT=200 VUS=10 DURATION=60s`.

| Cenário | VUs | Duração | Requisições | Req/s | p50 | p95 | p99 | Erros | Hits de cache |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct (lookup, Trino toda vez) | 10 | 60 s | 244 | 4.0 | 2,530.3 ms | 3,045.2 ms | 3,166.3 ms | 0% | 0 |
| cached, frio (cache vazio no início) | 10 | 60 s | 5,689 | 94.8 | 2.9 ms | 6.6 ms | 2,722.7 ms | 0% | 5,450 |
| warmup (ids 1..200, já em cache) | 10 | — | 200 | — | 3.5 ms | 5.6 ms | 6.8 ms | 0% | 200 |
| cached, quente (100% de hits) | 10 | 60 s | 157,964 | 2,632.6 | 3.7 ms | 5.2 ms | 6.1 ms | 0% | 157,964 |
| page (janela de 7 dias, 100 linhas, Trino) | 10 | 60 s | 2,281 | 37.9 | 272.8 ms | 340.1 ms | 361.5 ms | 0% | 0 |

Uma exportação assíncrona, fora do laço do k6: 2,000,000 linhas viraram um objeto Parquet ZSTD de 47.7 MB em 30.9 segundos do início do job até o upload terminar.

Observações da mesma nota:

- Um lookup pontual sem cache levou cerca de 0.8 segundos com o motor ocioso. Com 10 usuários concorrentes as consultas enfileiraram no Trino e a latência foi para cerca de 2.5 segundos.
- A execução de cache frio é bimodal: hits em milissegundos, misses em segundos (veja o p99).
- Uma tentativa anterior limitou o contêiner do Trino a 2 GB e deixou o heap padrão da imagem (80% da RAM). Sob carga o contêiner foi morto por OOM e as requisições falharam. Definir um heap de 1.5 GB dentro de um limite de 3 GB consertou. Dimensione o heap e o limite do contêiner juntos.

Leia isso como uma comparação de ordem de grandeza naquela VM: lookup direto cerca de 4 requisições/s, cache quente cerca de 2,600 requisições/s com p95 de 5.2 ms, varredura paginada cerca de 38 requisições/s. Uma máquina mais quieta, outra quantidade de linhas ou o k6 em outro host não reproduzem os mesmos números. Rode e escreva a nova linha em `loadtest/RESULTS.md`.

## Armazenamento: arquivos contra o lago

O mesmo gerador também foi gravado nos formatos de arquivo que um parceiro ainda receberia, e como Parquet / Iceberg. A nota completa (checagem de 10 milhões de linhas, bytes que uma consulta de fato lê, projeções lineares, esboço de preço de lista S3 e ressalvas) está em [loadtest/STORAGE.md](../../loadtest/STORAGE.md). Reproduza a partir da raiz do repositório com `pip install -r loadtest/requirements.txt` e `python loadtest/storage_compare.py --rows 2000000 --rows-large 10000000`.

Medido em 2026-11-24, 2,000,000 linhas, 90 dias, semente 42. Hardware naquela nota: Intel Xeon compartilhado de 8 núcleos, 16 GiB de RAM.

| Formato | Bytes | Tamanho | Bytes/linha | contra CSV sem compressão |
|---|---:|---:|---:|---:|
| CSV, sem compressão (UTF-8, cabeçalho, aspas no estilo Excel) | 292,630,765 | 279.1 MiB | 146.32 | 1.000× |
| TXT de largura fixa / posicional (estilo CNAB) | 554,505,021 | 528.8 MiB | 277.25 | 1.895× |
| JSON Lines (um objeto por linha; o que uma API ingênua devolveria) | 690,630,624 | 658.6 MiB | 345.32 | 2.360× |
| CSV com gzip no nível 6 (padrão do gzip GNU) | 65,139,015 | 62.12 MiB | 32.57 | 0.223× |
| Tabela Iceberg (zstd, partições day(ts), metadados incluídos) | 67,355,337 | 64.24 MiB | 33.68 | 0.230× |
| Arquivo Parquet único (zstd, dicionário, estatísticas) | 37,867,262 | 36.11 MiB | 18.93 | 0.129× |

O número do Iceberg inclui metadados. Só os arquivos de dados foram 66,836,438 bytes em 720 arquivos Parquet. Uma fatia de 7 dias dessas partições foi 5,180,671 bytes; os mesmos 7 dias limitados a `customer_id` e `amount` foram 754,415 bytes. Não trate as projeções de 1 bilhão de linhas e de 1 TiB da nota como medições.
