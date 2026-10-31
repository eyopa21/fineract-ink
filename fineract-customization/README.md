# Fineract customization: ERP-owned GL accounts (Part A)

A change inside Apache Fineract 1.15.0 so that GL accounts owned by an external accounting master (the ERP)
can be synchronized idempotently. Delivered as [`gl-external-reference.patch`](gl-external-reference.patch),
a diff against the upstream tag `1.15.0` (commit `d5636847`), applied to the submodule in `upstream/`.

To read the diff in the GitHub UI, the same change is on a fork branch:
[`1.15.0...feat/add-externalId-to-gl-accounts-module`](https://github.com/eyopa21/fineract/compare/1.15.0...eyopa21:fineract:feat/add-externalId-to-gl-accounts-module?expand=1).

## Behaviours enforced inside Fineract

| # | Requirement | Where it is enforced |
|---|---|---|
| 1 | ERP `external_reference` persisted on the GL account and unique | `acc_gl_account.external_id` (Liquibase part 0242) with unique constraint `acc_gl_account_external_id_UNIQUE`; `GLAccount.externalId` (`ExternalId` value object, same as clients and loans) |
| 2 | Same reference, identical payload returns the existing account | `GLAccountWritePlatformServiceJpaRepositoryImpl.createGLAccount`: lookup by reference, `GLAccountExternalIdPolicy.isSamePayload`, returns `resourceId` of the existing account with `changes.idempotentReplay = true`; nothing is written |
| 3 | Same reference, different payload rejected with 409 | same place: `GLAccountExternalIdConflictException`, mapped to HTTP 409 by `GLAccountExternalIdConflictExceptionMapper`. A unique-index violation (concurrent create) is mapped to the same 409 |
| 4 | Protected by Fineract's permission model | unchanged command pipeline: `POST /glaccounts` goes through `CommandWrapperBuilder.createGLAccount()` and `SynchronousCommandProcessingService`, which checks `CREATE_GLACCOUNT` before dispatching to `CreateGLAccountCommandHandler` |
| 5 | Schema change as Liquibase migration | `fineract-provider/.../tenant/parts/0242_acc_gl_account_external_id.xml`, included from `changelog-tenant.xml` |
| 6 | JUnit coverage | 10 new unit tests in `fineract-accounting` (service, policy, mapper) and 3 integration tests in `integration-tests` hitting the REST API |

## Mechanism and why

The existing `POST /api/v1/glaccounts` endpoint and its command handler were extended rather than adding a
new endpoint or a `custom/` module:

- The reference must live on the GL entity itself and be unique at the database level, which means touching
  the core entity, its table and its write service. A side module cannot change those.
- Reusing the existing command keeps the existing permission (`CREATE_GLACCOUNT`), audit trail
  (`m_portfolio_command_source`) and maker-checker semantics for free. A replay is logged as a command that
  resolved to the existing resource id.
- The transaction boundary is unchanged: `CreateGLAccountCommandHandler.processCommand` is `@Transactional`,
  so the lookup, the comparison and the insert run in one transaction. The lookup prevents duplicates in the
  normal case; the unique index is the last line of defence when two requests race, and the loser receives
  409 and succeeds with 200 on retry.
- Fineract's own `Idempotency-Key` header mechanism was not used for this: it keys on the command, not on the
  ERP reference, so a second ERP sync run with a new key would still create a duplicate.

The `external_id` is immutable through the API: `PUT /glaccounts/{id}` with a different value is rejected
with 409 (sync is create-only; updates and deletions are out of scope).

## Files changed (19)

```
fineract-core/        GLAccount.java, GLAccountRepository.java, GLAccountJsonInputParams.java, GLAccountData.java
fineract-accounting/  GLAccountCommand.java, GLAccountCommandFromApiJsonDeserializer.java,
                      GLAccountWritePlatformServiceJpaRepositoryImpl.java, GLAccountReadPlatformServiceImpl.java,
                      GLAccountsApiResource.java, GLAccountsApiResourceSwagger.java,
                      service/GLAccountExternalIdPolicy.java (new),
                      exception/GLAccountExternalIdConflictException.java (new),
                      exception/GLAccountExternalIdConflictExceptionMapper.java (new),
                      test: GLAccountExternalIdCreateTest, GLAccountExternalIdPolicyTest, GLAccountExternalIdConflictExceptionMapperTest (new)
fineract-provider/    db/changelog/tenant/changelog-tenant.xml, parts/0242_acc_gl_account_external_id.xml (new)
integration-tests/    GLAccountExternalIdIntegrationTest.java (new)
```

A side fix included in the write service: on PostgreSQL the `gl_code` unique key is named
`acc_gl_account_gl_code_key`, not `acc_gl_code` as the JPA annotation says, so upstream reported a duplicate
code as "Unknown data integrity issue" (403). The handler now matches both names.

## API

```http
POST /fineract-provider/api/v1/glaccounts
{"name":"Loan Receivable","glCode":"1200","type":1,"usage":1,"manualEntriesAllowed":true,"externalId":"GL-1200"}
```

| Case | Response |
|---|---|
| new reference | `200 {"resourceId":7,"resourceExternalId":"GL-1200"}` |
| same reference, identical payload | `200 {"resourceId":7,"resourceExternalId":"GL-1200","changes":{"idempotentReplay":true,"externalId":"GL-1200"}}` |
| same reference, different payload | `409 {"userMessageGlobalisationCode":"error.msg.glaccount.externalid.conflict", ...}` |
| user without `CREATE_GLACCOUNT` | `403 {"userMessageGlobalisationCode":"error.msg.not.authorized", "errors":[{"developerMessage":"User has no authority to: CREATE_GLACCOUNT"}]}` |

`GET /glaccounts` and `GET /glaccounts/{id}` now return `externalId`.

## Build, test, demo

All commands run from the repository root.

| Step | Command | Notes |
|---|---|---|
| apply patch to a fresh clone | `make patch-apply` | no-op if already applied |
| verify patch against pristine tag | `make patch-check` | uses a throwaway git worktree |
| build the image | `make build-fineract` | Jib on the host, JDK 21; or `make build-fineract-docker` with Docker only |
| unit tests | `make test-java` | ~2 min, no server |
| integration tests | `make up` then `make test-java-it` | first run generates Fineract's OpenAPI clients (~15 min, 8 GB Gradle heap recommended) |
| scripted demo of behaviours 1 to 4 | `make demo-part-a` | prints state before and after each call |
| regenerate the patch after editing `upstream/` | `make patch-export` | includes new files |

Evidence from the last run is in [`../evidence/part-a/`](../evidence/part-a/): build log, JUnit XML for unit
and integration tests, and the demo transcript.
