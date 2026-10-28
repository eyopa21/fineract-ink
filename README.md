# Fintech Fineract Assessment

Apache Fineract (Java/Spring Boot) customized for ERP-owned GL accounts, plus a Python Mock ERP and a
Python Integration Service, wired together with Docker Compose.

> Status: Phase 0 (environment) complete. Tiers completed are listed at the bottom and updated as work lands.
> The working checklist is in [TASKS.md](TASKS.md).

## Quick start

Prerequisites: Docker 24+ with Compose v2, Git. For the default image build path also JDK 21
(an alternative Docker-only path exists, see below).

```bash
git clone --recurse-submodules <this-repo>
cd fineract-assessment
make build-fineract      # builds fineract-custom:1.15.0 from source with the patch applied (~5 min warm, ~12 min cold)
make up                  # starts postgres, fineract, mock-erp, integration-service and waits for health
```

If you cloned without `--recurse-submodules`:

```bash
git submodule update --init --depth 1
```

First Fineract boot takes 3 to 5 minutes because it runs its own Liquibase migrations into an empty
database. Later boots are faster.

| Service | URL on the host |
|---|---|
| Integration Service API and OpenAPI docs | http://localhost:8100 and http://localhost:8100/docs |
| Mock ERP API and OpenAPI docs | http://localhost:8101 and http://localhost:8101/docs |
| Fineract API | http://localhost:8080/fineract-provider/api/v1 |
| Fineract Swagger UI | http://localhost:8080/fineract-provider/swagger-ui/index.html |

Host ports are `INTEGRATION_PORT`, `MOCK_ERP_PORT` and `FINERACT_PORT` (defaults 8100, 8101, 8080). Override them in
the environment or a `.env` file next to `docker-compose.yml` if they collide with something on your machine.

Fineract bootstrap credentials: user `mifos`, password `password`, header `Fineract-Platform-TenantId: default`.
Part B replaces this with a least-privilege API user for the Integration Service.

Useful targets:

```bash
make help                # all targets
make ps                  # service status
make logs SVC=fineract   # tail one service
make reset               # stop and delete all data; next `make up` starts from an empty database
```

## Pinned versions

| Component | Version | Where it is pinned |
|---|---|---|
| Apache Fineract | tag `1.15.0`, commit `d5636847` | git submodule at `fineract-customization/upstream` |
| JDK | 21 (Azul Zulu 21 in the image, any JDK 21 on the host) | `.tool-versions` (asdf), `build.gradle` toolchain |
| Gradle | 8.14.5 | `gradle/wrapper/gradle-wrapper.properties` in the submodule |
| Fineract base image | `azul/zulu-openjdk-alpine:21` | Jib config in `fineract-provider/build.gradle` |
| PostgreSQL | 18.3 | `docker-compose.yml` |
| Python | 3.12 | `pyproject.toml` and Dockerfile of each service |
| FastAPI / SQLAlchemy / Pydantic | locked | `uv.lock` in each service |

Build notes for the host path:

- Gradle must itself run on JDK 21. A system-wide `JAVA_HOME` pointing at an older JDK overrides the
  JDK on `PATH`, so the Makefile sets `JAVA_HOME` from asdf when available. Otherwise export it yourself.
- Fineract's `gradle.properties` asks for a 12 GB daemon heap. On a 16 GB machine put the same line with
  `-Xmx6g` into `~/.gradle/gradle.properties`; the `--add-exports` flags on that line are required.

## Building the customized Fineract image

Two paths produce the same image name, `fineract-custom:1.15.0`:

| Path | Command | Needs | Time |
|---|---|---|---|
| Jib on the host (default) | `make build-fineract` | JDK 21, Docker | ~5 min warm |
| Inside Docker | `make build-fineract-docker` | Docker only | ~20 min cold, 6 GB RAM |

Both apply `fineract-customization/gl-external-reference.patch` to the pristine 1.15.0 tag. The Docker
path clones the tag from GitHub during the build. The host path builds the submodule working tree, so run
`make patch-apply` first on a fresh clone. `make patch-check` proves the patch applies cleanly to the tag.

What the patch does and why is documented in [fineract-customization/README.md](fineract-customization/README.md).
`make demo-part-a` runs a scripted demonstration of the four required behaviours against the running stack.

To run the unmodified baseline instead (useful for before/after comparisons):

```bash
cd fineract-customization/upstream && ./gradlew :fineract-provider:jibDockerBuild -x test && cd ../..
FINERACT_IMAGE=fineract:1.15.0 make up
```

## Tests

| Language | Command | What it runs |
|---|---|---|
| Java unit | `make test-java` | JUnit unit tests for the Part A GL account customization (no server) |
| Java integration | `make up` then `make test-java-it` | JUnit REST test against the running customized Fineract |
| Python | `make test-python` | pytest for `mock-erp` and `integration-service` |
| Python quality | `make check` | ruff lint and format check, mypy `--strict`, then pytest |

Python tooling runs through [uv](https://docs.astral.sh/uv/). If `uv` is not installed on the host the
Makefile uses the official `ghcr.io/astral-sh/uv` image instead, so no host install is required.

## Repository layout

```
fineract-assessment/
├── README.md ARCHITECTURE.md API.md SIMULATIONS.md DECISIONS.md TASKS.md
├── docker-compose.yml           one command starts everything
├── Makefile                     build, run, test and simulation targets
├── docker/postgres/init.sh      creates the four databases in the single Postgres
├── fineract-customization/
│   ├── upstream/                git submodule pinned to Apache Fineract 1.15.0
│   ├── gl-external-reference.patch   the Java customization as a diff against the tag
│   └── Dockerfile               Docker-only build path
├── fineract/configuration/      Part B environment setup
├── integration-service/         Python FastAPI service (app/, tests/, Dockerfile)
├── mock-erp/                    Python FastAPI mock ERP (app/, tests/, Dockerfile)
├── scripts/                     demos and simulations
├── postman/                     supporting collection
└── evidence/                    captured logs and simulation output
```

## Tiers completed

- [ ] Tier 1 Core (Parts A to G). Done so far: Part A (Fineract Java customization, see [fineract-customization/README.md](fineract-customization/README.md) and `evidence/part-a/`)
- [ ] Tier 2 Reliability (Parts H to K)
- [ ] Tier 3 Stretch (Part L)

## AI assistance disclosure

Claude (Anthropic) was used as a pair-programming assistant for exploring the Fineract source,
drafting configuration and code, and writing documentation. All design decisions, code and evidence
were reviewed and are explained in [DECISIONS.md](DECISIONS.md).
