# Implantar na Oracle Cloud Infrastructure (OCI) Always Free

Toda imagem em `docker-compose.yml` publica um build `linux/arm64`, então a stack roda sem mudança
na forma Arm **Ampere A1** da OCI.

## Limites Always Free que importam aqui (conferidos em 2026-11-24)

Fonte: documentação da Oracle, *Always Free Resources* —
<https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm>

| Recurso | Franquia Always Free (por tenancy, só na home region) |
|---|---|
| Computação Ampere A1 (`VM.Standard.A1.Flex`, Arm) | Primeiras **1,500 OCPU-hours e 9,000 GB-hours por mês**, que a Oracle declara equivalentes a **2 OCPUs e 12 GB de RAM** no total. Uma VM com 2 OCPU / 12 GB, ou duas VMs com 1 OCPU cada. |
| Micro VMs AMD (`VM.Standard.E2.1.Micro`) | Até 2 VMs, 1/8 OCPU e 1 GB de RAM cada (pequenas demais para esta stack) |
| Block Volume | **200 GB** no total (boot + volumes de bloco), 5 backups de volume. Volume de boot padrão ~47-50 GB por VM. |
| Object Storage | **20 GB** no total (Standard + Infrequent Access + Archive) e **50,000 requisições de API por mês** para contas só Always Free (10 GB por nível para contas pagas / trial) |
| Transferência de dados de saída | 10 TB por mês |
| Balanceador de carga | 1 LB flexível (10 Mbps) e 1 Network Load Balancer |

> **Atenção:** muitos tutoriais ainda dizem "4 OCPU / 24 GB grátis". A página oficial agora diz
> **2 OCPU / 12 GB** (1,500 OCPU-hours / 9,000 GB-hours). Algumas tenancies mais antigas podem mostrar
> números diferentes — confira *Governance & Administration → Limits, Quotas and Usage* no console,
> e releia a página acima antes de planejar capacidade.

**A stack cabe?** Sim. Os limites de memória dos contêineres somam ~5.2 GB
(Trino 3 GB, API 768 MB, Iceberg REST 768 MB, armazenamento de objetos 512 MB, Redis 192 MB),
deixando espaço para o sistema numa VM de 2 OCPU / 12 GB. Com 2 OCPUs espere vazão menor do Trino
do que num laptop — meça com `make loadtest-all` e registre em `loadtest/RESULTS.md`.

## Ressalvas antes de começar

* **Recuperação por ociosidade.** A Oracle pode recuperar instâncias Always Free ociosas por mais de 7 dias: percentil 95 de CPU < 20%, rede < 20% **e** (para A1) memória < 20%. Uma JVM do Trino em execução mantém a memória bem acima de 20% de 12 GB, mas confira as métricas da instância e guarde backups — não trate uma VM gratuita como durável.
* **"Out of host capacity".** A capacidade A1 se esgota com frequência em regiões populares. A documentação sugere tentar outro domínio de disponibilidade ou tentar de novo mais tarde. A home region fica fixa no cadastro e os recursos Always Free só existem lá, então escolha uma home region menos ocupada se puder. Passar para Pay-As-You-Go mantém os recursos Always Free grátis e em geral melhora a disponibilidade, mas então tudo acima dos limites é cobrado — defina um alerta de orçamento e cotas de compartment.
* **Cota de requisições do Object Storage.** 50,000 requisições/mês é pouco para um lakehouse: Trino e o catálogo Iceberg fazem muitas chamadas GET/HEAD/LIST por consulta. Use o OCI Object Storage para exportações ou uma tabela pequena de demonstração, e mantenha o lago principal no volume de bloco da VM (opção A abaixo).
* **Segurança.** Troque toda senha padrão e chave de API no `.env`, exponha só a porta da API (de preferência atrás de HTTPS) e nunca abra Trino (8080), Redis (6379) nem o console de armazenamento (9001) para a internet.

## Opção A — uma VM Ampere A1 com Docker Compose (recomendada)

1. **Crie a VM**: Compute → Instances → Create. Imagem: Ubuntu 24.04 (aarch64) ou Oracle Linux 9. Forma: `VM.Standard.A1.Flex`, **2 OCPU / 12 GB**. Volume de boot de 50 GB; opcionalmente anexe um volume de bloco (até o total de 200 GB) para dados do Docker. Acrescente a sua chave pública SSH.
2. **Rede**: na security list da subnet (ou numa NSG) permita TCP 22 a partir do seu IP e TCP 443 (ou 8000 para um teste rápido, restrito ao seu IP). Em imagens Ubuntu, a OCI também entrega regras de iptables no host — abra a porta lá também:
   ```bash
   sudo iptables -I INPUT 6 -p tcp --dport 8000 -j ACCEPT && sudo netfilter-persistent save
   ```
3. **Instale o Docker** (Ubuntu):
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER && newgrp docker
   sudo apt-get install -y make git python3
   ```
4. **Implante**:
   ```bash
   git clone https://github.com/ccheitor/data-lake-api.git && cd data-lake-api
   cp .env.example .env
   # edite .env: MINIO_ROOT_PASSWORD forte, API_KEYS novas,
   # S3_PUBLIC_ENDPOINT=http://<ip-publico-da-vm>:9000 (ou um hostname HTTPS) para as URLs pre-assinadas funcionarem de longe
   make up
   make seed ROWS=2000000
   make test
   ```
   Se os downloads pré-assinados precisam funcionar de fora, a porta 9000 também tem de ser alcançável (restrinja, ou coloque os dois atrás de um proxy reverso como o Caddy com HTTPS e encaminhe `/exports/*` para o armazenamento).
5. **Mantenha no ar**: todos os serviços têm `restart: unless-stopped`; habilite o Docker na inicialização com `sudo systemctl enable docker`.

## Opção B — Kubernetes (cluster OKE Basic ou k3s)

* **OKE**: o plano de controle de um cluster **Basic** é grátis; você paga só os nós workers, então mantenha o node pool dentro da franquia Always Free A1 (por exemplo 2 nós × 1 OCPU / 6 GB, ou um nó de 2 OCPU / 12 GB). O console cria um cluster *Enhanced* (pago) por padrão, a menos que você marque a opção Basic (CLI: `--type BASIC_CLUSTER`). Os node pools sofrem os mesmos problemas de capacidade A1. Documentação: <https://docs.oracle.com/en-us/iaas/Content/ContEng/Tasks/contengcomparingenhancedwithbasicclusters_topic.htm>
* **k3s** na VM A1 é mais leve e evita peças extras: `curl -sfL https://get.k3s.io | sh -`. Depois implante o Trino com o chart Helm oficial (`helm repo add trino https://trinodb.github.io/charts`), o Redis com a imagem oficial `redis:7-alpine` como StatefulSet, e a API como Deployment. Com só 12 GB, rode o Trino como um coordinator que também agenda trabalho (`node-scheduler.include-coordinator=true`).
* O Kubernetes acrescenta ~1 GB de overhead; em 12 GB cabe, mas a opção A deixa mais memória para o Trino. Use a opção B sobretudo para praticar Kubernetes/Helm.

## Usar OCI Object Storage no lugar do MinIO

O OCI Object Storage tem uma **Amazon S3 Compatibility API**
(<https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/s3compatibleapi.htm>):

* Endpoint (path style): `https://<namespace>.compat.objectstorage.<region>.oci.customer-oci.com` (`<namespace>` é o namespace de Object Storage da tenancy, mostrado na página de detalhes do bucket).
* Credenciais: crie uma **Customer Secret Key** (Profile → User settings → Customer secret keys). Ela dá um par access key / secret key; o segredo aparece uma vez só. Máximo de 2 por usuário.
* Região: use o identificador de região da OCI (por exemplo `sa-saopaulo-1`); só SigV4.

Mudanças neste projeto:

1. Crie os buckets `warehouse` e `exports` na home region (opcionalmente uma regra de ciclo de vida para apagar exports depois de 1 dia). Remova os serviços `minio` e `minio-init` do compose.
2. `.env`: defina `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` com o par da Customer Secret Key (eles são mapeados para `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`), e `S3_PUBLIC_ENDPOINT=https://<namespace>.compat.objectstorage.<region>.oci.customer-oci.com`.
3. Em `docker-compose.yml`, troque `http://minio:9000` por esse mesmo endpoint HTTPS em `iceberg-rest` (`CATALOG_S3_ENDPOINT`), `api` e `seed` (`S3_ENDPOINT`), e defina `AWS_REGION` com a sua região.
4. `trino/catalog/iceberg.properties`: `s3.endpoint=` o mesmo endpoint, `s3.region=<region>`, mantenha `s3.path-style-access=true`.

Lembre a franquia **20 GB / 50,000 requisições por mês**: serve para exportações e uma demonstração pequena, não para teste de carga de um lago. Um híbrido funciona bem: lago no volume de bloco da VM (Silo/MinIO), exportações no OCI Object Storage, para os links pré-assinados serem servidos pela OCI em HTTPS sem abrir a porta 9000 na VM.
