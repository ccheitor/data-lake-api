# Deploying on Oracle Cloud Infrastructure (OCI) Always Free

Every image in `docker-compose.yml` publishes a `linux/arm64` build, so the stack runs unchanged
on OCI's Arm-based **Ampere A1** shape.

## Always Free limits that matter here (checked 2026-11-24)

Source: Oracle documentation, *Always Free Resources* —
<https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm>

| Resource | Always Free allowance (per tenancy, home region only) |
|---|---|
| Ampere A1 compute (`VM.Standard.A1.Flex`, Arm) | First **1,500 OCPU-hours and 9,000 GB-hours per month**, which Oracle states is equivalent to **2 OCPUs and 12 GB RAM** in total. One VM with 2 OCPU / 12 GB, or two VMs with 1 OCPU each. |
| AMD micro VMs (`VM.Standard.E2.1.Micro`) | Up to 2 VMs, 1/8 OCPU and 1 GB RAM each (too small for this stack) |
| Block Volume | **200 GB** total (boot + block volumes), 5 volume backups. Boot volume default ~47-50 GB per VM. |
| Object Storage | **20 GB** total (Standard + Infrequent Access + Archive) and **50,000 API requests per month** for Always-Free-only accounts (10 GB per tier for paid / trial accounts) |
| Outbound data transfer | 10 TB per month |
| Load balancer | 1 flexible LB (10 Mbps) and 1 Network Load Balancer |

> **Heads-up:** many tutorials still say "4 OCPU / 24 GB free". The official page now says
> **2 OCPU / 12 GB** (1,500 OCPU-hours / 9,000 GB-hours). Some older tenancies may show different
> numbers — check *Governance & Administration → Limits, Quotas and Usage* in your console,
> and re-read the page above before you plan capacity.

**Does the stack fit?** Yes. Container memory limits add up to ~5.2 GB
(Trino 3 GB, API 768 MB, Iceberg REST 768 MB, object storage 512 MB, Redis 192 MB),
leaving room for the OS on a 2 OCPU / 12 GB VM. With 2 OCPUs expect lower Trino throughput than
on a laptop — measure it with `make loadtest-all` and record it in `loadtest/RESULTS.md`.

## Caveats before you start

* **Idle reclamation.** Oracle may reclaim Always Free instances that are idle over 7 days: 95th
  percentile CPU < 20%, network < 20% **and** (for A1) memory < 20%. A running Trino JVM keeps
  memory well above 20% of 12 GB, but verify in the instance metrics, and keep backups — don't
  treat a free VM as durable.
* **"Out of host capacity".** A1 capacity is frequently exhausted in popular regions. The docs
  suggest trying another availability domain or retrying later. Your home region is fixed at
  sign-up and Always Free resources only exist there, so pick a less busy home region if you can.
  Upgrading to Pay-As-You-Go keeps Always Free resources free and usually improves availability,
  but then anything above the limits is billed — set a budget alert and compartment quotas.
* **Object Storage request quota.** 50,000 requests/month is small for a lakehouse: Trino and the
  Iceberg catalog issue many GET/HEAD/LIST calls per query. Use OCI Object Storage for exports or a
  small demo table, and keep the main lake on the VM's block volume (option A below).
* **Security.** Change every default password and API key in `.env`, expose only the API port
  (ideally behind HTTPS), and never open Trino (8080), Redis (6379) or the storage console (9001)
  to the internet.

## Option A — one Ampere A1 VM with Docker Compose (recommended)

1. **Create the VM**: Compute → Instances → Create. Image: Ubuntu 24.04 (aarch64) or Oracle Linux 9.
   Shape: `VM.Standard.A1.Flex`, **2 OCPU / 12 GB**. Boot volume 50 GB; optionally attach a block
   volume (up to the 200 GB total) for Docker data. Add your SSH public key.
2. **Networking**: in the subnet's security list (or an NSG) allow TCP 22 from your IP and TCP 443
   (or 8000 for a quick test, restricted to your IP). On Ubuntu images, OCI also ships host
   iptables rules — open the port there too:
   ```bash
   sudo iptables -I INPUT 6 -p tcp --dport 8000 -j ACCEPT && sudo netfilter-persistent save
   ```
3. **Install Docker** (Ubuntu):
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER && newgrp docker
   sudo apt-get install -y make git python3
   ```
4. **Deploy**:
   ```bash
   git clone https://github.com/ccheitor/data-lake-api.git && cd data-lake-api
   cp .env.example .env
   # edit .env: strong MINIO_ROOT_PASSWORD, new API_KEYS,
   # S3_PUBLIC_ENDPOINT=http://<vm-public-ip>:9000 (or an HTTPS hostname) so presigned URLs work remotely
   make up
   make seed ROWS=2000000
   make test
   ```
   If presigned downloads must work from outside, port 9000 has to be reachable too (restrict it, or
   put both behind a reverse proxy such as Caddy with HTTPS and route `/exports/*` to storage).
5. **Keep it running**: all services have `restart: unless-stopped`; enable Docker at boot with
   `sudo systemctl enable docker`.

## Option B — Kubernetes (OKE Basic cluster or k3s)

* **OKE**: the control plane of a **Basic** cluster is free; you pay only for worker nodes, so
  keep the node pool inside the Always Free A1 allowance (e.g. 2 nodes × 1 OCPU / 6 GB, or one node
  2 OCPU / 12 GB). The console creates an *Enhanced* (paid) cluster by default unless you tick the
  Basic option (CLI: `--type BASIC_CLUSTER`). Node pools are subject to the same A1 capacity issues.
  Docs: <https://docs.oracle.com/en-us/iaas/Content/ContEng/Tasks/contengcomparingenhancedwithbasicclusters_topic.htm>
* **k3s** on the A1 VM is lighter and avoids extra moving parts:
  `curl -sfL https://get.k3s.io | sh -`. Then deploy Trino with its official Helm chart
  (`helm repo add trino https://trinodb.github.io/charts`), Redis with the official
  `redis:7-alpine` image as a StatefulSet, and the API as a Deployment. With only 12 GB, run Trino as a
  single coordinator that also schedules work (`node-scheduler.include-coordinator=true`).
* Kubernetes adds ~1 GB of overhead; on 12 GB it fits, but option A gives Trino more memory.
  Use option B mainly to practise Kubernetes/Helm, which is a great portfolio addition.

## Using OCI Object Storage instead of MinIO

OCI Object Storage has an **Amazon S3 Compatibility API**
(<https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/s3compatibleapi.htm>):

* Endpoint (path style): `https://<namespace>.compat.objectstorage.<region>.oci.customer-oci.com`
  (`<namespace>` is your tenancy's Object Storage namespace, shown in the bucket details page).
* Credentials: create a **Customer Secret Key** (Profile → User settings → Customer secret keys).
  It gives an access key / secret key pair; the secret is shown only once. Max 2 per user.
* Region: use the OCI region identifier (e.g. `sa-saopaulo-1`); SigV4 only.

Changes in this project:

1. Create buckets `warehouse` and `exports` in your home region (optionally a lifecycle rule to
   delete exports after 1 day). Remove the `minio` and `minio-init` services from compose.
2. `.env`: set `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` to the Customer Secret Key pair
   (they are mapped to `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`), and
   `S3_PUBLIC_ENDPOINT=https://<namespace>.compat.objectstorage.<region>.oci.customer-oci.com`.
3. In `docker-compose.yml`, replace `http://minio:9000` with that same HTTPS endpoint in
   `iceberg-rest` (`CATALOG_S3_ENDPOINT`), `api` and `seed` (`S3_ENDPOINT`), and set `AWS_REGION`
   to your region.
4. `trino/catalog/iceberg.properties`: `s3.endpoint=` the same endpoint, `s3.region=<region>`,
   keep `s3.path-style-access=true`.

Remember the free tier's **20 GB / 50,000 requests per month**: fine for exports and a small demo,
not for load-testing a lake. A hybrid works well: lake on the VM's block volume (Silo/MinIO),
exports in OCI Object Storage, so presigned links are served by OCI over HTTPS without opening
port 9000 on the VM.
