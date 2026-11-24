# Primeiros passos

Suba a stack numa máquina com Docker, carregue uma tabela pequena e chame cada endpoint. O primeiro caminho que funciona é:

```bash
git clone https://github.com/ccheitor/data-lake-api.git
cd data-lake-api
make up
make seed ROWS=200000
make test
```

`make up` constrói a imagem da API, sobe a stack e consulta `GET /health` até Trino, Redis e armazenamento de objetos responderem ok. O Trino costuma levar 30–60 segundos. O script de espera desiste depois de 5 minutos.

`make seed` derruba e recria `iceberg.payments.transactions` e grava linhas sintéticas. `ROWS=200000` basta para explorar. O padrão, `ROWS=2000000`, é o que o teste de carga publicado usou.

`make test` confere autenticação, paginação, mascaramento, o cache e um download Parquet pré-assinado.

## Pré-requisitos

Você precisa do Docker Engine com Compose v2, `make`, `curl` e Python 3 (o teste de fumaça usa o Python para ler JSON). O Git só é necessário para clonar. Reserve cerca de 6 GB de RAM livre e alguns GB de disco para as imagens. Os contêineres em execução estão limitados a cerca de 5.2 GB no total.

| Ambiente | O que instalar | Notas |
|---|---|---|
| Linux | Docker Engine e o plugin Compose, mais `make`, `curl`, `python3` | Se o usuário não está no grupo `docker`, prefixe os alvos com `DOCKER='sudo docker'` |
| macOS (Intel ou Apple Silicon) | Docker Desktop, Xcode Command Line Tools (`make`, `git`) | Dê ao Docker Desktop pelo menos 8 GB de RAM. As imagens são multi-arch, então Apple Silicon funciona |
| Windows | WSL2 com Ubuntu, depois Docker Desktop no backend WSL2 | Rode git, make e o shell dentro do Ubuntu. Não clone o repositório em `/mnt/c` |

Toda imagem publica `linux/amd64` e `linux/arm64`. O mesmo arquivo Compose roda num laptop x86 e numa VM Arm.

### Linux

Ubuntu ou Debian:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
sudo apt-get install -y make git python3 curl
```

Abra um shell novo para o grupo `docker` valer, e então `docker compose version` deve imprimir v2. Em outras distribuições, instale o Docker Engine pela documentação do Docker e confirme que `docker compose` funciona. O binário antigo `docker-compose` v1 em Python não é o que este repositório usa.

### macOS

Instale o [Docker Desktop](https://www.docker.com/products/docker-desktop/) e as ferramentas de linha de comando (`xcode-select --install`), que trazem `git` e `make`.

No Docker Desktop, abra Settings, Resources, e defina a memória em 8 GB ou mais. O contêiner do Trino sozinho está limitado a 3 GB. Apple Silicon puxa os manifestos arm64; não é preciso definir uma plataforma.

`make loadtest` roda o k6 com `--network host`. Esse modo combina com a VM Linux, não com o Docker Desktop. Num Mac, instale o k6 no host (`brew install k6`) e siga [Desempenho](performance.md), ou rode o arquivo Locust no host. `make up`, `make seed` e `make test` não precisam de rede do host.

### Windows com WSL2

Os scripts são bash. `scripts/smoke-test.sh` usa o `date` do GNU. O caminho suportado no Windows é Ubuntu no WSL2, com a integração WSL2 do Docker Desktop ligada para essa distro.

1. Instale o WSL2 e o Ubuntu (`wsl --install` num PowerShell elevado e reinicie se o Windows pedir).
2. Instale o Docker Desktop e ligue **Use the WSL 2 based engine**. No Docker Desktop, Resources, WSL integration, habilite a distro Ubuntu.
3. Abra o terminal do Ubuntu, não o PowerShell, e instale o make:

   ```bash
   sudo apt-get update && sudo apt-get install -y make git python3 curl
   ```

4. Clone o repositório no sistema de arquivos Linux, por exemplo `~/src/data-lake-api`. Um checkout em `/mnt/c/...` é lento porque cada arquivo cruza para o disco do Windows.
5. Dê ao Docker Desktop pelo menos 8 GB de RAM (Settings, Resources). O WSL2 também respeita `%UserProfile%\.wslconfig` se você definir `memory=` ali. A stack não usa mais RAM do que a VM do WSL.

Depois rode os mesmos comandos `make up`, `make seed` e `make test` no shell do Ubuntu.

## Passo a passo

A partir de um clone limpo:

```bash
cp .env.example .env   # make up faz isso se .env não existe
make up
```

Quando voltar, estas URLs funcionam no host:

| URL | O que é |
|---|---|
| http://localhost:8000/docs | Swagger UI gerada pelo FastAPI |
| http://localhost:8000/health | Checagem das dependências, sem chave de API |
| http://localhost:9001 | Console do armazenamento. Usuário e senha vêm do `.env` |
| http://localhost:8080 | UI do Trino. Qualquer nome de usuário, sem senha nesta demonstração |

Carregue dados. O contêiner de seed é o perfil `tools`, então não fica rodando:

```bash
make seed ROWS=200000 DAYS=30
```

`DAYS` é a janela de histórico que termina hoje, no relógio do contêiner. Em seguida:

```bash
make test
```

Uma execução que passa imprime `PASS` para saúde, um 401 sem chave, a ordem do cursor, cartão mascarado e claro, um miss de cache seguido de hit, e um download pré-assinado. Termina com `All smoke tests passed.`

Faça uma chamada você mesmo. As datas precisam cobrir o dado que você acabou de gerar e ficar dentro de 31 dias:

```bash
FROM=$(date -d '-7 days' +%F 2>/dev/null || date -v-7d +%F)
TO=$(date +%F)
curl -s -H 'X-API-Key: dev-analyst-key' \
  "localhost:8000/v1/transactions?date_from=$FROM&date_to=$TO&limit=2"
```

As chaves padrão estão em `.env.example`: `dev-analyst-key` (mascarada) e `dev-admin-key` (cartão e CPF claros). Troque-as antes de qualquer máquina ficar alcançável numa rede em que você não confia.

Pare a stack com `make down` (os volumes ficam, então a tabela continua lá da próxima vez) ou `make clean` (apaga os volumes).

Os outros alvos do dia a dia estão em [Configuração](configuration.md).

## Solução de problemas

### `make up` sai antes de a API ficar saudável

`scripts/wait-healthy.sh` chama `http://localhost:8000/health` por até 5 minutos. Na falha:

```bash
docker compose ps
docker compose logs trino --tail 80
docker compose logs api --tail 80
docker compose logs iceberg-rest --tail 80
```

`/health` devolve 503 enquanto qualquer checagem falha. O corpo nomeia a dependência: `trino`, `redis` ou `object_storage`. O Trino quase sempre é o lento. O healthcheck espera até `GET /v1/info` informar `"starting": false`.

Se `minio-init` sai com código diferente de zero, os buckets não foram criados e a checagem de armazenamento da API falha. Leia `docker compose logs minio-init`. O contêiner de init precisa alcançar `http://minio:9000`. O acesso do host a `localhost:9000` pode funcionar mesmo quando o tráfego entre contêineres é descartado; veja rede abaixo.

### Memória esgotada, ou o Trino é morto por OOM

As notas do teste de carga publicado registram este modo de falha: um limite de 2 GB no contêiner do Trino junto com o heap padrão da imagem (uma fração grande desse limite) matou a JVM sob carga, e toda requisição falhou. O repositório agora define `-Xmx1536M` dentro de um limite de contêiner de 3 GB. Heap e limite do contêiner têm de andar juntos. Subir só o `-Xmx` além do que a memória do cgroup permite traz o OOM de volta.

Se `docker compose ps` mostra o Trino reiniciando, ou o `dmesg` cita uma morte por OOM:

- A memória do Docker Desktop está baixa. Defina 8 GB ou mais.
- Outra carga na máquina compete. Faça `make down` dos outros projetos.
- Não baixe o `mem_limit` do Trino abaixo de 3 GB sem também baixar `-Xmx` em `trino/etc/jvm.config` e `query.max-memory` em `trino/etc/config.properties`.

O contêiner da API tem 768 MB com dois workers uvicorn. Uma exportação grande vai para um arquivo temporário em blocos de 50,000 linhas, então o limite da API não é o que segura o resultado inteiro. O contêiner de seed tem 2 GB e commita a cada 250,000 linhas, então uma carga de milhões de linhas não exige uma máquina maior.

### Portas já em uso

O Compose publica:

| Porta no host | Serviço |
|---|---|
| 8000 | API |
| 8080 | Trino |
| 8181 | Iceberg REST |
| 9000 | API S3 |
| 9001 | Console do armazenamento |
| 6379 | Redis |

Ache quem escuta (`ss -ltnp` no Linux, ou a visão de portas do Docker Desktop) e pare, ou mude o lado esquerdo do mapeamento em `docker-compose.yml` (`"8000:8000"` vira `"18000:8000"`). Se mover a 8000, chame o teste de fumaça com `BASE_URL=http://localhost:18000 make test`. Se mover a 9000, defina `S3_PUBLIC_ENDPOINT` nessa mesma porta do host. As URLs pré-assinadas são montadas a partir de `S3_PUBLIC_ENDPOINT`, não do endereço interno do cluster.

### Permissão negada no socket do Docker

`dial unix /var/run/docker.sock: connect: permission denied` significa que o usuário não está no grupo `docker`. Ou entre de novo depois de `usermod -aG docker`, ou rode `make up DOCKER='sudo docker'` (e o mesmo prefixo em `seed`, `ps`, `logs`, `down`).

### Contêineres não se alcançam

Sintomas: `minio-init` registra `i/o timeout` ao discar `minio:9000`, ou a API registra erros de conexão com o Trino, enquanto `curl localhost:9000` no host funciona. Portas publicadas são intermediadas pelo Docker; o tráfego entre contêineres usa a bridge.

Confira `docker compose ps` e, de um contêiner na rede do projeto, `python -c` ou `curl` contra `http://minio:9000/minio/health/live`. No Linux, um firewall do host ou um segundo backend de iptables (nft e legacy carregados, com política FORWARD DROP só num deles) pode descartar o tráfego da bridge. `sudo iptables -L FORWARD` e `sudo iptables-legacy -L FORWARD` não devem descartar a bridge do Compose. Clientes de VPN corporativa às vezes fazem isso. Reiniciar o Docker depois de a VPN conectar é um conserto comum. É um problema do host; o arquivo Compose não desliga a comunicação entre contêineres.

### `make seed` falha ou o teste de fumaça não acha página

O seed precisa rodar depois de o catálogo estar no ar (`make up` primeiro). Ele sempre passa `--replace`, que derruba a tabela. Se você precisava do dado antigo, ele se foi.

O "hoje" do gerador é o relógio do contêiner, em geral UTC. `make test` usa a data local do host para uma janela de 7 dias. Perto da meia-noite, um host a oeste do UTC pode pedir uma janela que ainda não começou dentro do contêiner, e a primeira página fica vazia, então o teste falha em `next_cursor`. Rode de novo `make seed DAYS=30`, ou chame a API com datas explícitas que cubram o histórico gerado.

`make test` também espera pelo menos seis linhas nessa janela de 7 dias (pede `limit=5` e um cursor). Um seed minúsculo como `ROWS=100` em `DAYS=90` pode não chegar lá. Use `ROWS=20000` ou mais.

### O download pré-assinado devolve 403 ou não conecta

O host da URL é `S3_PUBLIC_ENDPOINT` (padrão `http://localhost:9000`). Esse host tem de ser o que quem baixa consegue resolver. Do laptop, localhost está certo. De outra máquina, defina o endereço alcançável antes de subir a API e recrie o contêiner da API para ele pegar o ambiente (`docker compose up -d api`). A assinatura está presa ao cabeçalho Host; editar a URL depois invalida.

O bucket `exports` tem uma regra de ciclo de vida de um dia. Um objeto mais velho que isso pode sumir.

### `make loadtest` no Docker Desktop não serve

`--network host` não prende o contêiner à rede do host Mac ou Windows do jeito que faz no Linux. O k6 então chama `localhost:8000` dentro do contêiner, onde a API não escuta. Use um host Linux para `make loadtest`, ou rode k6/Locust no host contra `http://localhost:8000`. Os comandos estão em [Desempenho](performance.md).

### O Compose avisa que Buildx ou Bake não está instalado

`Docker Compose is configured to build using Bake, but buildx isn't installed` é um aviso. O Compose recua e ainda constrói a imagem da API. Instale o plugin buildx se quiser o aviso fora. Isso não muda a aplicação.

### A API devolve 401, 400 ou 422

401 significa que o cabeçalho `X-API-Key` falta ou não está em `API_KEYS`. 400 significa que o intervalo de datas está invertido, passou do teto, ou o cursor não pode ser decodificado. 422 significa que um parâmetro falhou na validação (data ruim, categoria desconhecida, valor negativo, limite fora da faixa). A [referência da API](api-reference.md) lista cada caso.
