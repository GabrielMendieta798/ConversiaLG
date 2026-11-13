# Tasks: Add WhatsApp inbound message idempotency

No Python implementation task starts until this adjusted proposal, design and
spec are explicitly approved. Groups are sequential human checkpoints: finish
and verify one group, show its evidence, and stop before the next group.

The five checked tasks predate the option-A adjustment. They identify existing
code, not final verification of the change; the pending tests below must still
prove their behavior.

## Group 1 — Migration and atomic repository foundation

### 1. Data model and migration

- [x] 1.1 Add the `ProcessedWhatsAppMessage` SQLAlchemy model.
- [x] 1.2 Export the model so Alembic metadata includes it.
- [x] 1.3 Confirm `57f88cf9ee63` is a new revision after `88d85f44fb7d` and
  that the initial or other historical migrations were not edited for 4a.
- [x] 1.4 Review `upgrade()` for a `TEXT` primary key and non-null
  `TIMESTAMPTZ` `received_at` with a server-side `now()` default.
- [x] 1.5 Review `downgrade()` so it removes only
  `processed_whatsapp_messages`.
- [x] 1.6 Add a dedicated PostgreSQL migration test that runs
  `88d85f44fb7d -> 57f88cf9ee63 -> 88d85f44fb7d`, inspects the complete table
  contract and proves the parent schema is otherwise unchanged.
- [x] 1.7 Run the migration test only through the guarded dedicated `_test`
  database fixture; do not run destructive schema reset against development.

### 2. Idempotency repository

- [x] 2.1 Claim with PostgreSQL
  `INSERT ... ON CONFLICT DO NOTHING RETURNING message_id`.
- [x] 2.2 Return the explicit `MessageClaimResult.CLAIMED` or
  `MessageClaimResult.DUPLICATE` contract.
- [x] 2.3 Keep commit, rollback and transaction begin outside the repository.
- [x] 2.4 Add unit contract tests for claimed/duplicate return values without
  treating them as concurrency evidence.
- [x] 2.5 Add PostgreSQL integration tests for a new claim, a committed
  sequential duplicate, invisibility before caller commit and caller rollback.
- [x] 2.6 Extend the transaction-control guard to cover the WhatsApp
  idempotency repository or add an equivalent focused guard.
- [ ] 2.7 Run the complete verification workflow and show the Group 1 results
  before checking tasks or starting Group 2.
  Current blocker: the unchanged `origin/develop` menu-collation test requires
  `en_US`, while both local PostgreSQL databases report `es_AR.UTF-8`. All
  Group 1 focused tests pass; no menu code or database collation was changed.

## Group 2 — Signature, injectable contracts and shared transaction

### 3. Configuration and signature verification

- [ ] 3.1 Add required `WHATSAPP_APP_SECRET` configuration with a
  representation that hides its value; add the empty key to `.env.example`.
- [ ] 3.2 Update configuration fixtures and tests for present, missing, empty
  and non-leaking app secret behavior.
- [ ] 3.3 Verify `X-Hub-Signature-256` as `sha256=<hex digest>` using
  HMAC-SHA256 over the original request bytes and `hmac.compare_digest`.
- [ ] 3.4 Test valid, invalid, missing and malformed signatures, including a
  body whose reserialized JSON has different bytes.
- [ ] 3.5 Prove an invalid signature returns uniform 403 before parser
  invocation, Session creation, claim, handler or dispatcher effects.

### 4. Stable ports and per-message orchestration

- [ ] 4.1 Add the typed inbound-message identity, injected batch-parser,
  transaction-bound handler factory, handler and post-commit dispatcher
  Protocols described by the design.
- [ ] 4.2 Add fakes for those ports without implementing the Meta parser or
  Cloud API adapter from 4b; keep them in test-only modules and out of every
  production default.
- [ ] 4.3 Compose production with an explicit unavailable WhatsApp runtime
  until 4b: after valid authentication return uniform 503 without parser,
  Session, repository, handler or dispatcher activity. Prove an invalid
  signature still returns 403 first.
- [ ] 4.4 Validate the complete parsed batch before opening the first Session:
  every `message_id` is a `str` with non-whitespace content, is not normalized,
  and an invalid value returns opaque 5xx without reaching the repository.
- [ ] 4.5 Build the handler from the active Session only after a successful
  claim; never construct or invoke it for a duplicate.
- [ ] 4.6 Open a fresh Session and explicit PostgreSQL `READ COMMITTED`
  transaction for each parsed message (`D-32`).
- [ ] 4.7 Commit the claim and all handler database effects together and
  retain only descriptive actions for post-commit dispatch.
- [ ] 4.8 Roll back claim and partial handler effects on failure, discard its
  actions and return uniform 5xx so the same `message_id` can be retried.
- [ ] 4.9 Return 200 without a useful body for a committed new message and a
  committed duplicate; declare a response model for the route.
- [ ] 4.10 Correct the existing prefix guard so the exact `/webhook` route and
  true subpaths both satisfy the approved `application-runtime` spec.
- [ ] 4.11 Add API and PostgreSQL tests for production fail-closed composition,
  invalid identity rejection, handler injection, same-transaction commit,
  duplicate short-circuit, rollback and successful retry.
- [ ] 4.12 Assert `SHOW transaction_isolation` is `read committed` inside the
  handler transaction.
- [ ] 4.13 Run the complete verification workflow and show the Group 2 results
  before checking tasks or starting Group 3.

## Group 3 — Batches, post-commit dispatch and safe logging

### 5. Independent batch outcomes

- [ ] 5.1 Process parser results in order with a separate Session and
  transaction for every `message_id`.
- [ ] 5.2 Implement fail-fast for transactional failures: preserve earlier
  commits, roll back the failed item, leave later items untouched and return
  5xx.
- [ ] 5.3 Test retry of a partially successful batch: prior commits resolve as
  duplicates and failed/not-reached messages process normally.
- [ ] 5.4 With an explicitly configured test runtime, prove an empty parser
  result returns 200 with no Session, claim, handler or dispatcher invocation;
  the production composition before 4b must still return 503.

### 6. Post-commit effects

- [ ] 6.1 Invoke the injected dispatcher only after the corresponding message
  transaction has committed and closed.
- [ ] 6.2 Prove rollback dispatches nothing.
- [ ] 6.3 Treat dispatcher failure as post-commit: retain committed data, log
  safely, continue later batch messages and return 200 when no transaction
  failed.
- [ ] 6.4 Document and test that 4a does not guarantee exactly-once external
  delivery or close the commit-to-send gap.

### 7. Opaque and correlated logging

- [ ] 7.1 Reuse the existing opaque error-chain treatment so a transactional
  failure produces exactly one error record correlated by `message_id`
  (`D-25`).
- [ ] 7.2 Never log `str(exc)`, SQL parameters, raw payload, message body,
  signature, app secret, full phone, customer name or address.
- [ ] 7.3 Add tests for claimed, duplicate, rolled-back and dispatch-failed
  outcomes and scan responses/logs for the sensitive test values.
- [ ] 7.4 Run the complete verification workflow and show the Group 3 results
  before checking tasks or starting Group 4.

## Group 4 — Real concurrency and final verification

### 8. PostgreSQL concurrency

- [ ] 8.1 Test two threads with independent PostgreSQL connections and
  transactions processing the same new `message_id` at the same time.
- [ ] 8.2 Prove exactly one claim and one handler database effect commit and
  the loser resolves as duplicate.
- [ ] 8.3 Test a first claimant held open while the second waits; after the
  first rolls back, prove the second can claim and commit exactly one effect.
- [ ] 8.4 Use barriers with timeouts, abort them on worker failure, join all
  threads before cleanup and verify distinct PostgreSQL backend connections.
- [ ] 8.5 Reject sequential calls as evidence for any concurrency scenario.

### 9. Scope and deferred lock verification

- [ ] 9.1 Confirm 4a invokes the handler only after claim and never for a
  duplicate.
- [ ] 9.2 Confirm no ConversationRepository, conversation state machine or
  Order/Reservation lock was introduced by this change.
- [ ] 9.3 Record for `add-conversation-state-machine` the inherited real-lock
  scenarios: claim -> Conversation -> Order/Reservation and crossed flows
  without deadlock.
- [ ] 9.4 Confirm the production application imports or defaults to no test
  double and remains fail-closed until 4b supplies the real runtime.
- [ ] 9.5 Confirm no real Meta parser, GET verification endpoint or Cloud API
  adapter was implemented in 4a.

### 10. Final verification and documentation

- [ ] 10.1 Map every scenario in all 4a delta specs to a test and identify any
  weak assertion, skip, xfail or sequential concurrency substitute.
- [ ] 10.2 Run `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run mypy backend/app`, `uv run pytest -m unit` and
  `uv run pytest -m integration` twice in succession.
- [ ] 10.3 Validate the OpenSpec change in strict mode when the CLI is
  available; otherwise perform and report the equivalent manual structural
  validation without claiming the CLI ran.
- [ ] 10.4 Review the complete diff against `origin/develop`, conflict
  markers, changed-file scope, secrets, signature handling, transaction
  ownership, isolation, concurrency and logging.
- [ ] 10.5 Perform the required security review; correct every medium/high
  finding before closure.
- [ ] 10.6 Document the commit-to-send gap and remaining risks.
- [ ] 10.7 Update `ESTADO.md` and the README session log only when the change
  closes.

## Deferred to `add-conversation-state-machine` — not 4a tasks

The following scenarios remain mandatory under `CONV-9` but are intentionally
not checkboxes of this change because the required repositories do not exist:

- acquire a real Conversation row lock after the idempotency claim;
- acquire a real Order or Reservation row lock after Conversation;
- stop a committed duplicate before any of those locks;
- run crossed concurrent flows and prove the full order does not deadlock.
