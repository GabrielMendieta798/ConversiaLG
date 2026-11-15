# Runtime configuration delta for WhatsApp signature verification

## ADDED Requirements

### Requirement: WhatsApp app secret comes only from the environment

The application MUST require a non-empty `WHATSAPP_APP_SECRET` environment
setting before it can start the POST webhook. The secret MUST NOT come from a
YAML file or source code and MUST be hidden from model representations,
configuration errors, logs and HTTP responses (`GEN-3`, `SEC-1`, `SEC-2`).

This change MUST NOT add the Meta verify token, access token, phone number ID or
Graph API version; those settings belong to
`add-whatsapp-cloud-api-roundtrip`.

#### Scenario: App secret is present

- **WHEN** the environment provides a non-empty `WHATSAPP_APP_SECRET` together
  with the other required configuration
- **THEN** configuration loads successfully
- **AND** the POST signature verifier can use the secret

#### Scenario: App secret is missing

- **WHEN** `WHATSAPP_APP_SECRET` is absent or empty
- **THEN** configuration validation prevents application startup
- **AND** the error names `WHATSAPP_APP_SECRET` as missing
- **AND** it does not reveal any other setting value

#### Scenario: Secret representation is safe

- **WHEN** settings or the full application configuration are rendered with
  `str` or `repr`
- **THEN** the app secret does not appear

#### Scenario: Environment template documents the key without a value

- **WHEN** `.env.example` is inspected
- **THEN** it contains `WHATSAPP_APP_SECRET=`
- **AND** the committed value is empty
- **AND** the template still contains no real credential or token

#### Scenario: Signature failures do not reveal the secret

- **WHEN** signature verification fails for any reason
- **THEN** the app secret appears in neither logs nor the HTTP response
