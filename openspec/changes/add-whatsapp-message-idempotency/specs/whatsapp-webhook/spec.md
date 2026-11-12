# WhatsApp webhook specification delta

## ADDED Requirements

### Requirement: Authenticate the original request body

The POST webhook MUST validate `X-Hub-Signature-256` before parsing the body,
opening a database Session or performing any effect. It MUST compute
HMAC-SHA256 over the exact bytes received from HTTP with
`WHATSAPP_APP_SECRET` and MUST compare the expected and received signatures
with `hmac.compare_digest`.

The system MUST NOT reserialize JSON before verification. It MUST NOT log the
request body, signature or app secret.

#### Scenario: Valid signature over the original bytes

- **GIVEN** a request body has been signed as `sha256=<hex digest>` with the
  configured app secret
- **WHEN** the POST webhook receives the unchanged bytes and that header
- **THEN** signature verification succeeds
- **AND** the request may proceed to the production-runtime availability check

#### Scenario: Reserialized JSON does not replace the original body

- **GIVEN** the original JSON bytes and a semantically equivalent
  reserialization have different byte representations
- **AND** the signature was computed over the original bytes
- **WHEN** the webhook verifies the request
- **THEN** the original bytes validate
- **AND** the reserialized bytes do not validate with that signature

#### Scenario: Missing or malformed signature

- **WHEN** the signature header is absent, does not use the `sha256` scheme or
  does not contain a valid SHA-256 hexadecimal digest
- **THEN** the endpoint responds with HTTP 403 and the uniform error body
- **AND** the parser is not invoked
- **AND** no database Session is opened
- **AND** no processed-message row or handler effect is created

#### Scenario: Incorrect signature

- **WHEN** the signature is well formed but does not match the original body
- **THEN** the endpoint responds with HTTP 403 and the uniform error body
- **AND** the parser is not invoked
- **AND** no database Session is opened
- **AND** no processed-message row or handler effect is created

### Requirement: Fail closed until the real production runtime exists

The production application composition MUST NOT import, instantiate or use the
test parser, handler, handler factory or dispatcher as defaults. Test doubles
MUST be supplied only by test fixtures, a test application factory or explicit
FastAPI dependency overrides.

Until `add-whatsapp-cloud-api-roundtrip` provides the approved production
composition, a correctly signed POST request MUST return the existing opaque
HTTP 503 Service Unavailable response before parser invocation or database
access. Change 4b MUST replace the unavailable production provider without
changing the route, ports or transaction boundary.

#### Scenario: Signed webhook cannot use test doubles in production

- **GIVEN** the production application is running before change 4b
- **AND** no real WhatsApp runtime has been composed
- **WHEN** a correctly signed POST request reaches the webhook
- **THEN** the endpoint responds with the uniform HTTP 503 response
- **AND** it does not invoke a test or real parser, handler or dispatcher
- **AND** it does not open a database Session or call the idempotency repository
- **AND** it does not persist a processed-message row or handler effect
- **AND** a retry after 4b installs the real runtime can be processed normally

#### Scenario: Authentication still precedes availability

- **GIVEN** the production WhatsApp runtime is unavailable
- **WHEN** a request carries an invalid signature
- **THEN** the endpoint responds with HTTP 403 rather than HTTP 503
- **AND** it performs no parser, database, handler or dispatcher effect

### Requirement: Accept validated messages through an injected parser port

After authentication, the webhook orchestration MUST give the original bytes
to an injected batch parser. The parser MUST return a sequence of validated,
typed message objects that expose an opaque, non-empty `message_id`. The
transactional handler MUST NOT receive a raw dictionary.

Because a Protocol does not validate at runtime, the 4a orchestration MUST
validate the entire parser result before opening the first database Session.
Every `message_id` MUST be a `str` containing at least one non-whitespace
character. Validation MUST NOT trim or otherwise normalize the opaque value.

This change MUST provide the parser contract and test it with a fake. Parsing
the real Meta payload, normalizing customer data, ignoring status updates and
handling unsupported message types belong to
`add-whatsapp-cloud-api-roundtrip`.

#### Scenario: Parser returns multiple validated messages

- **WHEN** the authenticated parser returns multiple typed messages
- **AND** every `message_id` satisfies the runtime identity invariant
- **THEN** the webhook processes them in parser order
- **AND** idempotency uses each object's `message_id`

#### Scenario: Parser returns an invalid message identity

- **WHEN** the configured parser returns any message whose `message_id` is not
  a string, is empty or contains only whitespace
- **THEN** the endpoint responds with a uniform 5xx response
- **AND** no database Session is opened for any message in that batch
- **AND** the idempotency repository, handler and dispatcher are not invoked
- **AND** the invalid value is not included in logs or the HTTP response

#### Scenario: Parser returns no inbound messages

- **WHEN** the authenticated parser returns an empty sequence
- **THEN** the webhook responds with HTTP 200 without opening a message
  transaction or dispatching an action

#### Scenario: Parser implementation is replaceable in 4b

- **WHEN** the unavailable production runtime is replaced by a Meta parser
  that returns a concrete Pydantic message type satisfying the same contract
- **THEN** the idempotency orchestration, transaction boundary and handler
  contract do not change

### Requirement: Process a parsed batch one transaction at a time

The webhook MUST process each parsed `message_id` with its own Session and
`READ COMMITTED` transaction. Transactional failures use a fail-fast policy:
messages after the failed item are not processed during that request.

#### Scenario: Every message commits independently

- **GIVEN** a parsed batch contains multiple new messages
- **WHEN** every handler succeeds
- **THEN** each claim and handler effect commits independently
- **AND** each message's post-commit actions are dispatched only after that
  message commits

#### Scenario: One message in a batch fails

- **GIVEN** earlier messages in a batch committed successfully
- **WHEN** a later message fails before commit
- **THEN** the successful messages remain committed
- **AND** the failed message leaves no committed claim or partial handler effect
- **AND** no later message in that request is processed
- **AND** the webhook responds with a 5xx status

#### Scenario: Retry a partially successful batch

- **WHEN** the same authenticated batch is retried
- **THEN** previously committed message IDs resolve as duplicates
- **AND** their handlers and external actions are not repeated
- **AND** the rolled-back message and messages not reached after it can be
  claimed and processed normally

### Requirement: Dispatch descriptive actions only after commit

The transaction-bound handler MUST return descriptive actions without calling
external services. The webhook orchestration MUST pass those actions to an
injected dispatcher only after the corresponding message transaction commits.

The dispatcher contract MUST be replaceable by the WhatsApp Cloud API adapter
in 4b without changing the handler or transaction orchestration.

#### Scenario: Transaction rolls back

- **WHEN** message processing rolls back
- **THEN** none of that attempt's actions is passed to the dispatcher

#### Scenario: Transaction commits

- **WHEN** a new message and its handler effects commit
- **THEN** its actions may be dispatched after the transaction context has
  closed successfully
- **AND** the dispatcher has no database Session

#### Scenario: External dispatch fails

- **GIVEN** the message transaction already committed
- **WHEN** its dispatcher raises an external-service error
- **THEN** the committed claim and handler effects remain committed
- **AND** the failure is logged without request content, token, complete phone
  number or address
- **AND** later messages in the batch continue processing
- **AND** the webhook still responds with HTTP 200 if no transaction failed

#### Scenario: Process crashes after commit and before dispatch

- **WHEN** the database transaction commits
- **AND** the process crashes before an external dispatch completes
- **THEN** the committed `message_id` remains deduplicated
- **AND** the system does not claim exactly-once external delivery

### Requirement: Return stable and opaque webhook outcomes

The POST webhook MUST declare a response model and preserve the uniform error
contract. It MUST return HTTP 200 without a useful response body when all
parsed messages either commit successfully or are committed duplicates. It
MUST return HTTP 403 for an invalid signature and a 5xx status for a
contract or transactional processing failure. Before 4b provides a real
production runtime, a validly signed production request MUST return HTTP 503.

#### Scenario: Process a new message

- **WHEN** a validly signed new message is claimed and handled successfully
- **THEN** the endpoint responds with HTTP 200 without a useful body

#### Scenario: Receive a committed duplicate

- **WHEN** a validly signed duplicate `message_id` is detected
- **THEN** the endpoint responds with HTTP 200 without a useful body
- **AND** it does not repeat the handler or post-commit actions

#### Scenario: Handler failure is retryable

- **WHEN** the handler raises and the message transaction rolls back
- **THEN** the response is a uniform 5xx without exception text, SQL, table
  names, stacktrace or customer data

### Requirement: Log webhook outcomes once and without sensitive data

The system MAY log the `message_id`, duration and an outcome of `claimed`,
`duplicate`, `rolled_back` or `dispatch_failed`. It MUST NOT log the full
webhook payload, message body, signature, app secret, complete phone number,
customer name, delivery address, SQL parameters or a database exception's
message.

A transactional failure MUST reuse the opaque error-chain handling of the
application error boundary (`D-25`) and MUST produce one error record, not one
in the webhook plus another in middleware.

#### Scenario: Transactional processing fails

- **WHEN** message processing raises an error that may carry SQL parameters or
  driver detail
- **THEN** exactly one error record can be correlated by `message_id`
- **AND** it contains the safe error type and opaque stack metadata
- **AND** it contains no exception message, SQL parameters, payload or customer
  data

#### Scenario: Duplicate is logged safely

- **WHEN** a committed duplicate is observed and an outcome record is emitted
- **THEN** the record may contain `message_id` and `duplicate`
- **AND** it contains no request body or customer data

### Requirement: Preserve the future global lock order

The webhook orchestration MUST complete the idempotency claim before building
or invoking the injected handler. A duplicate MUST stop before the handler.

The real Conversation, Order and Reservation locks are explicitly deferred to
`add-conversation-state-machine`, which MUST implement and test the full order
required by `CONV-9`.

#### Scenario: New message reaches the extension point after claim

- **WHEN** the injected handler is invoked for a new message
- **THEN** the idempotency insert has already succeeded in its transaction

#### Scenario: Duplicate stops before future locks

- **WHEN** the idempotency insert identifies a duplicate
- **THEN** the injected handler is not constructed or invoked
- **AND** no future Conversation, Order or Reservation lock path can run
