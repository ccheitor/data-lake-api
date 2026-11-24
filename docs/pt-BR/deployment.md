# Implantação

Quatro formas de rodar a mesma ideia, do laptop para fora. Só a primeira está especificada por completo neste repositório. As outras são formatos de implantação que reusam a API e o formato da tabela.

## Compose local

É o padrão, e o que o `make test` cobre. [Primeiros passos](getting-started.md) é o procedimento: Docker, `make up`, `make seed`, `make test`.

Mantenha local a menos que você tenha trocado o `.env`. As chaves de API publicadas e a senha do MinIO estão no repositório. O Compose publica Trino, Redis, o catálogo e o console de armazenamento no host.

Os dados ficam em três volumes Docker (`minio-data`, `catalog-data`, `audit-logs`). `make down` os conserva. `make clean` os apaga. Não há backup.

## Oracle Cloud Always Free

A stack cabe numa VM Ampere A1 gratuita porque toda imagem tem build arm64 e os limites dos contêineres somam cerca de 5.2 GB, abaixo dos 12 GB com que a capacidade Always Free A1 atual é descrita. O passo a passo, a tabela de limites, o aviso de recuperação por ociosidade e a opção de apontar o S3 para o OCI Object Storage estão em [oracle-cloud-free.md](oracle-cloud-free.md).

Leia essa página antes de dimensionar a VM. A franquia de CPU e RAM do nível gratuito mudou com o tempo; a nota daquele guia está datada de 2026-11-24 e manda rever a página Always Free da Oracle. Rode `make loadtest-all` na VM e acrescente uma linha em [loadtest/RESULTS.md](../../loadtest/RESULTS.md) em vez de supor que os números x86 se transferem.

Nessa VM, não deixe 8080, 6379, 8181 nem 9001 abertos para a internet. Defina `S3_PUBLIC_ENDPOINT` com um endereço que quem baixa alcança, se as exportações forem buscadas de fora.

## Uma VPS pequena

Qualquer VPS Linux com 4 vCPUs e 16 GB de RAM basta para o arquivo Compose como está escrito (o limite de 3 GB do Trino mais o resto da stack e o sistema). Dois vCPUs rodam e mostram vazão menor no Trino; meça em vez de estimar.

1. Instale Docker e Compose v2, mais `make` e `python3`.
2. Clone o repositório, copie `.env.example` para `.env` e troque `MINIO_ROOT_PASSWORD` e `API_KEYS`.
3. Defina `S3_PUBLIC_ENDPOINT` com a URL pública de base que os clientes usarão nos downloads.
4. Abra SSH a partir do seu IP, e abra só a porta da API (8000, ou 443 atrás de um proxy reverso). Deixe Trino, Redis, o catálogo e o console presos a localhost. Você pode tirar as portas de host em `docker-compose.yml` desses serviços e manter a rede Docker.
5. Coloque TLS na frente da API (Caddy ou um proxy parecido). URLs pré-assinadas também precisam de TLS se a porta de armazenamento for alcançável; senão, mantenha os downloads numa rede privada.
6. `make up`, depois `make seed` com uma quantidade de linhas que o disco aguente. O Parquet de 2 milhões de linhas na exportação publicada tinha 47.7 MB; os arquivos do lago são maiores que esse extrato único por causa do particionamento, mas ainda são pequenos perto de um disco de 50 GB. Observe o volume do Silo.
7. Habilite o Docker na inicialização (`systemctl enable docker`). Os serviços já usam `restart: unless-stopped`.

Backup é problema seu numa VPS única: faça snapshot do disco, ou aceite que `make clean` e um volume morto são perda de dado. O arquivo SQLite do catálogo e o bucket do warehouse precisam de backup juntos. Um catálogo sem os Parquet, ou arquivos sem o catálogo, não é uma tabela legível.

Esse arranjo é o arquivo Compose numa máquina alugada. Não passa de um processo Trino, e os workers de exportação ainda morrem com o processo da API.

## Esboço na AWS, orientado a custo

Dois esboços. Nenhum é um módulo deste repositório. Os dois mantêm Iceberg (ou pelo menos Parquet) como tabela e HTTP como interface. Preço muda; a comparação é sobre o que se paga, não uma cotação.

### S3, Athena e Lambda

Use quando as consultas são em rajada e a latência do Athena é aceitável.

- **S3** guarda o warehouse e o bucket de exportação. O Silo sai. As configurações PyIceberg do gerador (`s3.endpoint`, acesso path-style) passam a endpoints AWS de verdade, ou você grava com o AWS SDK e registra a tabela no Glue.
- **Glue Data Catalog** (ou outro catálogo Iceberg que o Athena aceite) substitui o fixture REST.
- **Athena** substitui o Trino na consulta paginada e na varredura da exportação. Você paga por dado lido. O particionamento por dia, que esta tabela já tem, é o que amarra essa conta ao intervalo do pedido em vez do lago inteiro. Um lookup que toca toda partição é uma consulta Athena cara; não coloque isso neste caminho.
- **Lambda** (ou um contêiner pequeno atrás do API Gateway) pode implementar as mesmas três rotas: montar o SQL, iniciar uma consulta Athena, guardar o id da consulta como esta API guarda um job, e devolver uma URL S3 pré-assinada quando a consulta termina. O próprio local de saída do Athena já é um objeto que se pode pré-assinar.
- **Redis ou DynamoDB** ainda é necessário se você quer a taxa de lookup com cache quente do teste de carga. O Athena não faz milhares de requisições por segundo com latência de milissegundo. O comportamento cache-aside de `api/app/main.py` é a peça a conservar; só o caminho de miss muda de Trino para Athena.

Forma do custo: quase nada parado, depois uma cobrança por consulta conforme os bytes lidos, mais armazenamento S3 e requisições de API, mais duração do Lambda. Um teste de carga como `make loadtest MODE=direct` contra o Athena leria as mesmas partições sem parar e é a ferramenta errada. O cenário de cache quente é o que continua barato, porque nunca chega ao Athena.

O que se perde: a UI do Trino no formato Compose, as peculiaridades exatas do dialeto SQL (o Athena deriva do Trino, mas não é esta imagem Trino 484) e misses abaixo de um segundo. O que se ganha: nenhuma instância para corrigir quando ninguém consulta.

### EC2, ou uma task ECS

Use quando você quer o comportamento deste repositório, inclusive os misses no Trino, e vai consultar com frequência bastante para um nó sempre ligado ser mais simples que preço por consulta.

- Uma instância EC2 (ou uma task ECS/Fargate com volume persistente) roda este arquivo Compose, com S3 no lugar do Silo se você preferir não operar um disco. Trocar Silo por S3 é a mesma mudança de endpoint da seção de armazenamento de objetos da Oracle em [oracle-cloud-free.md](oracle-cloud-free.md).
- ElastiCache Redis substitui o contêiner Redis se o cache precisar sobreviver a um restart da task. Aponte `REDIS_URL` para ele.
- Coloque um Application Load Balancer ou um proxy TLS pequeno na porta 443 só na frente da API.

Forma do custo: você paga a instância e o disco ou o S3 toda hora, inclusive de madrugada. Uma instância de 4 vCPU / 16 GB é a classe que combina com os limites do Compose. Isso custa mais que Athena parado e é mais previsível que uma carga Athena pesada em varredura. Some a transferência de dados se os downloads pré-assinados saem da região.

Não rode o teste de carga publicado contra um warehouse Athena de produção para "ver o TPS". A coluna de Trino direto nos resultados já é o aviso: 4 requisições/s com latência de vários segundos, num conjunto de 2 milhões de linhas, com o gerador na mesma máquina.

## O que permanece igual em todo lugar

- O contrato HTTP da [referência da API](api-reference.md): chaves, papéis, cursores, jobs de exportação, downloads pré-assinados.
- Particionamento Iceberg por `day(ts)` e a proibição de varreduras interativas sem limite.
- Um armazenamento de baixa latência na frente das consultas pontuais.
- Mascaramento na consulta que produz tanto o JSON quanto o Parquet, até uma política de catálogo substituí-lo.
