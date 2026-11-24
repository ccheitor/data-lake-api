# Roteiro

O repositório é uma máquina só de propósito. Os itens abaixo são o que se acrescentaria para a mesma API aguentar ingestão real, identidades reais e mais de um nó. Nenhum está implementado. Cada um substitui um atalho citado em [Arquitetura](architecture.md) ou [Segurança](security.md).

## Ingestão em fluxo com Kafka

Substituir `make seed` como forma de as linhas chegarem. Produtores gravam eventos de cartão no Kafka. Um sink (Flink, Spark Structured Streaming ou Kafka Connect com sink Iceberg) faz append na mesma tabela, particionada por `day(ts)`.

Um segundo consumidor atualiza o armazenamento de baixa latência que sustenta `GET /v1/customers/{id}/transactions`. A consulta pontual então serve dado com segundos de atraso, e o cache pode derrubar a chave quando chega um evento daquele cliente, em vez de esperar `CACHE_TTL_SECONDS`. O contrato HTTP não muda. O gerador pode continuar como produtor de eventos falsos para teste.

## OAuth2 e OIDC

Substituir `API_KEYS` por tokens de client credentials (Keycloak, Auth0, Entra ID ou qualquer provedor OIDC). Escopos mapeiam o que `role` faz hoje: um escopo vê colunas mascaradas, outro vê colunas claras, e a leitura de exportação é um escopo à parte. Um gateway de API (Kong, Envoy, APISIX) impõe limite de taxa e cota por cliente. O `key_id` do log de auditoria passa a ser o subject do token, ainda não o segredo.

Até isso existir, o cabeçalho e os dois papéis de [Segurança](security.md) são todo o sistema de identidade.

## Política no motor

Mover as expressões `concat` / `regexp_replace` de `api/app/security.py` para o controle de acesso do Trino (regras em arquivo, Apache Ranger ou OPA). Acrescentar filtros de linha lá também, para um cliente não pedir um `customer_id` arbitrário. Um catálogo (DataHub ou OpenMetadata) e um contrato de dados por conjunto ficam ao lado, para o esquema de 14 colunas não estar documentado só neste repositório.

A API enviaria `SELECT` das colunas lógicas e confiaria no motor para mascarar. As exportações continuam corretas porque rodam o mesmo SQL.

## Kubernetes e Helm

O chart Helm do Trino com workers em autoscaling, a API como Deployment com horizontal pod autoscaler, Redis via operador, e armazenamento de objetos que não seja um contêiner só. O pool de threads de exportação não sobrevive a um pod novo: troque-o por uma fila de verdade (Celery, RQ ou um tópico Kafka de jobs) cujos workers sejam outro Deployment. Prometheus e Grafana ficam com os gráficos de latência e taxa que o `make loadtest` imprime uma vez e esquece.

Num nó Always Free de 12 GB isso fica apertado. O [guia da Oracle](oracle-cloud-free.md) já diz que k3s ou um cluster OKE Basic cabe, e que o Compose deixa mais RAM para o Trino. Use Kubernetes aqui quando estiver praticando o empacotamento, não porque a demonstração de laptop precise.

## Uma interface

Metabase ou Streamlit em cima do Trino, para quem não vai chamar a API. Aponte o Metabase para o coordinator do Trino e para `iceberg.payments.transactions`. Ele vai emitir o próprio SQL, o que é mais um motivo para a máscara viver no Trino e não só nesta API. O Streamlit é a opção mais leve para uma página de filtros que chama `GET /v1/transactions` e mostra o cursor.

Qualquer uma das duas é um cliente. Deve usar uma chave de API ou, mais tarde, um token. Não deve receber a senha raiz do MinIO.

## Arrow Flight

Para quem quer colunas sem gravar um Parquet antes, sirva a exportação com Arrow Flight ou Flight SQL. O modelo de job pode ficar: a requisição ainda volta rápido, e o worker ainda lê o Trino em blocos, mas o cliente recebe lotes em vez de baixar um objeto. URLs pré-assinadas continuam a resposta certa para quem só quer um arquivo.

## Manutenção da tabela

O gerador faz append de um arquivo por lote e nunca compacta. Com o tempo, arquivos pequenos e snapshots se acumulam. Um job agendado (Airflow ou um cron na VM) deveria:

- Compactar arquivos de dados.
- Expirar snapshots de que você não precisa mais para voltar no tempo.
- Reescrever arquivos para honrar a ordem de sort depois de appends posteriores, para as estatísticas do lookup continuarem apertadas.

Uma segunda transformação de partição, `bucket(customer_id)`, faria o lookup sem cache abrir menos arquivos. Isso muda a especificação da tabela (derrubar e recriar, ou uma evolução de partição Iceberg) e vale a pena quando o cache não pode absorver a latência. Não remove o motivo do cache.

## Ainda em aberto

- TLS e uma política de rede que despublique Trino, Redis e o console. [Implantação](deployment.md) e [Segurança](security.md) descrevem o alvo; o arquivo Compose não impõe isso.
- Retenção de auditoria além de um arquivo num volume Docker.
- Um catálogo de verdade (JDBC/PostgreSQL ou Glue) no lugar do fixture REST e do arquivo SQLite.
