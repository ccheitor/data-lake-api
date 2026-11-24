# Use "make DOCKER='sudo docker' ..." if your user is not in the docker group.
DOCKER   ?= docker
COMPOSE  ?= $(DOCKER) compose
ROWS     ?= 2000000
DAYS     ?= 90
MODE     ?= cached
VUS      ?= 10
DURATION ?= 60s
API_KEY  ?= dev-analyst-key
HOT      ?= 1000

.PHONY: up seed test loadtest loadtest-all logs ps down clean

.env:
	cp .env.example .env

up: .env            ## build and start the whole stack, wait until the API is healthy
	$(COMPOSE) up -d --build
	./scripts/wait-healthy.sh

seed:               ## generate ROWS fake transactions over DAYS days into Iceberg
	$(COMPOSE) --profile tools run --rm seed --rows $(ROWS) --days $(DAYS) --replace

test:               ## end-to-end smoke test of every endpoint
	./scripts/smoke-test.sh

loadtest:           ## k6 load test: MODE=cached|direct|page VUS=10 DURATION=60s
	$(DOCKER) run --rm --network host -v $(PWD)/loadtest:/scripts:ro -v $(PWD)/loadtest/results:/results \
	  -e MODE=$(MODE) -e VUS=$(VUS) -e DURATION=$(DURATION) -e API_KEY=$(API_KEY) -e HOT_CUSTOMERS=$(HOT) \
	  grafana/k6:latest run --quiet /scripts/k6-lookup.js

loadtest-all:       ## direct Trino vs cold cache vs warm cache vs paginated scan
	$(COMPOSE) exec -T redis redis-cli FLUSHALL
	$(MAKE) loadtest MODE=direct HOT=$(HOT)
	$(MAKE) loadtest MODE=cached HOT=$(HOT)
	$(MAKE) loadtest MODE=warmup HOT=$(HOT)
	$(MAKE) loadtest MODE=cached HOT=$(HOT)
	$(MAKE) loadtest MODE=page

logs:
	$(COMPOSE) logs -f api

ps:
	$(COMPOSE) ps

down:               ## stop containers, keep data volumes
	$(COMPOSE) down

clean:              ## stop and delete all data volumes
	$(COMPOSE) down -v
