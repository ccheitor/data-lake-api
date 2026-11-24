# Documentation

Guides for the lakehouse data API. The [README](../README.md) is the short landing page.

| Guide | What it covers |
|---|---|
| [Architecture](architecture.md) | Components, the three request flows, and why the stack is shaped this way |
| [Getting started](getting-started.md) | Install on Linux, macOS, and Windows/WSL2, then run and troubleshoot it |
| [API reference](api-reference.md) | Every endpoint, parameter, role, error, and example |
| [Configuration](configuration.md) | Environment variables, Compose settings, and Makefile targets |
| [Data model](data-model.md) | Iceberg table, partitions, sort order, and the generator |
| [Security](security.md) | API keys, masking, the audit log, and production gaps |
| [Performance](performance.md) | Why TPS splits across two layers, how to load-test, measured results, file vs lake size |
| [Deployment](deployment.md) | Laptop, Oracle Cloud Always Free, a small VPS, and an AWS sketch |
| [Roadmap](roadmap.md) | What this demo leaves for a production system |

Related files outside this folder:

- [Load test results](../loadtest/RESULTS.md) — the numbers cited in the performance guide
- [Storage comparison](../loadtest/STORAGE.md) — CSV, fixed-width, JSON, gzip, Parquet, and Iceberg sizes
- [Oracle Cloud Always Free](oracle-cloud-free.md) — step-by-step for an Ampere A1 VM
- [Contributing](../CONTRIBUTING.md)
