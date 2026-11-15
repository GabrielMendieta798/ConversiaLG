# WhatsApp inbound message idempotency specification

## ADDED Requirements

### Requirement: Persist a minimal inbound message claim

The system MUST persist successfully processed WhatsApp inbound message
identifiers in `processed_whatsapp_messages`.

The table MUST contain:

- `message_id` as a PostgreSQL `TEXT` primary key;
- `received_at` as a non-null PostgreSQL `TIMESTAMPTZ` with a server-side
  `now()` default.

The table MUST NOT require a numeric surrogate identifier and MUST NOT contain
the webhook payload or customer data.

#### Scenario: Persist a successfully processed message

- **WHEN** a new inbound `message_id` and its handler effect commit
  successfully
- **THEN** exactly one row exists for that `message_id`
- **AND** `received_at` is populated by PostgreSQL

#### Scenario: Keep the claim free of customer data

- **WHEN** a processed-message row is inspected
- **THEN** it contains only `message_id` and `received_at`
- **AND** it contains no webhook payload, message body, signature, phone
  number, customer name or delivery address

### Requirement: Reject an invalid message identity before the claim

The webhook orchestration MUST validate every parsed message identity before
opening the first message Session or calling the idempotency repository. A
`message_id` MUST be a `str` with at least one non-whitespace character. It
MUST remain opaque and MUST NOT be trimmed or otherwise normalized before the
claim.

#### Scenario: Invalid identity cannot reach the repository

- **GIVEN** an authenticated parser result contains a `message_id` that is not
  a string, is empty or contains only whitespace
- **WHEN** the orchestration validates the parsed batch
- **THEN** it returns a uniform 5xx response
- **AND** no Session is opened for any item in the batch
- **AND** the idempotency repository is not called
- **AND** no handler or external effect occurs

### Requirement: Claim a message atomically

The idempotency repository MUST claim a message with PostgreSQL
`INSERT ... ON CONFLICT DO NOTHING RETURNING message_id` and MUST return an
explicit claimed-or-duplicate result.

It MUST NOT implement claim as a `SELECT` followed by a separate `INSERT`.

#### Scenario: Claim a new message

- **WHEN** no committed row exists for an inbound `message_id`
- **THEN** the insert returns that `message_id`
- **AND** the repository reports the message as claimed

#### Scenario: Receive a sequential duplicate

- **WHEN** a committed row already exists for an inbound `message_id`
- **THEN** the insert returns no row
- **AND** the repository reports the message as duplicate

#### Scenario: Repository leaves transaction control to its caller

- **WHEN** the repository claims a new message
- **THEN** it does not begin, commit or roll back a transaction
- **AND** another connection cannot see the row before the caller commits

### Requirement: Give every message its own READ COMMITTED transaction

Every message produced by the authenticated inbound parser MUST be processed
with a new SQLAlchemy Session and its own PostgreSQL transaction. The
transaction isolation level MUST be explicitly `READ COMMITTED`, not merely
assumed from the database server default (`D-32`).

#### Scenario: Isolation is fixed by the webhook orchestration

- **WHEN** the handler runs for a newly claimed message
- **THEN** `SHOW transaction_isolation` in that same transaction reports
  `read committed`

#### Scenario: Two messages from one webhook use independent transactions

- **GIVEN** one authenticated webhook yields two new messages
- **WHEN** both are processed successfully
- **THEN** each claim and handler effect commits in its own transaction
- **AND** neither message reuses the other message's Session or transaction

### Requirement: Bind the handler to the claim transaction

For a claimed message, the orchestration MUST build an injected handler from a
factory bound to the same Session that performed the claim. The handler MUST
apply its database effects through dependencies bound to that Session and MUST
return descriptive post-commit actions without executing external I/O.

The handler and its repositories MUST NOT begin, commit or roll back the
transaction. The handler MUST NOT receive a raw dictionary as its validated
message input.

#### Scenario: Processing succeeds

- **WHEN** a new message is claimed and its handler succeeds
- **THEN** the handler is invoked exactly once
- **AND** the claim and every database effect of the handler commit together
- **AND** the returned actions are retained for dispatch after commit

#### Scenario: Duplicate stops before the handler boundary

- **WHEN** the claim reports a committed duplicate
- **THEN** the handler factory is not used
- **AND** the handler is not invoked
- **AND** no handler effect or post-commit action is repeated

#### Scenario: Handler cannot dispatch before commit

- **WHEN** the handler returns one or more external actions
- **THEN** no dispatcher is invoked while the message transaction is active

### Requirement: Roll back the claim with handler effects

The idempotency claim and every transactional handler effect caused by one
inbound message MUST execute in the same database transaction.

#### Scenario: Processing fails after the claim

- **WHEN** the handler raises after claiming a new message but before commit
- **THEN** the claim and all partial handler changes roll back together
- **AND** no post-commit action for that processing is dispatched
- **AND** the webhook responds with a 5xx status
- **AND** a later retry can claim and process the same `message_id`

#### Scenario: Retry after rollback succeeds once

- **GIVEN** a first attempt for a `message_id` rolled back after the claim
- **WHEN** the same message is retried and its handler succeeds
- **THEN** the retry reports claimed
- **AND** exactly one committed claim and one committed handler effect exist

### Requirement: Preserve the lock-order extension point

The idempotency claim MUST complete before the transaction-bound handler is
constructed or invoked. A duplicate MUST stop before that handler boundary.

The real `SELECT ... FOR UPDATE` locks for Conversation, Order and Reservation
are deferred to `add-conversation-state-machine`. That change MUST preserve the
full normative order of `CONV-9`: idempotency claim, Conversation lock, then
Order or Reservation lock.

#### Scenario: Claimed message reaches the handler after the insert

- **WHEN** a new message reaches the transaction-bound handler
- **THEN** its idempotency row has already been inserted in that transaction

#### Scenario: Duplicate cannot acquire later domain locks

- **WHEN** the claim reports duplicate
- **THEN** processing stops before the handler boundary
- **AND** code responsible for Conversation, Order or Reservation locks cannot
  be invoked

### Requirement: Resolve concurrent delivery in PostgreSQL

Concurrent idempotency behavior MUST be verified with real parallel execution
against PostgreSQL. A sequential pair of calls MUST NOT be accepted as the
concurrency proof.

#### Scenario: Receive the same message concurrently

- **GIVEN** no committed row exists for a `message_id`
- **WHEN** two threads with independent PostgreSQL connections and transactions
  attempt to process it at the same time
- **THEN** PostgreSQL allows at most one transaction to commit the claim
- **AND** exactly one transaction commits the handler effect
- **AND** the other transaction resolves as duplicate after the winner commits

#### Scenario: Concurrent winner rolls back

- **GIVEN** one transaction has inserted the claim without committing
- **AND** a second independent transaction is waiting for the same
  `message_id`
- **WHEN** the first transaction rolls back
- **THEN** the waiting transaction can claim and process the message
- **AND** exactly one claim and one handler effect are ultimately committed

### Requirement: Verify the migration in a dedicated PostgreSQL test database

The Alembic revision that introduces `processed_whatsapp_messages` MUST be
tested in the dedicated migration database. The test MUST exercise upgrade
from its direct parent, inspect the complete table contract and downgrade back
to that parent without altering any other table.

#### Scenario: Upgrade creates the exact table

- **WHEN** the migration upgrades from revision `88d85f44fb7d` to
  `57f88cf9ee63`
- **THEN** the table has a `TEXT` primary key `message_id`
- **AND** `received_at` is non-null `TIMESTAMPTZ` with a server-side
  `now()` default
- **AND** no other table is added, dropped or changed by that revision

#### Scenario: Downgrade removes only the processed-message table

- **WHEN** revision `57f88cf9ee63` is downgraded to `88d85f44fb7d`
- **THEN** `processed_whatsapp_messages` no longer exists
- **AND** the schema that existed at the parent revision is otherwise intact

### Requirement: Complete idempotency verification uses PostgreSQL

Repository, transaction, rollback and concurrency integration tests MUST run
against PostgreSQL rather than SQLite. Migration tests that reset a schema MUST
run only against the dedicated database whose verified name ends in `_test`.

#### Scenario: Complete idempotency verification

- **WHEN** the change verification suite runs
- **THEN** it covers a new claim, sequential duplicate, concurrent claim,
  rollback and retry, concurrent winner rollback and shared handler transaction
  against PostgreSQL
- **AND** no concurrency scenario is implemented as sequential calls
- **AND** the complete verification workflow is green before closing the
  change
