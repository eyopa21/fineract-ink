# Fintech Fineract Assessment

Apache Fineract 1.15.0 (Java/Spring Boot) customized for ERP-owned GL accounts, a Python Mock ERP, and a
Python Integration Service that connects a financing partner to both. One command starts everything.

**Tiers completed:** Tier 1 Core (Parts A to G) and Tier 2 Reliability (Parts H to K) in full. Tier 3:
GL master-data reconciliation. Not built: see "What is not built" in [DECISIONS.md](DECISIONS.md). The working checklist with every item
and where its evidence lives is [TASKS.md](TASKS.md).

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | components, data flow, data model, state machine, GL sync design |
| [API.md](API.md) | endpoints, examples, error format, idempotency contract |
| [SIMULATIONS.md](SIMULATIONS.md) | every simulation in the evidence template, with figures |
| [DECISIONS.md](DECISIONS.md) | trade-offs, assumptions, known weaknesses, log-trail walkthrough, AI disclosure |
| [fineract-customization/README.md](fineract-customization/README.md) | the Java change: mechanism, files, tests |
| [fineract/configuration/README.md](fineract/configuration/README.md) | every Fineract configuration decision |

## 1. Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Docker with Compose v2 | 24+ | everything |
| Git | any | clone with the submodule |
| JDK 21 | Azul Zulu 21 or any JDK 21 | building the Fineract image on the host (default path) |
| uv | 0.4+ | Python tests on the host; optional, the Makefile falls back to the uv Docker image |
| 16 GB RAM, ~6 GB free | | the Fineract build; Fineract itself runs in about 1.5 GB |

Install uv with `brew install uv` or `pip install uv`. JDK 21 via asdf is pinned in `.tool-versions`; any
other JDK 21 works if `JAVA_HOME` points at it. The Makefile picks up asdf's JDK automatically.

## 2. Start the stack, step by step

```bash
git clone --recurse-submodules <this-repo>
cd fineract-assessment
```
If you cloned without `--recurse-submodules`: `git submodule update --init --depth 1`.

**Step 1: apply the Fineract patch to the pristine submodule and build the custom image** (~5 min warm,
~12 min cold, JDK 21 on the host):
```bash
make patch-apply
make build-fineract
```
Without a JDK, build entirely inside Docker instead (~20 min cold, 6 GB RAM): `make build-fineract-docker`.
Both produce `fineract-custom:1.15.0`. `make patch-check` proves the patch applies cleanly to the tag.
To read the Java change in the GitHub UI, the same diff is on a fork branch:
[1.15.0...feat/add-externalId-to-gl-accounts-module](https://github.com/eyopa21/fineract/compare/1.15.0...eyopa21:fineract:feat/add-externalId-to-gl-accounts-module?expand=1).
The patch file in this repository is what gets built; the fork branch is a reading aid.

**Step 2: start everything and wait for health** (first Fineract boot 3 to 5 minutes: it runs its own
Liquibase migrations into an empty database; later boots about 2 minutes):
```bash
make up
```
Four containers: Postgres 18 with four databases, Fineract on plain HTTP, the Mock ERP (migrated and seeded
on start), the Integration Service (migrated on start, runs its recovery sweep).

**Step 3: configure Fineract** (Part B; idempotent, safe to repeat; ~30 s):
```bash
make setup
```
Renames the office to Kigali Main Office, enables RWF, syncs the ERP chart of accounts into Fineract GL
accounts, creates the SME Asset Finance loan product on those accounts, creates the least-privilege role and
the `integration-api` user the service runs as, and verifies with that user. Until this has run, the
Integration Service's Fineract calls answer 503 (its runtime user does not exist yet).

**Step 4: check**
```bash
make ps
curl -s -H "X-API-Key: partner-demo-key" http://localhost:8100/api/accounts/sync | head -c 300
```

| Service | URL on the host | Credentials |
|---|---|---|
| Integration Service API and docs | http://localhost:8100 and http://localhost:8100/docs | header `X-API-Key: partner-demo-key` |
| Mock ERP API and docs | http://localhost:8101 and http://localhost:8101/docs | header `X-API-Key: erp-demo-key` |
| Fineract API | http://localhost:8080/fineract-provider/api/v1 | `mifos` / `password`, header `Fineract-Platform-TenantId: default` |
| Fineract Swagger UI | http://localhost:8080/fineract-provider/swagger-ui/index.html | Authorize with basicAuth and tenantid `default` |

Host ports are `INTEGRATION_PORT` (8100), `MOCK_ERP_PORT` (8101), `FINERACT_PORT` (8080); override them in
the environment or a `.env` next to `docker-compose.yml` if they collide. The database port is not
published.

**Stop, reset, logs**
```bash
make down                    # stop, keep data
make reset                   # stop and delete all data; the next `make up` starts from an empty database (then `make setup` again)
make logs SVC=integration-service
make psql DB=integration     # DB=fineract_default | erp | integration
```

## 3. Run the simulations (evidence)

All against the running stack, each prints before/after state and saves a transcript under `evidence/`:

```bash
make demo-part-a              # Part A: the Fineract customization, behaviours 1-4
make demo-gl-sync             # Part A (Python): ERP chart -> Fineract, four times
make demo-part-g              # Part G: Mock ERP idempotency, balance rule, faults
make simulate-lifecycle       # Part D: application -> approval -> disbursement -> 3 repayments -> payoff -> Closed
make simulate-concurrency     # Part I: I1, I2a, I2b, I3, 12 parallel threads, real HTTP
make simulate-failures        # Part J: the five failure scenarios, with a real container restart for the crash case
make simulate-reconciliation  # Part K: all four outcomes seeded, report, then explicit repair
make simulate-gl-reconciliation  # Stretch: Fineract GL accounts vs the ERP chart of accounts
```
Each run creates its own client and loan, so the targets can be repeated. [SIMULATIONS.md](SIMULATIONS.md)
walks through the saved transcripts.

## 4. Tests

| Language | Command | What runs | Needs |
|---|---|---|---|
| Java unit | `make test-java` | 10 JUnit tests for the Part A customization in `fineract-accounting` | JDK 21, ~2 min |
| Java integration | `make test-java-it` | 3 JUnit REST tests against the running customized Fineract | stack up; first run ~15 min (generates Fineract's OpenAPI clients, 8 GB Gradle heap) |
| Python unit | `make test-python` | 61 Integration Service + 26 Mock ERP tests with fake upstreams | uv (host or Docker fallback), seconds |
| Python quality | `make check` | ruff lint and format check, mypy `--strict`, then the unit tests | same |
| Python live | `make test-python-live` | the Part I concurrency tests against the stack | stack up |

Python code is typed (`mypy --strict`) and linted (`ruff`). JUnit XML from the last runs is under
`evidence/part-a/`, `evidence/part-e/` and `evidence/part-g/`.

## 5. Pinned versions

| Component | Version | Pinned in |
|---|---|---|
| Apache Fineract | tag `1.15.0`, commit `d5636847` | git submodule `fineract-customization/upstream` |
| JDK | 21 | `.tool-versions` (asdf), `build.gradle` toolchain; image base `azul/zulu-openjdk-alpine:21` |
| Gradle | 8.14.5 | the submodule's wrapper |
| PostgreSQL | 18.3 | `docker-compose.yml` |
| Python | 3.12 | each service's `pyproject.toml` and Dockerfile |
| FastAPI, SQLAlchemy 2, Alembic, Pydantic 2, httpx, structlog | locked | each service's `uv.lock` |

Build notes: Gradle must run on JDK 21 (a system-wide `JAVA_HOME` on an older JDK overrides the shim; the
Makefile sets `JAVA_HOME` from asdf when available). Fineract's `gradle.properties` asks for a 12 GB daemon
heap; on a 16 GB machine put the same `org.gradle.jvmargs` line with `-Xmx6g` (8 GB for the integration
tests) into `~/.gradle/gradle.properties`, keeping its `--add-exports` flags.

## 6. Developing locally without the containers

Each Python service runs on the host against the compose Postgres (publish it with the override below):

```bash
cd mock-erp && cp .env.example .env && uv sync && uv run alembic upgrade head && uv run python -m app.seed && uv run uvicorn app.main:app --port 8001 --reload
cd integration-service && cp .env.example .env && uv sync && uv run alembic upgrade head && uv run uvicorn app.main:app --port 8100 --reload
```
`.env.example` targets `localhost` ports; adjust `ERP_BASE_URL` if you run the ERP on 8001 instead of the
container on 8101. A gitignored `docker-compose.override.yml` can publish Postgres on 5432 and expose a JDWP
debug port on the Fineract container; see `fineract-customization/README.md` for running Fineract itself
from an IDE. Only one Fineract may run against the database at a time.

## 7. Repository layout

```
fineract-assessment/
├── README.md ARCHITECTURE.md API.md SIMULATIONS.md DECISIONS.md TASKS.md
├── docker-compose.yml           one command starts everything
├── Makefile                     build, run, setup, test and simulation targets (`make help`)
├── docker/postgres/init.sh      creates the four databases
├── fineract-customization/      upstream/ (submodule), gl-external-reference.patch, README.md, Dockerfile
├── fineract/configuration/      Part B decisions
├── integration-service/         app/ (routers, services, clients, models), alembic/, tests/, tests_live/, Dockerfile
├── mock-erp/                    app/, alembic/, tests/, Dockerfile
├── scripts/                     demos and simulations
├── postman/collection.json      supporting collection
└── evidence/                    transcripts, JUnit XML, Fineract snapshots, log trails, per part
```

## 8. AI assistance disclosure

Claude (Anthropic) was used as a pair-programming assistant for exploring the Fineract source, drafting
code, tests, scripts and documentation. Design decisions were reviewed and are explained in
[DECISIONS.md](DECISIONS.md); every test result and transcript in `evidence/` was produced by running the code.
