# Task tracker

Working checklist for the Fintech Engineer take-home. Hours are a rough split of the 30 hour budget.
Tick an item only when it is done and verified.

## Phase 0: Repo and baseline build (~3h)

- [x] Create the assessment repo with an initial commit
- [x] Pin Apache Fineract 1.15.0 as a git submodule under `fineract-customization/upstream`
- [x] Pin JDK 21 via `.tool-versions` (asdf) at the repo root
- [x] Verify the unmodified source compiles (`:fineract-provider:compileJava`, 6m51s, Gradle 8.14.5, JDK 21, 6 GB daemon heap)
- [x] Add `.gitignore`
- [x] Push to remote
- [x] Build the unmodified Docker image with `jibDockerBuild` (4m43s, `fineract:1.15.0`, 962 MB) and confirm it starts on `docker-compose-postgresql.yml` (first boot ~5 min, 285 tables created, no business data)
- [x] Write `docker-compose.yml` with Postgres, Fineract, Mock ERP, Integration Service (one Postgres, four databases; Fineract on plain HTTP 8080; host ports 8100/8101/8080 configurable)
- [x] Confirm baseline Fineract starts from an empty database under compose (`make up`, all four services healthy, Fineract boot 170 s, tenant timezone Africa/Kigali, no GL accounts/clients/products)
- [x] Add a Makefile: build-fineract (Jib), build-fineract-docker, patch-export/apply/check, up, down, reset, lock, test-python, lint, typecheck, check, test-java, setup and simulate-* stubs
- [x] Record pinned versions in README (tag 1.15.0, JDK 21, Gradle 8.14.5, heap note, JAVA_HOME note, both image build paths)

## Baseline findings (unmodified 1.15.0, recorded 2026-10-27)

Facts verified against the running baseline image. Reuse these in the README, API.md and DECISIONS.md.

- Build: `./gradlew :fineract-provider:jibDockerBuild -x test` inside the submodule with `JAVA_HOME` on JDK 21. Jib, no Dockerfile. Base image `azul/zulu-openjdk-alpine:21`.
- Toolchain gotchas: `/etc/environment` sets `JAVA_HOME` to system JDK 17, which Gradle prefers over the asdf shim. Fix by sourcing `~/.asdf/plugins/java/set-java-home.zsh` or exporting `JAVA_HOME="$(asdf where java openjdk-21)"`. Project asks for a 12 GB daemon heap; overridden to 6 GB in `~/.gradle/gradle.properties`.
- Run: `docker compose -f docker-compose-postgresql.yml up -d` inside the submodule. Project name is `upstream` (directory name), services `db` and `fineract`, network `upstream_default`.
- Database: `postgres:18.3`, anonymous volume at `/var/lib/postgresql`, host port 5432 published. Init script creates `fineract_tenants` (tenant store) and `fineract_default` (tenant DB). Fineract runs its own Liquibase at boot: 285 tables, seed permissions, `mifos`/`password` admin user, one "Head Office". No GL accounts, clients or loan products.
- First boot ~5 min on this machine; later boots faster. Health check on port 8443.
- Auth: HTTP basic `mifos`/`password` plus header `Fineract-Platform-TenantId: default`. HTTPS with self-signed cert (`curl -k`).
- Swagger UI: `https://localhost:8443/fineract-provider/swagger-ui/index.html`. OpenAPI document: `/fineract-provider/fineract.json`. Authorize with `basicAuth` and `tenantid`.
- GL endpoints: `POST|GET /api/v1/glaccounts`, `GET /api/v1/glaccounts/{id}`, `GET /api/v1/glaccounts/template`.
- GL `type` enum: ASSET=1, LIABILITY=2, EQUITY=3, INCOME=4, EXPENSE=5. `usage`: DETAIL=1, HEADER=2. Loan products post only to DETAIL accounts.
- Create returns `{"resourceId":<numeric id>}`. No `externalId` accepted or returned. `acc_gl_account` columns: id, name, parent_id, hierarchy, gl_code, disabled, manual_journal_entries_allowed, account_usage, classification_enum, tag_id, description.
- Duplicate `glCode` returns HTTP 403 "Unknown data integrity issue": the handler matches constraint name `acc_gl_code` but Postgres names it `acc_gl_account_gl_code_key`. Patch must look up first and map constraint violations explicitly to 409, not rely on constraint-name matching.
- Fineract 1.15.0 has a command-level `Idempotency-Key` mechanism (succeeded / failed / in-progress exceptions). Keyed on the command, not the GL external reference, so it does not satisfy Part A alone; usable as a secondary layer for disbursement and repayment.
- Code map for Part A: entity `fineract-core/.../glaccount/domain/GLAccount.java`; params `GLAccountJsonInputParams`; deserializer `fineract-accounting/.../serialization/GLAccountCommandFromApiJsonDeserializer.java`; handler `CreateGLAccountCommandHandler` (`@CommandType(GLACCOUNT, CREATE)`, `@Transactional`); service `GLAccountWritePlatformServiceJpaRepositoryImpl.createGLAccount`; API `GLAccountsApiResource`; permission `CREATE_GLACCOUNT` seeded in `0002_initial_data.xml`; latest migration part `0241`, so new part is `0242`; existing unit test `GLAccountWritePlatformServiceJpaRepositoryImplTest`.
- Default tenant timezone is `Asia/Kolkata`; set `FINERACT_DEFAULT_TENANTDB_TIMEZONE=Africa/Kigali` in the assessment compose.
- Baseline DB (submodule compose) contained one test GL account (code 1200); that stack is down. The assessment stack (`make up`) starts clean; `make reset` wipes it.
- Assessment stack host ports: Integration 8100, Mock ERP 8101, Fineract 8080 (8000/8001 are taken by other local projects). Python services run as non-root in `python:3.12-slim`, deps locked with uv.

## Tier 1, Part A: Fineract Java customization (~7h)

- [x] Add `external_id` field to `GLAccount` entity with a unique constraint (`ExternalId` value object, unique constraint `acc_gl_account_external_id_UNIQUE`)
- [x] Liquibase migration part `0242_acc_gl_account_external_id.xml` adding the column and unique constraint; included from `changelog-tenant.xml`; verified applied in `databasechangelog`
- [x] Add `externalId` to `GLAccountJsonInputParams`, `GLAccountCommand`, the deserializer and `GLAccount.fromJson`
- [x] Write service: look up by external reference, return existing on identical payload (`changes.idempotentReplay=true`), pure comparison in `GLAccountExternalIdPolicy`
- [x] Write service: `GLAccountExternalIdConflictException` + JAX-RS mapper -> HTTP 409; unique-index violation mapped to the same 409; externalId immutable on PUT
- [x] Repository finder `findByExternalId(ExternalId)`
- [x] Permission: existing endpoint and `CREATE_GLACCOUNT` reused (checked by the command pipeline); verified 403 for a user without it
- [x] Expose `externalId` in the GL account read model, GET responses and Swagger DTOs
- [x] JUnit unit tests in `fineract-accounting`: 10 tests (create, replay, conflict, constraint fallback, policy, mapper), all passing
- [x] JUnit integration test `GLAccountExternalIdIntegrationTest`: persisted + replay x3, 409 on different payload, 403 without permission; passing against the compose stack (`make test-java-it`)
- [x] Regenerate `fineract-customization/gl-external-reference.patch` (19 files, +747/-9; `make patch-check` applies cleanly to 1.15.0). Commit: user
- [x] Capture build log and JUnit XML (unit + integration) under `evidence/part-a/`
- [x] Build `fineract-custom:1.15.0` with the patch applied (`make build-fineract`, 1m33s warm); starts under compose, migration 0242 applied on boot
- [x] Scripted demo `scripts/demo-part-a.sh` (`make demo-part-a`); transcript in `evidence/part-a/demo-part-a.txt`

## Tier 1, Part G: Mock ERP in Python (~2h)

Done 2026-10-28. API key header `X-API-Key`, error envelope `{error:{code,message,details},correlation_id}`, `X-Correlation-ID` echoed. Demo: `make demo-part-g`, transcript in `evidence/part-g/`.

- [x] Choose framework: FastAPI + uv + ruff + mypy strict, SQLAlchemy 2 + Alembic, psycopg 3, structlog; justify in DECISIONS.md (pending)
- [x] `POST /api/accounts` idempotent on `external_reference` (201 create, 200 replay with `X-Idempotent-Replay`, 409 on different payload); DB unique constraints on `code` and `external_reference`, race handled via IntegrityError
- [x] `GET /api/accounts`
- [x] `GET /api/accounts/{code}/balance` (computed from journal lines at read time, sign by account type)
- [x] `POST /api/journal-entries` idempotent on `external_reference`; unbalanced -> 422, unknown account / currency mismatch -> 422; lines persisted in one transaction
- [x] `GET /api/journal-entries/{external_reference}`
- [x] Seed chart of accounts: 1100 Bank, 1200 Loan Receivable, 4000 Interest Income plus 1300, 2100, 4100, 4200, 4300, 5100 for the cash-based loan product mappings (idempotent seed on every start)
- [x] pytest: 24 tests (accounting rules, canonical hashing, accounts API, journal API, faults); `evidence/part-g/pytest-mock-erp.xml`
- [x] Dockerfile (uv multi-stage, non-root; `start.sh` runs `alembic upgrade head`, seed, uvicorn)

## Tier 1, Parts B, C, E, F: Integration Service (~7h)

- [ ] Database schema: idempotency table (operation type + external_reference, payload hash, status, result)
- [ ] Database schema: client mapping with unique `client_uid`
- [ ] Database schema: loan mapping with unique `loan_reference` and unique disbursement per loan
- [ ] Database schema: transaction table with state machine column
- [ ] Authentication
- [ ] Correlation ID on every request and response
- [ ] Consistent error format
- [ ] OpenAPI docs
- [ ] Account type mapping ERP -> Fineract as one pure, tested function
- [ ] GL sync: `SYNC_PENDING -> SYNCED | FAILED`, retry when Fineract is down
- [ ] GL sync: sending the same account 4 times yields one ERP account, one GL account, one mapping
- [ ] Fineract setup script: office "Kigali Main Office", currency RWF
- [ ] Fineract setup script: least-privilege API user
- [ ] Fineract setup script: loan product "SME Asset Finance" (18%, monthly, 12 months, single disbursement) using synced GL accounts
- [ ] Fineract setup script: accounting rule chosen and documented (cash or accrual)
- [ ] Setup ordering enforced: GL sync before product creation
- [ ] `POST /api/clients` and `GET /api/clients/{client_uid}` using Fineract client externalId
- [ ] `POST /api/loans` issuing `loan_reference`, stored as Fineract loan externalId; approval
- [ ] `GET /api/loans/{loan_reference}`
- [ ] `POST /api/loans/{loan_reference}/disburse` with per-loan uniqueness
- [ ] `POST /api/loans/{loan_reference}/repayments`
- [ ] `GET /api/transactions/{external_reference}`
- [ ] Idempotency logic: new -> process; finished replay -> 200 same body; in progress -> 202; different payload -> 409
- [ ] No Fineract numeric IDs in any public response
- [ ] pytest unit tests: validation, transaction mapping, account-type mapping, idempotency, state transitions
- [ ] Integration tests against running Fineract and Mock ERP
- [x] Type checker and linter configured and passing (`make check`: ruff, mypy --strict, pytest, both services)

## Tier 1, Part D: Loan lifecycle (~2h)

- [ ] Script: application -> approval -> disbursement (5,000,000 RWF) -> 3 partial repayments -> final payoff -> Closed
- [ ] Capture Fineract status and balances after every stage into `evidence/`
- [ ] Demonstrate create client -> find by `client_uid` -> validate exists, active, correct name

## Tier 2, Part H: Accounting mapping (~1h)

- [ ] Pure, unit-tested mapping from Fineract transaction breakdown to journal lines
- [ ] Disbursement: DR Loan Receivable / CR Bank
- [ ] Repayment: DR Bank / CR Loan Receivable (principal) / CR Interest Income (interest)
- [ ] Document the source of the principal/interest split and how overpayments and reversals are handled

## Tier 2, Part I: Duplicates and concurrency (~2h)

- [ ] I1 simulation: same disbursement 4 times; print requests, Fineract disbursements, ERP entries, integration transactions
- [ ] I2 automated parallel test: 10+ concurrent disbursements, same reference
- [ ] I2 automated parallel test: 10+ concurrent disbursements, different references
- [ ] I2 write-up: layers relied on, why one layer is not enough, last line of defence
- [ ] I3 automated parallel test: concurrent repayments with different references, final balance checked against Fineract's breakdown
- [ ] I3: duplicates of one reference apply once

## Tier 2, Part J: Failure handling and recovery (~3h)

- [ ] Explicit state machine persisted per operation with documented transitions
- [ ] Retry policy: backoff, max attempts, what is safe to retry
- [ ] Timeout resolution by looking up the Fineract transaction by external reference before any retry
- [ ] Recovery: startup sweep and/or admin endpoint that completes or routes to `NEEDS_RECONCILIATION`
- [ ] Failure injection facility (ERP down, Fineract down, Fineract timeout, ERP timeout, crash between steps). ERP side done: `PUT /admin/faults` modes none/down/timeout/timeout_after_write
- [ ] Simulation 1: ERP down after Fineract success
- [ ] Simulation 2: Fineract down
- [ ] Simulation 3: Fineract timeout after sending
- [ ] Simulation 4: ERP timeout after entry written
- [ ] Simulation 5: crash between Fineract success and ERP call, recovered on restart

## Tier 2, Part K: Reconciliation (~2h)

- [ ] `POST /api/reconciliation` over a date range or set of references
- [ ] Outcomes: MATCHED, MISSING_IN_ERP, MISSING_IN_FINERACT, AMOUNT_MISMATCH
- [ ] Keyed per Fineract transaction matched to ERP entry by `external_reference`; reversal handling stated
- [ ] Simulation seeding all four outcomes and printing the report
- [ ] Read-only by default; any repair action explicit and idempotent

## Observability

- [ ] Structured JSON logs: correlation_id, external_reference, client_uid, loan_reference, fineract_transaction_id, erp_transaction_id, status, timestamp
- [ ] DECISIONS.md: "What happened to FIN-2026-000001?" answered from logs plus one API call, with a sample trail

## Documentation and evidence (~2h, written throughout)

- [ ] README.md: start command, tests per language, pinned versions, tiers completed, AI usage disclosure
- [ ] ARCHITECTURE.md: components, data flow, data model, state machine, GL sync design
- [ ] API.md: endpoints, examples, errors, idempotency contract
- [ ] SIMULATIONS.md: every simulation in the nine-point evidence template
- [ ] DECISIONS.md: customization mechanism, sync vs async, DB constraints, idempotency, retries, partial failure, not built, known weaknesses
- [ ] Postman collection
- [ ] Review commit history for incremental commits

## Tier 3, Part L: Stretch (optional, ~1h)

- [ ] GL master-data reconciliation (reuses Part K)
