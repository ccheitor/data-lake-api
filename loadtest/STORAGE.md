[Português](#portugues) | [English](#english)

<a id="portugues"></a>

# Comparação de armazenamento: formatos de troca de arquivo contra o lago

Medido em 2026-11-24 (America/Sao_Paulo). Reproduzível com:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r loadtest/requirements.txt
python loadtest/storage_compare.py --rows 2000000 --rows-large 10000000
```

O script reusa `api/seed/generate.py` (o mesmo esquema, pools Faker pt_BR, clientes Zipf, valores log-normais, ordenação por `(customer_id, ts)`, semente `42`). A janela de calendário está fixa em 90 dias terminando de forma exclusiva em 2026-11-24, então a execução não depende do relógio. Lotes de 250,000 linhas, 50,000 clientes, 5,000 estabelecimentos.

Hardware: Intel Xeon compartilhado de 8 núcleos, 16 GiB de RAM, Linux x86_64, Python 3.13.5, PyArrow 26.0.1, DuckDB 2.0.1, PyIceberg 0.12.0. Os arquivos grandes foram gravados em `/tmp/storage-compare` e não estão no repositório.

## 2 milhões de linhas (principal, a mesma escala do teste de carga)

Tempo: 49.6 s para gerar e gravar todos os formatos, inclusive a tabela Iceberg.

| Formato | Bytes | Tamanho | Bytes/linha | contra CSV sem compressão |
|---|---:|---:|---:|---:|
| CSV, sem compressão (UTF-8, cabeçalho, aspas no estilo Excel) | 292,630,765 | 279.1 MiB | 146.32 | 1.000× |
| TXT de largura fixa / posicional (estilo CNAB, layout abaixo) | 554,505,021 | 528.8 MiB | 277.25 | 1.895× |
| JSON Lines (um objeto por linha; o que uma API ingênua devolveria) | 690,630,624 | 658.6 MiB | 345.32 | 2.360× |
| CSV com gzip no nível 6 (padrão do gzip GNU) | 65,139,015 | 62.12 MiB | 32.57 | 0.223× |
| Tabela Iceberg (zstd, partições day(ts), metadados incluídos) | 67,355,337 | 64.24 MiB | 33.68 | 0.230× |
| Arquivo Parquet único (zstd, dicionário, estatísticas) | 37,867,262 | 36.11 MiB | 18.93 | 0.129× |

Detalhe do Iceberg (warehouse SqlCatalog local): 720 arquivos Parquet de dados somando 66,836,438 bytes (63.74 MiB), mais 518,899 bytes (506.7 KiB) de metadados (manifests, JSON de metadados, catálogo SQLite). 746 arquivos no total. Nenhum valor foi truncado no layout posicional.

O arquivo Parquet único é 7.73× menor que o CSV sem compressão (12.9% do CSV). A tabela Iceberg é 4.34× menor que o CSV (23.0%), quase do tamanho do CSV com gzip, porque a tabela é particionada por dia e commitada a cada 250,000 linhas: 720 arquivos de dados de cerca de 90 KiB cada. Compactação periódica fecharia a maior parte da diferença para o Parquet de arquivo único. Gzip nível 6 reduz o CSV a 22.3% do tamanho sem compressão.

### Confirmação no armazenamento de objetos do projeto

O warehouse compatível com MinIO da stack Docker foi esvaziado, e então rodou o equivalente ao `make`: `docker compose --profile tools run --rm seed --rows 2000000 --days 90 --replace` (o gerador do projeto, janela `datetime.now()`, não a data fixa). Medido somando os tamanhos de objeto de `mc ls --recursive --json`:

- Total do warehouse: **67,370,345 bytes** (64.25 MiB, 33.69 bytes/linha)
- Arquivos de dados: 66,896,858 bytes, 720 objetos
- Metadados: 473,487 bytes, 25 objetos

Isso combina com a tabela Iceberg local da execução com data fixa (67,355,337 bytes) bem abaixo de 0.1%. A stack foi parada com `docker compose down` depois. Trino e a API não foram iniciados; só MinIO, o catálogo Iceberg REST e o job de seed foram necessários para esta medição.

## 10 milhões de linhas (checagem de linearidade)

Tempo: 231.63 s. Bytes/linha são essencialmente idênticos à execução de 2M, então uma projeção linear se justifica neste conjunto sintético.

| Formato | Bytes | Tamanho | Bytes/linha | contra bytes/linha de 2M |
|---|---:|---:|---:|---:|
| CSV, sem compressão (UTF-8, cabeçalho, aspas no estilo Excel) | 1,463,127,524 | 1.363 GiB | 146.31 | 1.0000× |
| TXT de largura fixa / posicional (estilo CNAB, layout abaixo) | 2,772,530,324 | 2.582 GiB | 277.25 | 1.0000× |
| JSON Lines (um objeto por linha; o que uma API ingênua devolveria) | 3,453,127,383 | 3.216 GiB | 345.31 | 1.0000× |
| CSV com gzip no nível 6 (padrão do gzip GNU) | 325,702,346 | 310.6 MiB | 32.57 | 1.0000× |
| Tabela Iceberg (zstd, partições day(ts), metadados incluídos) | 337,246,995 | 321.6 MiB | 33.72 | 1.0014× |
| Arquivo Parquet único (zstd, dicionário, estatísticas) | 189,118,105 | 180.4 MiB | 18.91 | 0.9988× |

Iceberg em 10M: 3600 arquivos de dados, 318.7 MiB de Parquet mais 2.960 MiB de metadados.

## Quanto uma consulta tem de ler

Os bytes físicos lidos numa projeção de colunas foram tirados do rodapé Parquet (`total_compressed_size` de cada pedaço de coluna). É o que um motor bem comportado de fato lê do disco, ignorando um rodapé/magic minúsculo. O DuckDB serviu só como checagem de sanidade de que uma consulta de duas colunas devolve a mesma contagem de linhas (2,000,000 e 10,000,000).

No conjunto de 2 milhões de linhas:

| Varredura | Bytes | contra o arquivo Parquet inteiro | contra CSV sem compressão |
|---|---:|---:|---:|
| CSV sem compressão, todas as colunas (o arquivo) | 292,630,765 | — | 1.000× |
| Parquet único, as 14 colunas | 37,853,670 | 1.000× | 0.129× |
| Parquet único, só `customer_id` + `amount` | 5,952,894 | 0.157× | 0.020× |
| Arquivos de dados Iceberg, todas as colunas | 63,952,479 | — | 0.219× |
| Arquivos de dados Iceberg, só `customer_id` + `amount` | 9,735,800 | 0.152 das colunas Iceberg | 0.033× |
| Iceberg, últimos 7 dias populados, todas as colunas desses arquivos | 5,180,671 | 0.078 dos arquivos de dados Iceberg (56 arquivos, 7 de 90 dias) | 0.018× |
| Iceberg, últimos 7 dias, só `customer_id` + `amount` | 754,415 | — | 0.0026× |

Uma consulta de duas colunas no arquivo Parquet único lê **15.7%** desse arquivo (5,952,894 bytes), cerca de **49× menos** do que ler o CSV sem compressão. Um filtro de 7 dias na tabela Iceberg particionada por dia lê **7.75%** dos arquivos de dados (5,180,671 bytes), em linha com 7/90 = 7.78%. Juntando os dois — 7 dias e duas colunas — lê **754,415 bytes**, cerca de **388× menos** que o CSV.

## Projeções lineares (não são medições)

Método: `bytes_per_row` da execução de 2 milhões de linhas, vezes N. A execução de 10 milhões sustenta isso (bytes/linha concordaram em quatro algarismos). Tabelas reais mudam conforme tamanho de arquivo, compressão e compactação mudam, então trate estes números como ordem de grandeza.

### 1 bilhão de linhas

| Formato | Tamanho projetado | Armazenamento S3 Standard, US$/mês |
|---|---:|---:|
| CSV, sem compressão | 136.3 GiB (146,315,382,500 bytes) | $3.13 |
| TXT de largura fixa | 258.2 GiB (277,252,510,500 bytes) | $5.94 |
| JSON Lines | 321.6 GiB (345,315,312,000 bytes) | $7.40 |
| CSV gzip (nível 6) | 30.33 GiB (32,569,507,500 bytes) | $0.70 |
| Tabela Iceberg | 31.36 GiB (33,677,668,500 bytes) | $0.72 |
| Parquet zstd (arquivo único) | 17.63 GiB (18,933,631,000 bytes) | $0.41 |

### 1 TiB de CSV sem compressão

1 TiB são 2⁴⁰ bytes (1,099,511,627,776), a unidade que a AWS usa para “1 TB = 1024 GB” na página de preço do S3. A 146.32 bytes/linha isso é cerca de **7,514,668,718 linhas** deste esquema.

| Formato | Tamanho projetado | Armazenamento S3 Standard, US$/mês |
|---|---:|---:|
| CSV, sem compressão | 1.000 TiB (1,099,511,627,776 bytes) | $23.55 |
| TXT de largura fixa | 1.895 TiB (2,083,460,767,530 bytes) | $44.63 |
| JSON Lines | 2.360 TiB (2,594,930,172,794 bytes) | $55.58 |
| CSV gzip (nível 6) | 227.9 GiB (244,749,059,158 bytes) | $5.24 |
| Tabela Iceberg | 235.7 GiB (253,076,521,959 bytes) | $5.42 |
| Parquet zstd (arquivo único) | 132.5 GiB (142,279,964,586 bytes) | $3.05 |

Só armazenamento, primeiro nível de 50 TB: manter 1 TiB de CSV cru no S3 Standard seria **$23.55/month**. O mesmo dado como um arquivo Parquet único seria **$3.05/month** (cerca de $20.50 a menos). Como tabela Iceberg com este padrão de escrita, **$5.42/month** (cerca de $18.13 a menos). Requisições, transferência de dados e imposto não entram.

## Fonte do preço S3

- Preço de lista público usado: **$0.023 per GB - first 50 TB / month of storage used**.
- Região / classe: primeiros 50 TB / mês, S3 Standard, US East (N. Virginia).
- Unidade de cobrança: 1 GB = 2^30 bytes, 1 TB = 1024 GB (página de preço S3 da AWS).
- Página legível: https://aws.amazon.com/s3/pricing/
- Fonte legível por máquina (AWS Price List API, oferta us-east-1, versão 20261121172919, publicationDate 2026-11-21T17:29:19Z): https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/20261121172919/us-east-1/index.json
- SKU `WP9ANXZGBYYSGJEA`, usage type `TimedStorage-ByteHrs`, volume type Standard, storage class General Purpose. Data efetiva daquele termo da oferta: 2026-11-01.
- Conferido em 2026-11-24. Preços mudam; a página de preço e o arquivo de oferta corrente é que mandam. Requisições, transferência de dados, imposto e qualquer outra classe de armazenamento.

## Layout de largura fixa

Este é um registro estilo CNAB documentado e inventado para a comparação, não o layout oficial de banco nenhum. UTF-8, um registro por linha (`\n`). Campos de texto são preenchidos com espaço à direita e truncados se estourarem (nenhum estourou). Campos numéricos são preenchidos com zero à esquerda. O comprimento do registro é 276 caracteres mais a quebra de linha; acentos em nomes e cidades acrescentam um pouco mais de um byte extra por linha em média (277.25 bytes/linha medidos).

| Campo | Largura | Preenchimento |
|---|---:|---|
| `transaction_id` | 15 | esquerda, espaços |
| `ts` | 26 | esquerda, espaços |
| `customer_id` | 12 | direita, zeros |
| `customer_cpf` | 14 | esquerda, espaços |
| `card_number` | 19 | esquerda, espaços |
| `merchant_id` | 12 | direita, zeros |
| `merchant_name` | 80 | esquerda, espaços |
| `merchant_category` | 16 | esquerda, espaços |
| `city` | 40 | esquerda, espaços |
| `state` | 2 | esquerda, espaços |
| `amount` | 15 | direita, zeros |
| `currency` | 3 | esquerda, espaços |
| `channel` | 12 | esquerda, espaços |
| `status` | 10 | esquerda, espaços |

O compresslevel do gzip é 6, igual a `gzip file.csv` no gzip GNU (o padrão do Python é 9).

## Ressalvas

- As taxas de compressão foram medidas em dado sintético Faker/NumPy (nomes pt_BR, CPFs, números de cartão, categorias). Dado real de produção tem outra entropia e comprime diferente — trate as razões como ordem de grandeza, não como garantia.
- O CSV usa o módulo csv do Python (aspas compatíveis com Excel). O gzip usa compresslevel=6, o padrão do gzip GNU.
- O layout TXT posicional é um registro estilo CNAB documentado e inventado para esta comparação, não o layout oficial de um banco específico.
- O arquivo Parquet único é zstd + dicionário + estatísticas, gravado com PyArrow. A tabela Iceberg usa o mesmo esquema, partição day(ts), ordem `(customer_id, ts)` e write.parquet.compression-codec=zstd de `api/seed/generate.py`. O tamanho Iceberg inclui arquivos de dados, manifests, JSON de metadados e o catálogo SQLite local.
- As projeções (1 bilhão de linhas, 1 TiB de CSV cru) são lineares (bytes por linha × N) e não são medições.
- Os custos S3 são só armazenamento, no preço de lista público do S3 Standard em us-east-1, primeiro nível de 50 TB. Excluem requisições, transferência, imposto e descontos.
- O número de 7 dias + duas colunas (754,415 bytes) foi calculado no mesmo warehouse Iceberg de 2M do script, logo depois da execução; está incluído aqui e não foi escrito pelo próprio `storage_compare.py`.

## O que o script grava

`loadtest/storage_compare.py` grava um `summary.json` legível por máquina (e um `result.json` por execução) em `--out` (padrão `/tmp/storage-compare`). A partir da raiz do repositório, `pip install -r loadtest/requirements.txt` instala a stack da API mais `duckdb` e o extra SQLite do PyIceberg (`sqlalchemy`).

<a id="english"></a>

# Storage comparison: file-exchange formats vs the lake

Measured on 2026-11-24 (America/Sao_Paulo). Reproducible with:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r loadtest/requirements.txt
python loadtest/storage_compare.py --rows 2000000 --rows-large 10000000
```

The script reuses `api/seed/generate.py` (same schema, Faker pt_BR pools, Zipf customers, log-normal amounts, sort by `(customer_id, ts)`, seed `42`). The calendar window is pinned to 90 days ending exclusive of 2026-11-24, so the run does not depend on the clock. Batches of 250,000 rows, 50,000 customers, 5,000 merchants.

Hardware: shared 8-core Intel Xeon, 16 GiB RAM, x86_64 Linux, Python 3.13.5, PyArrow 26.0.1, DuckDB 2.0.1, PyIceberg 0.12.0. The bulky files were written under `/tmp/storage-compare` and are not in the repo.

## 2 million rows (primary, same scale as the load test)

Elapsed: 49.6 s to generate and write every format, including the Iceberg table.

| Format | Bytes | Size | Bytes/row | vs uncompressed CSV |
|---|---:|---:|---:|---:|
| CSV, uncompressed (UTF-8, header, Excel-style quoting) | 292,630,765 | 279.1 MiB | 146.32 | 1.000× |
| Fixed-width / positional TXT (CNAB-style, see layout below) | 554,505,021 | 528.8 MiB | 277.25 | 1.895× |
| JSON Lines (one object per row; what a naive API would return) | 690,630,624 | 658.6 MiB | 345.32 | 2.360× |
| CSV gziped at level 6 (GNU gzip default) | 65,139,015 | 62.12 MiB | 32.57 | 0.223× |
| Iceberg table (zstd, day(ts) partitions, metadata included) | 67,355,337 | 64.24 MiB | 33.68 | 0.230× |
| Single Parquet file (zstd, dictionary, statistics) | 37,867,262 | 36.11 MiB | 18.93 | 0.129× |

Iceberg breakdown (local SqlCatalog warehouse): 720 Parquet data files totaling 66,836,438 bytes (63.74 MiB), plus 518,899 bytes (506.7 KiB) of metadata (manifests, metadata JSON, SQLite catalog). 746 files in total. No values were truncated in the positional layout.

The single Parquet file is 7.73× smaller than uncompressed CSV (12.9% of the CSV). The Iceberg table is 4.34× smaller than the CSV (23.0%), almost the same size as gziped CSV, because the table is partitioned by day and committed every 250,000 rows: 720 data files of roughly 90 KiB each. Periodic compaction would close most of the gap to the single-file Parquet. Gzip level 6 shrinks the CSV to 22.3% of its uncompressed size.

### Confirmation on the project's object storage

The MinIO-compatible warehouse of the Docker stack was emptied, then `make`-equivalent `docker compose --profile tools run --rm seed --rows 2000000 --days 90 --replace` was run (the project's generator, `datetime.now()` window, not the pinned date). Measured by summing `mc ls --recursive --json` object sizes:

- Warehouse total: **67,370,345 bytes** (64.25 MiB, 33.69 bytes/row)
- Data files: 66,896,858 bytes, 720 objects
- Metadata: 473,487 bytes, 25 objects

That matches the local Iceberg table from the pinned-date script run (67,355,337 bytes) to well under 0.1%. The stack was stopped with `docker compose down` afterwards. Trino / the API were not started; only MinIO, the Iceberg REST catalog and the seed job were needed for this measurement.

## 10 million rows (linearity check)

Elapsed: 231.63 s. Bytes/row are essentially identical to the 2M run, so a linear projection is justified for this synthetic dataset.

| Format | Bytes | Size | Bytes/row | vs 2M bytes/row |
|---|---:|---:|---:|---:|
| CSV, uncompressed (UTF-8, header, Excel-style quoting) | 1,463,127,524 | 1.363 GiB | 146.31 | 1.0000× |
| Fixed-width / positional TXT (CNAB-style, see layout below) | 2,772,530,324 | 2.582 GiB | 277.25 | 1.0000× |
| JSON Lines (one object per row; what a naive API would return) | 3,453,127,383 | 3.216 GiB | 345.31 | 1.0000× |
| CSV gziped at level 6 (GNU gzip default) | 325,702,346 | 310.6 MiB | 32.57 | 1.0000× |
| Iceberg table (zstd, day(ts) partitions, metadata included) | 337,246,995 | 321.6 MiB | 33.72 | 1.0014× |
| Single Parquet file (zstd, dictionary, statistics) | 189,118,105 | 180.4 MiB | 18.91 | 0.9988× |

Iceberg at 10M: 3600 data files, 318.7 MiB of Parquet plus 2.960 MiB of metadata.

## How much a query has to read

Physical bytes read for a column projection were taken from the Parquet footer (`total_compressed_size` of each column chunk). That is what a well-behaved engine actually reads from disk, ignoring a tiny footer/magic overhead. DuckDB was used only as a sanity check that a two-column query returns the same row count (2,000,000 and 10,000,000).

On the 2 million row set:

| Scan | Bytes | vs full Parquet file | vs uncompressed CSV |
|---|---:|---:|---:|
| Uncompressed CSV, all columns (the file) | 292,630,765 | — | 1.000× |
| Single Parquet, all 14 columns | 37,853,670 | 1.000× | 0.129× |
| Single Parquet, only `customer_id` + `amount` | 5,952,894 | 0.157× | 0.020× |
| Iceberg data files, all columns | 63,952,479 | — | 0.219× |
| Iceberg data files, only `customer_id` + `amount` | 9,735,800 | 0.152 of Iceberg columns | 0.033× |
| Iceberg, last 7 populated days, all columns in those files | 5,180,671 | 0.078 of Iceberg data files (56 files, 7 of 90 days) | 0.018× |
| Iceberg, last 7 days, only `customer_id` + `amount` | 754,415 | — | 0.0026× |

A two-column query on the single Parquet file reads **15.7%** of that file (5,952,894 bytes), about **49× less** than reading the uncompressed CSV. A 7-day date filter on the day-partitioned Iceberg table reads **7.75%** of the data files (5,180,671 bytes), in line with 7/90 = 7.78%. Combining both — 7 days and two columns — reads **754,415 bytes**, about **388× less** than the CSV.

## Linear projections (not measurements)

Method: `bytes_per_row` from the 2 million row run, times N. The 10 million row run supports this (bytes/row agreed to four figures). Real tables change as file sizes, compression and compaction change, so treat these as order-of-magnitude figures.

### 1 billion rows

| Format | Projected size | S3 Standard storage, US$/month |
|---|---:|---:|
| CSV, uncompressed | 136.3 GiB (146,315,382,500 bytes) | $3.13 |
| Fixed-width TXT | 258.2 GiB (277,252,510,500 bytes) | $5.94 |
| JSON Lines | 321.6 GiB (345,315,312,000 bytes) | $7.40 |
| CSV gzip (level 6) | 30.33 GiB (32,569,507,500 bytes) | $0.70 |
| Iceberg table | 31.36 GiB (33,677,668,500 bytes) | $0.72 |
| Parquet zstd (single file) | 17.63 GiB (18,933,631,000 bytes) | $0.41 |

### 1 TiB of uncompressed CSV

1 TiB is 2⁴⁰ bytes (1,099,511,627,776), the unit AWS uses for “1 TB = 1024 GB” on the S3 pricing page. At 146.32 bytes/row that is about **7,514,668,718 rows** of this schema.

| Format | Projected size | S3 Standard storage, US$/month |
|---|---:|---:|
| CSV, uncompressed | 1.000 TiB (1,099,511,627,776 bytes) | $23.55 |
| Fixed-width TXT | 1.895 TiB (2,083,460,767,530 bytes) | $44.63 |
| JSON Lines | 2.360 TiB (2,594,930,172,794 bytes) | $55.58 |
| CSV gzip (level 6) | 227.9 GiB (244,749,059,158 bytes) | $5.24 |
| Iceberg table | 235.7 GiB (253,076,521,959 bytes) | $5.42 |
| Parquet zstd (single file) | 132.5 GiB (142,279,964,586 bytes) | $3.05 |

Storage only, first 50 TB tier: keeping 1 TiB of raw CSV in S3 Standard would be **$23.55/month**. The same data as a single Parquet file would be **$3.05/month** (about $20.50 less). As an Iceberg table with this write pattern, **$5.42/month** (about $18.13 less). Requests, data transfer and tax are not included.

## S3 price source

- Public list price used: **$0.023 per GB - first 50 TB / month of storage used**.
- Region / class: first 50 TB / month, S3 Standard, US East (N. Virginia).
- Billing unit: 1 GB = 2^30 bytes, 1 TB = 1024 GB (AWS S3 pricing page).
- Human-readable page: https://aws.amazon.com/s3/pricing/
- Machine-readable source (AWS Price List API, us-east-1 offer, version 20261121172919, publicationDate 2026-11-21T17:29:19Z): https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/20261121172919/us-east-1/index.json
- SKU `WP9ANXZGBYYSGJEA`, usage type `TimedStorage-ByteHrs`, volume type Standard, storage class General Purpose. Effective date on that offer term: 2026-11-01.
- Checked on 2026-11-24. Prices change; the pricing page and the current offer file are authoritative. Requests, data transfer, tax, and any other storage class.

## Fixed-width layout

This is a documented CNAB-style record invented for the comparison, not any bank's official layout. UTF-8, one record per line (`\n`). Text fields are space-padded on the right and truncated if they overflow (none did). Numeric fields are zero-padded on the left. Record length is 276 characters plus the newline; accents in names/cities add a little over one extra byte per row on average (277.25 bytes/row measured).

| Field | Width | Padding |
|---|---:|---|
| `transaction_id` | 15 | left, spaces |
| `ts` | 26 | left, spaces |
| `customer_id` | 12 | right, zeros |
| `customer_cpf` | 14 | left, spaces |
| `card_number` | 19 | left, spaces |
| `merchant_id` | 12 | right, zeros |
| `merchant_name` | 80 | left, spaces |
| `merchant_category` | 16 | left, spaces |
| `city` | 40 | left, spaces |
| `state` | 2 | left, spaces |
| `amount` | 15 | right, zeros |
| `currency` | 3 | left, spaces |
| `channel` | 12 | left, spaces |
| `status` | 10 | left, spaces |

gzip compresslevel is 6, matching `gzip file.csv` on GNU gzip (Python's default is 9).

## Caveats

- Compression ratios were measured on synthetic Faker/NumPy data (pt_BR names, CPFs, card numbers, categories). Real production data has different entropy and will compress differently — treat the ratios as order-of-magnitude, not a guarantee.
- CSV uses Python's csv module (Excel-compatible quoting). gzip uses compresslevel=6, the GNU gzip default.
- The positional TXT layout is a documented CNAB-style record invented for this comparison, not a specific bank's official layout.
- The single Parquet file is zstd + dictionary + statistics, written with PyArrow. The Iceberg table uses the same schema, day(ts) partition, (customer_id, ts) sort order and write.parquet.compression-codec=zstd as api/seed/generate.py. Iceberg size includes data files, manifests, metadata JSON and the local SQLite catalog.
- Projections (1 billion rows, 1 TiB of raw CSV) are linear (bytes per row × N) and are not measurements.
- S3 costs are storage only, at the public S3 Standard list price for us-east-1, first 50 TB tier. They exclude requests, transfer, tax, and discounts.
- The 7-day + two-column figure (754,415 bytes) was computed from the same 2M Iceberg warehouse as the script, immediately after the run; it is included here and was not written by `storage_compare.py` itself.

## What the script writes

`loadtest/storage_compare.py` writes a machine-readable `summary.json` (and per-run `result.json`) under `--out` (default `/tmp/storage-compare`). From the repo root, `pip install -r loadtest/requirements.txt` installs the API stack plus `duckdb` and the PyIceberg SQLite extra (`sqlalchemy`).
