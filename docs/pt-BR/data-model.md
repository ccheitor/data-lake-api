# Modelo de dados

Uma tabela Iceberg, `payments.transactions`, no catálogo `iceberg`. O namespace vem de `TRINO_SCHEMA` (padrão `payments`). O gerador sempre nomeia a tabela `transactions`. O `TABLE_NAME` da API tem o mesmo padrão.

A tabela é Iceberg formato versão 2, Parquet, compressão ZSTD. Quem escreve é o PyIceberg (`python -m seed.generate`). Quem lê é o Trino e, para arquivos que a API já exportou, qualquer coisa que leia Parquet.

## Colunas

| # | Coluna | Tipo Iceberg | Nula | Notas |
|---|---|---|---|---|
| 1 | `transaction_id` | string | não | `tx_` mais um id com zeros à esquerda. Único por linha |
| 2 | `ts` | timestamp | não | Hora do evento, sem fuso. Fonte da partição |
| 3 | `customer_id` | long | não | Índice a partir de 1 no pool de clientes |
| 4 | `customer_cpf` | string | não | CPF brasileiro do Faker, `000.000.000-00`. Sensível |
| 5 | `card_number` | string | não | Dígitos do Faker. Sensível |
| 6 | `merchant_id` | long | não | Índice a partir de 1 no pool de estabelecimentos |
| 7 | `merchant_name` | string | não | Nome da empresa |
| 8 | `merchant_category` | string | não | Uma de 12 categorias, fixa por estabelecimento |
| 9 | `city` | string | não | Cidade do estabelecimento |
| 10 | `state` | string | não | Sigla do estado (`estado_sigla`) |
| 11 | `amount` | decimal(12, 2) | não | Valor do ticket. A API devolve como string |
| 12 | `currency` | string | não | Sempre `BRL` neste gerador |
| 13 | `channel` | string | não | `pos`, `ecommerce`, `contactless`, `recurring` |
| 14 | `status` | string | não | `approved`, `declined`, `reversed` |

`customer_cpf` e `card_number` são as únicas colunas que a API mascara. As outras doze são selecionadas para todo papel. O canal é gravado e devolvido, e não é filtro da API HTTP. Categoria, status e um valor mínimo são.

Os ids de campo são estáveis (1–14). Não reutilize um id para outra coluna se evoluir o esquema; o Iceberg identifica colunas pelo id.

## Particionamento

```text
partition by day(ts)   -- nome do campo de partição ts_day
```

Uma consulta com `ts >= date_from` e `ts < o dia seguinte a date_to` só abre as partições daquelas datas. Por isso `GET /v1/transactions` exige intervalo de datas e recusa mais de 31 dias. Um lookup filtra só `customer_id`, então não ganha essa poda. O cache existe por causa disso.

O gerador sorteia cada timestamp de modo uniforme ao longo do número de dias pedido, terminando na hora atual do contêiner, com a janela começando à meia-noite local de `DAYS` atrás. Linhas nas bordas podem cair no primeiro ou no último dia civil conforme o relógio.

## Ordem de sort

Cada arquivo de dados é gravado ordenado por `customer_id` e depois `ts` (ambas transformações identity, ascendentes). O PyIceberg registra essa ordem na tabela. Dentro de um lote o gerador também ordena a tabela Arrow do mesmo jeito antes do `append`, então as estatísticas de row group do Parquet em `customer_id` ficam apertadas.

O Trino então pode pular row groups cujo min/max de `customer_id` não contém o lookup. Ele ainda tem de considerar cada partição diária. O teste de carga publicado mediu esse custo restante: um lookup direto com 10 usuários concorrentes ficou em torno de 2.5 segundos. Números e hardware estão em [Desempenho](performance.md).

## Como as linhas são geradas

`api/seed/generate.py` monta os pools uma vez e depois amostra com NumPy. O Faker é `pt_BR`, então nomes, cidades, CPFs e siglas parecem brasileiros. A semente do RNG e a do Faker têm padrão 42, então um dado `--rows`, `--customers`, `--merchants` e `--seed` repetem os mesmos pools. Os ids de transação também incluem um deslocamento baseado no tempo, então os ids não são estáveis entre execuções. `make seed` passa `--replace`, que derruba a tabela primeiro.

Pools:

- Clientes: CPF único (`fake.unique.cpf()`) e um número de cartão cada.
- Estabelecimentos: nome de empresa, cidade, estado e uma categoria escolhida de modo uniforme entre os 12 nomes.
- Cada estabelecimento recebe uma média log-normal do ticket. Viagem, eletrônicos, educação, saúde e contas ficam mais altos; o resto usa 4.0. Os valores são em centavos, cortados na faixa 1.00 a 50,000.00, e gravados como `decimal(12,2)`.

Amostragem por linha:

- O índice do cliente é Zipf com expoente 1.3, módulo o tamanho do pool, então poucos clientes são pesados.
- O índice do estabelecimento é uniforme.
- Probabilidades de canal: pos 0.45, ecommerce 0.30, contactless 0.20, recurring 0.05.
- Probabilidades de status: approved 0.93, declined 0.05, reversed 0.02.

Os commits são um append Iceberg por lote. O lote padrão é 250,000 linhas, o que mantém a memória plana quando `ROWS` está na casa dos milhões. O limite do contêiner de seed é 2 GB.

## Opções do gerador

`make seed ROWS=2000000 DAYS=90` vira:

```text
python -m seed.generate --rows 2000000 --days 90 --replace
```

| Flag | Padrão | Significado |
|---|---|---|
| `--rows` | `SEED_ROWS` ou 1,000,000 | Quantas transações acrescentar |
| `--days` | 90 | Janela de histórico que termina agora |
| `--customers` | 50000 | Tamanho do pool de clientes |
| `--merchants` | 5000 | Tamanho do pool de estabelecimentos |
| `--batch` | 250000 | Linhas por commit Iceberg |
| `--seed` | 42 | Semente do NumPy e do Faker |
| `--replace` | desligado | Derruba a tabela se existir e depois cria |

`make seed` sempre liga `--replace` e não passa os tamanhos dos pools. Para mudá-los, rode o serviço direto. O comando está em [Configuração](configuration.md).

Criar a tabela define as propriedades `write.parquet.compression-codec=zstd` e `format-version=2`. Se a tabela já existe e você não passa `--replace`, o gerador faz append e não altera a especificação. Uma especificação de partição diferente exige `--replace` ou um drop manual.

## O que o arquivo de exportação contém

Uma exportação não é a tabela Iceberg. É um objeto Parquet novo no bucket `exports`, caminho `AAAA/MM/DD/{job_id}.parquet` em UTC, ZSTD, com as 14 colunas da API. Os tipos nesse arquivo são timestamp (microssegundos), int64 para os dois ids, decimal(12, 2) para `amount` e string para o resto. As colunas sensíveis já estão mascaradas ou não, conforme o papel de quem chamou, porque o worker grava o que o Trino devolveu.

O bucket de exports não é uma tabela Iceberg. Nada no catálogo aponta para ele. A regra de ciclo de vida de um dia nesse bucket apaga o objeto; não afeta `warehouse`.
