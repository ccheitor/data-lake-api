# Segurança

Esta demonstração autentica chamadores, mascara duas colunas e grava uma linha de auditoria. Ela não cifra a rede, não limita taxa e não esconde as portas do armazenamento e do Trino. Trate os padrões como uso local.

Troque `MINIO_ROOT_PASSWORD` e `API_KEYS` no `.env` antes de a máquina aceitar tráfego de qualquer lugar que não seja a sua. Os valores de `.env.example` estão publicados no repositório.

## Chaves de API

`API_KEYS` é uma lista separada por vírgulas de `segredo:papel`. O processo lê isso uma vez, na importação, para um dicionário. Uma chave sem papel depois dos dois-pontos fica como `analyst`.

O cabeçalho é `X-API-Key`. A comparação é uma busca exata no dicionário. Não há hash na hora da checagem: o processo guarda as chaves cruas na memória, o que é normal para uma lista estática de desenvolvimento e errado para um conjunto grande de produção.

Na falha a resposta é `401` com `detail` `missing or invalid X-API-Key`. O corpo não diz se o cabeçalho faltou ou se estava errado.

O id gravado nos logs é `SHA-256(chave)` truncado em 12 caracteres hexadecimais (`api/app/security.py`). Duas chaves diferentes podem, em teoria, compartilhar o prefixo; 12 caracteres hex são uma impressão digital para um log de demonstração, não uma chave primária em que se deva apoiar autorização. A autorização usa a chave crua na busca e depois o papel no `Principal`.

A posse do export usa a mesma impressão. `GET /v1/exports/{id}` devolve o job quando `job.owner == key_id do chamador` ou `role == admin`. Qualquer outro caso, inclusive quando o job existe e é de outra pessoa, é `404`. A resposta omite `owner`.

## Papéis e mascaramento

`can_see_pii` é `role == "admin"`. Qualquer outro papel é mascarado, mesmo um nome que você invente em `API_KEYS`.

O mascaramento é SQL, não um pós-processamento em Python. A lista do analista:

```sql
concat('**** **** **** ', substr(card_number, -4)) AS card_number,
regexp_replace(customer_cpf, '^\d{3}\.\d{3}\.\d{3}', '***.***.***') AS customer_cpf
```

A lista do admin usa os nomes das colunas. A mesma lista serve à consulta paginada, ao lookup e à exportação, então um download Parquet não fura a máscara. A chave do Redis inclui o papel, então os dois payloads nunca ficam sob a mesma chave.

O que o mascaramento não faz:

- Não cobre nome do estabelecimento, cidade nem id do cliente. Isso continua visível para todo papel.
- Não aplica filtro de linha. Um analista pode pedir qualquer `customer_id`.
- Não vale para um cliente Trino que conecta na porta 8080 no mesmo catálogo. O Trino deste Compose não tem senha nem controle de acesso de sistema. A máscara só existe no SQL que esta API escolhe enviar.

Uma regra de produção pertence ao motor (controle de acesso do Trino em arquivo, Apache Ranger ou OPA), para que todo cliente a receba. Este serviço então deixaria de duplicar a expressão.

## Log de auditoria

O `AuditMiddleware` grava uma linha JSON por requisição, inclusive `/health` e 401, no logger `audit` (stdout e `AUDIT_LOG_PATH`). A propagação está desligada, então a linha não sai também pelo logger raiz.

Campos: `ts` (UTC), `request_id`, `key_id`, `client` (o par TCP imediato), `method`, `path`, `query`, `status`, `duration_ms`.

A query string é registrada por inteiro. Não coloque segredo nela. A chave de API só é lida do cabeçalho, e o valor do cabeçalho não é um campo do log.

No Compose o arquivo está no volume `audit-logs`, em `/var/log/api/audit.log`. Ele não é enviado a lugar nenhum. Só rotaciona se você acrescentar um rotator; o código usa um `FileHandler` simples em modo append. Os dois workers do uvicorn escrevem nesse arquivo.

`client` é o endereço da bridge Docker quando você chama a porta publicada, não o endereço real de quem chamou, a menos que haja um proxy na frente e a aplicação seja ensinada a confiar nele. O middleware não lê `X-Forwarded-For`.

## O que uma versão de produção acrescentaria

A demonstração executável para na lista acima. Uma implantação com dado real de cartão acrescentaria pelo menos:

- **TLS** na API e no endpoint de armazenamento usado nas URLs pré-assinadas. Este Compose é HTTP.
- **Rede privada.** Não publique 8080 (Trino), 6379 (Redis), 8181 (catálogo) nem 9001 (console). A porta 9000 só é necessária para downloads pré-assinados; prefira um endpoint HTTPS separado ou um caminho curto de gateway a deixar a porta S3 aberta. O guia da Oracle diz o mesmo.
- **Identidades de verdade.** Troque `API_KEYS` por client credentials OAuth2/OIDC, escopos por conjunto de dados e cotas por cliente num gateway. O item do roteiro está em [Roteiro](roadmap.md).
- **Guarda de segredos.** Chaves e a senha do MinIO vêm do `.env`. Um processo de produção deve lê-los de um cofre de segredos, não de um arquivo ao lado do clone.
- **Autorização além de dois papéis.** Filtros de linha (este cliente, este estabelecimento, esta região) impostos no Trino, mais máscaras de coluna na política do catálogo, para que uma porta Trino vazada não pule a API.
- **Limites de taxa e de payload** no gateway. Nada na API conta chamadas por chave. Os workers de exportação são um pool pequeno; não são uma cota.
- **Auditoria durável.** Armazenamento append-only com retenção, e uma revisão da query string para que filtros que sejam eles mesmos sensíveis possam ser hasheados ou omitidos.
- **Isolamento do job.** O estado da exportação no Redis não é autenticado na camada do Redis (não há senha neste Compose). Quem alcança o Redis lê registros de job e linhas em cache. O cache guarda o JSON já mascarado ou claro, conforme o papel que o preencheu.

Nada disso está implementado aqui. O código está organizado para que a lista do `SELECT` e o `Principal` sejam os dois lugares em que uma checagem de política posterior entraria.
