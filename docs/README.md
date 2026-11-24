# Documentação

[Português](#portugues) | [English](#english)

A [página inicial](../README.md) aponta para estes guias. Cada idioma está completo no próprio diretório: nada neste índice mistura os dois num mesmo parágrafo.

<a id="portugues"></a>

## Português

| Guia | O que cobre |
|---|---|
| [Arquitetura](pt-BR/architecture.md) | Componentes, os três fluxos e por que a stack tem esta forma |
| [Primeiros passos](pt-BR/getting-started.md) | Instalar no Linux, macOS e Windows/WSL2, rodar e resolver falhas comuns |
| [Referência da API](pt-BR/api-reference.md) | Cada endpoint, parâmetro, papel, erro e exemplo |
| [Configuração](pt-BR/configuration.md) | Variáveis de ambiente, Compose e alvos do Makefile |
| [Modelo de dados](pt-BR/data-model.md) | Tabela Iceberg, partições, ordem e o gerador |
| [Segurança](pt-BR/security.md) | Chaves, mascaramento, log de auditoria e lacunas de produção |
| [Desempenho](pt-BR/performance.md) | Por que o TPS se divide em duas camadas, como testar carga, números medidos e tamanho arquivo contra lago |
| [Implantação](pt-BR/deployment.md) | Laptop, Oracle Cloud Always Free, uma VPS pequena e um esboço na AWS |
| [Roteiro](pt-BR/roadmap.md) | O que esta demonstração deixa para um sistema de produção |
| [Oracle Cloud Always Free](pt-BR/oracle-cloud-free.md) | Passo a passo numa VM Ampere A1 |

Fora desta pasta:

- [Resultados do teste de carga](../loadtest/RESULTS.md) — a seção em português vem primeiro
- [Comparação de armazenamento](../loadtest/STORAGE.md) — CSV, largura fixa, JSON, gzip, Parquet e Iceberg
- [Como contribuir](../CONTRIBUTING.md)

<a id="english"></a>

## English

| Guide | What it covers |
|---|---|
| [Architecture](en/architecture.md) | Components, the three request flows, and why the stack is shaped this way |
| [Getting started](en/getting-started.md) | Install on Linux, macOS, and Windows/WSL2, then run and troubleshoot it |
| [API reference](en/api-reference.md) | Every endpoint, parameter, role, error, and example |
| [Configuration](en/configuration.md) | Environment variables, Compose settings, and Makefile targets |
| [Data model](en/data-model.md) | Iceberg table, partitions, sort order, and the generator |
| [Security](en/security.md) | API keys, masking, the audit log, and production gaps |
| [Performance](en/performance.md) | Why TPS splits across two layers, how to load-test, measured results, file vs lake size |
| [Deployment](en/deployment.md) | Laptop, Oracle Cloud Always Free, a small VPS, and an AWS sketch |
| [Roadmap](en/roadmap.md) | What this demo leaves for a production system |
| [Oracle Cloud Always Free](en/oracle-cloud-free.md) | Step-by-step for an Ampere A1 VM |

Outside this folder:

- [Load test results](../loadtest/RESULTS.md) — Portuguese section first, then English
- [Storage comparison](../loadtest/STORAGE.md) — CSV, fixed-width, JSON, gzip, Parquet, and Iceberg sizes
- [Contributing](../CONTRIBUTING.md)
