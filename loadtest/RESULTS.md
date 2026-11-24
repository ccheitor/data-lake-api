[Português](#portugues) | [English](#english)

<a id="portugues"></a>

# Resultados do teste de carga

Publique sempre o hardware, o conjunto de dados e as configurações ao lado dos números. Estas são execuções únicas; repita cada cenário pelo menos duas vezes antes de tirar conclusões.

## Execução 1 — VM de nuvem compartilhada (execução de referência do autor)

* **Data:** 2026-11-24
* **Hardware:** VM de nuvem x86_64 compartilhada, 8 vCPU (Intel Xeon), ~16 GB de RAM, outras cargas no host
* **Gerador de carga:** k6 (`grafana/k6:latest`) no **mesmo host** (compete por CPU com a stack)
* **Limites da stack:** Trino 484 de um nó, heap de 1.5 GB / contêiner de 3 GB; API com 2 workers uvicorn; Redis 128 MB
* **Conjunto:** 2,000,000 linhas, 90 dias, 50,000 clientes, particionado por `day(ts)`
* **Comando:** `make loadtest-all HOT=200 VUS=10 DURATION=60s`

| Cenário | VUs | Duração | Requisições | Req/s | p50 | p95 | p99 | Erros | Hits de cache |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct (lookup, Trino toda vez) | 10 | 60 s | 244 | 4.0 | 2,530.3 ms | 3,045.2 ms | 3,166.3 ms | 0% | 0 |
| cached, frio (cache vazio no início) | 10 | 60 s | 5,689 | 94.8 | 2.9 ms | 6.6 ms | 2,722.7 ms | 0% | 5,450 |
| warmup (ids 1..200, já em cache) | 10 | — | 200 | — | 3.5 ms | 5.6 ms | 6.8 ms | 0% | 200 |
| cached, quente (100% de hits) | 10 | 60 s | 157,964 | 2,632.6 | 3.7 ms | 5.2 ms | 6.1 ms | 0% | 157,964 |
| page (janela de 7 dias, 100 linhas, Trino) | 10 | 60 s | 2,281 | 37.9 | 272.8 ms | 340.1 ms | 361.5 ms | 0% | 0 |

Uma exportação assíncrona (não é teste de carga): 2,000,000 linhas → 47.7 MB de Parquet (zstd), 30.9 s do início do job até o objeto enviado.

Observações:

* Um lookup pontual sem cache leva ~0.8 s com o motor ocioso; com 10 usuários concorrentes as consultas enfileiram dentro do Trino e a latência cresce para ~2.5 s — o motor é orientado a vazão.
* A execução de cache frio mostra a latência bimodal clássica: hits em milissegundos, misses em segundos (p99).
* Numa tentativa anterior o Trino tinha limite de contêiner de 2 GB com o heap padrão da imagem (80% da RAM); sob carga o contêiner foi morto por OOM e toda requisição falhou. O heap explícito de 1.5 GB dentro de um limite de 3 GB consertou — dimensione o heap da JVM *e* o limite do contêiner juntos.

## A sua execução (modelo)

* **Data:**
* **Hardware:** (modelo de CPU / núcleos, RAM, laptop ou VM, por exemplo OCI VM.Standard.A1.Flex 2 OCPU / 12 GB)
* **Gerador de carga:** (mesmo host ou outra máquina)
* **Conjunto:** (`make seed ROWS=...`)
* **Comando:**

| Cenário | VUs | Duração | Requisições | Req/s | p50 | p95 | p99 | Erros | Hits de cache |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct | | | | | | | | | |
| cached, frio | | | | | | | | | |
| cached, quente | | | | | | | | | |
| page | | | | | | | | | |

<a id="english"></a>

# Load test results

Always publish the hardware, dataset and settings next to the numbers. These are single runs;
repeat each scenario at least twice before drawing conclusions.

## Run 1 — shared cloud VM (reference run by the author)

* **Date:** 2026-11-24
* **Hardware:** shared 8 vCPU x86_64 cloud VM (Intel Xeon), ~16 GB RAM, other workloads running on the host
* **Load generator:** k6 (`grafana/k6:latest`) on the **same host** (competes for CPU with the stack)
* **Stack limits:** Trino 484 single node, 1.5 GB heap / 3 GB container; API 2 uvicorn workers; Redis 128 MB
* **Dataset:** 2,000,000 rows, 90 days, 50,000 customers, partitioned by `day(ts)`
* **Command:** `make loadtest-all HOT=200 VUS=10 DURATION=60s`

| Scenario | VUs | Duration | Requests | Req/s | p50 | p95 | p99 | Errors | Cache hits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct (lookup, Trino every time) | 10 | 60 s | 244 | 4.0 | 2,530.3 ms | 3,045.2 ms | 3,166.3 ms | 0% | 0 |
| cached, cold (cache empty at start) | 10 | 60 s | 5,689 | 94.8 | 2.9 ms | 6.6 ms | 2,722.7 ms | 0% | 5,450 |
| warmup (ids 1..200, already cached) | 10 | — | 200 | — | 3.5 ms | 5.6 ms | 6.8 ms | 0% | 200 |
| cached, warm (100% hits) | 10 | 60 s | 157,964 | 2,632.6 | 3.7 ms | 5.2 ms | 6.1 ms | 0% | 157,964 |
| page (7-day window, 100 rows, Trino) | 10 | 60 s | 2,281 | 37.9 | 272.8 ms | 340.1 ms | 361.5 ms | 0% | 0 |

Single async export (not a load test): 2,000,000 rows → 47.7 MB Parquet (zstd), 30.9 s from
job start to object uploaded.

Observations:

* A single uncached point lookup takes ~0.8 s with an idle engine; with 10 concurrent users the
  queries queue inside Trino and latency grows to ~2.5 s — the engine is throughput-oriented.
* The cold-cache run shows the classic bimodal latency: hits in milliseconds, misses in seconds (p99).
* In an earlier attempt Trino had a 2 GB container limit with the image default heap
  (80% of RAM); under load the container was OOM-killed and every request failed. The explicit
  1.5 GB heap inside a 3 GB limit fixed it — size JVM heap *and* container limit together.

## Your run (template)

* **Date:**
* **Hardware:** (CPU model / cores, RAM, laptop or VM, e.g. OCI VM.Standard.A1.Flex 2 OCPU / 12 GB)
* **Load generator:** (same host or separate machine)
* **Dataset:** (`make seed ROWS=...`)
* **Command:**

| Scenario | VUs | Duration | Requests | Req/s | p50 | p95 | p99 | Errors | Cache hits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| direct | | | | | | | | | |
| cached, cold | | | | | | | | | |
| cached, warm | | | | | | | | | |
| page | | | | | | | | | |
