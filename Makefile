# Fintech Fineract assessment. Run `make help` for targets.
SHELL := /bin/bash
.SHELLFLAGS := -o pipefail -c
.DEFAULT_GOAL := help

FINERACT_TAG      ?= 1.15.0
FINERACT_IMAGE    ?= fineract-custom:$(FINERACT_TAG)
UPSTREAM          := fineract-customization/upstream
PATCH             := fineract-customization/gl-external-reference.patch
PY_SERVICES       := mock-erp integration-service
COMPOSE           := docker compose
export FINERACT_IMAGE

# JDK 21 for Gradle: prefer asdf's openjdk-21 when present, else the ambient JAVA_HOME.
ASDF_JAVA := $(shell command -v asdf >/dev/null 2>&1 && asdf where java openjdk-21 2>/dev/null)
GRADLE_ENV := $(if $(ASDF_JAVA),JAVA_HOME=$(ASDF_JAVA),)

# uv: use the host binary when installed, else the official image (no host install needed).
UV_HOST := $(shell command -v uv 2>/dev/null)
define UV
$(if $(UV_HOST),uv --directory $(1),docker run --rm -v "$(CURDIR)/$(1):/app" -w /app -e UV_LINK_MODE=copy ghcr.io/astral-sh/uv:python3.12-bookworm-slim uv)
endef

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-24s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- Fineract (Java)

.PHONY: patch-export
patch-export: ## Export the submodule's diff against the upstream tag into the patch file (new files included)
	git -C $(UPSTREAM) add --intent-to-add --all
	git -C $(UPSTREAM) diff $(FINERACT_TAG) > $(PATCH)
	@wc -l $(PATCH)

.PHONY: patch-apply
patch-apply: ## Apply the patch to the clean submodule (no-op if empty or already applied)
	@if [ ! -s $(PATCH) ]; then echo "patch is empty, nothing to apply"; \
	elif git -C $(UPSTREAM) apply --reverse --check ../gl-external-reference.patch 2>/dev/null; then echo "patch already applied"; \
	else git -C $(UPSTREAM) apply ../gl-external-reference.patch && echo "patch applied"; fi

.PHONY: patch-check
patch-check: ## Verify the patch applies cleanly to the pristine tag (uses a throwaway worktree, touches nothing)
	@rm -rf /tmp/fineract-patch-check
	git -C $(UPSTREAM) worktree add -q --detach /tmp/fineract-patch-check $(FINERACT_TAG)
	git -C /tmp/fineract-patch-check apply --check --stat $(CURDIR)/$(PATCH) && echo "patch applies cleanly to $(FINERACT_TAG)"
	git -C $(UPSTREAM) worktree remove --force /tmp/fineract-patch-check

.PHONY: build-fineract
build-fineract: ## Build $(FINERACT_IMAGE) from the submodule working tree with Jib (default path, ~5 min warm)
	cd $(UPSTREAM) && $(GRADLE_ENV) ./gradlew :fineract-provider:jibDockerBuild -x test \
		-Djib.to.image=$(firstword $(subst :, ,$(FINERACT_IMAGE))) -Djib.to.tags=$(lastword $(subst :, ,$(FINERACT_IMAGE)))

.PHONY: build-fineract-docker
build-fineract-docker: ## Build $(FINERACT_IMAGE) entirely inside Docker (no JDK on host, ~20 min cold)
	docker build -f fineract-customization/Dockerfile --build-arg FINERACT_TAG=$(FINERACT_TAG) -t $(FINERACT_IMAGE) fineract-customization

.PHONY: test-java
test-java: ## Part A JUnit unit tests (no server needed, ~2 min)
	cd $(UPSTREAM) && $(GRADLE_ENV) ./gradlew :fineract-accounting:test --tests '*GLAccountExternalId*' --tests '*GLAccountWritePlatformServiceJpaRepositoryImplTest'

.PHONY: test-java-it
test-java-it: ## Part A JUnit integration test against the running stack (make up first; first run generates the client, ~15 min)
	cd $(UPSTREAM) && $(GRADLE_ENV) BACKEND_PROTOCOL=http BACKEND_HOST=localhost BACKEND_PORT=$(or $(FINERACT_PORT),8080) \
		./gradlew :integration-tests:test -PcargoDisabled --max-workers=1 --tests 'org.apache.fineract.integrationtests.GLAccountExternalIdIntegrationTest'

# ---------------------------------------------------------------- Python services

.PHONY: lock
lock: ## Regenerate uv.lock for both Python services
	@for s in $(PY_SERVICES); do $(call UV,$$s) lock; done

.PHONY: test-python
test-python: ## Run pytest for both Python services
	@for s in $(PY_SERVICES); do echo "== $$s"; $(call UV,$$s) run --frozen pytest || exit 1; done

.PHONY: lint
lint: ## ruff check + format check for both Python services
	@for s in $(PY_SERVICES); do echo "== $$s"; $(call UV,$$s) run --frozen ruff check . && $(call UV,$$s) run --frozen ruff format --check . || exit 1; done

.PHONY: typecheck
typecheck: ## mypy --strict for both Python services
	@for s in $(PY_SERVICES); do echo "== $$s"; $(call UV,$$s) run --frozen mypy app tests || exit 1; done

.PHONY: check
check: lint typecheck test-python ## lint + typecheck + tests

.PHONY: test-python-live
test-python-live: ## Live tests against the running stack (Part I concurrency); INTEGRATION_URL/FINERACT_URL/ERP_URL override targets
	uv --directory integration-service run --frozen pytest -q -s tests_live

# ---------------------------------------------------------------- Stack

.PHONY: build
build: ## Build the Python service images
	$(COMPOSE) build

.PHONY: up
up: ## Start everything and wait for health (Fineract first boot ~5 min)
	$(COMPOSE) up -d --build --wait

.PHONY: down
down: ## Stop the stack, keep data
	$(COMPOSE) down

.PHONY: reset
reset: ## Stop the stack and delete all data (next `up` starts from an empty database)
	$(COMPOSE) down -v --remove-orphans

.PHONY: ps
ps: ## Show service status
	$(COMPOSE) ps

.PHONY: logs
logs: ## Tail logs (SVC=name to filter)
	$(COMPOSE) logs -f $(SVC)

.PHONY: psql
psql: ## Open psql on DB=<fineract_default|erp|integration> (default fineract_default)
	$(COMPOSE) exec postgres psql -U postgres -d $(or $(DB),fineract_default)

.PHONY: demo-part-a
demo-part-a: ## Part A: scripted demo of behaviours 1-4 against the running stack
	./scripts/demo-part-a.sh

.PHONY: demo-gl-sync
demo-gl-sync: ## Part A (Python side): ERP chart -> Fineract GL sync, 4x idempotent, against the running stack
	./scripts/demo-gl-sync.sh

.PHONY: demo-part-g
demo-part-g: ## Part G: scripted demo of the Mock ERP (idempotency, balance rule, balances, faults)
	./scripts/demo-part-g.sh

.PHONY: setup
setup: ## Part B: configure Fineract inside the stack (office, currency, GL sync, loan product, API user); idempotent
	@mkdir -p evidence/part-b
	$(COMPOSE) exec -T integration-service python -m app.setup | tee evidence/part-b/setup-report.json

.PHONY: setup-local
setup-local: ## Part B against Fineract on localhost (IDE instance) using integration-service/.env
	@mkdir -p evidence/part-b
	uv --directory integration-service run --frozen python -m app.setup | tee evidence/part-b/setup-report.json

# ---------------------------------------------------------------- Simulations (filled in Tier 2)

.PHONY: simulate-lifecycle simulate-duplicates simulate-concurrency simulate-failures simulate-reconciliation
simulate-lifecycle: ## Part D: one loan end to end (client -> loan -> disburse -> 3 repayments -> payoff -> Closed), state after every stage
	@mkdir -p evidence/part-d
	./scripts/demo-part-d.sh | tee evidence/part-d/lifecycle.txt
simulate-duplicates: ## I1 only: same disbursement 4 times
	uv --directory integration-service run --frozen pytest -q -s tests_live/test_concurrency.py -k i1
simulate-concurrency: ## I1/I2/I3: sequential duplicates, 12 parallel disbursements (same/different refs), parallel repayments; evidence/part-i
	@mkdir -p evidence/part-i
	uv --directory integration-service run --frozen pytest -q -s tests_live/test_concurrency.py 2>&1 | tee evidence/part-i/concurrency.txt
simulate-failures: ## Part J: five failure scenarios (ERP down, Fineract down, Fineract timeout, ERP timeout, crash) with before/during/after state
	@mkdir -p evidence/part-j
	RESTART_CMD="$(COMPOSE) restart integration-service" python3 scripts/simulate_failures.py | tee evidence/part-j/failures.txt
simulate-reconciliation: ## Part K: seed four outcomes and print the report
	@echo "TODO: Part K"
