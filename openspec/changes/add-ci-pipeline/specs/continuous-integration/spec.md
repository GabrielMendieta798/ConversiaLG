## Purpose

Defines the automatic gate every Pull Request towards `develop` or `main` must
pass: what it verifies, in which order, against which database, and the
isolation, secrecy and reproducibility guarantees it gives every contributor.

## ADDED Requirements

### Requirement: Gate runs on every Pull Request to the protected branches

The repository SHALL define a CI check that runs automatically on every Pull
Request whose base branch is `develop` or `main`, on opening, on every new
commit and on reopening. The check SHALL only read the repository: it SHALL
NOT deploy, publish images or packages, create releases, or write to any
branch.

#### Scenario: Pull Request to develop

- **WHEN** a Pull Request targeting `develop` is opened or receives a commit
- **THEN** the CI check runs on that commit

#### Scenario: Pull Request to main

- **WHEN** a Pull Request targeting `main` is opened or receives a commit
- **THEN** the CI check runs on that commit

#### Scenario: Pull Request to another branch

- **WHEN** a Pull Request targets a branch other than `develop` or `main`
- **THEN** the CI check does not run

#### Scenario: Read-only token

- **WHEN** the workflow definition is inspected
- **THEN** the token permissions granted to the job are exactly `contents: read`
- **AND** no step deploys, publishes, releases or pushes

### Requirement: Verification steps mirror run-verify and stop at the first failure

The check SHALL run, in this order, the five verification steps of
`run-verify`: lint, format check, strict typecheck of `backend/app`, unit
suite, integration suite. The check SHALL stop at the first failing step and
SHALL NOT run any later step. Every step SHALL appear individually in the
check report.

#### Scenario: Everything green

- **GIVEN** a Pull Request whose code passes lint, format, typecheck, unit and
  integration
- **WHEN** the check runs
- **THEN** the check passes
- **AND** the report shows the five verification steps, each one passed

#### Scenario: Lint error

- **GIVEN** a Pull Request that introduces a lint violation
- **WHEN** the check runs
- **THEN** the check fails at the lint step
- **AND** the format, typecheck, unit and integration steps do not run

#### Scenario: Format error

- **GIVEN** a Pull Request with a file the formatter would rewrite and no lint
  violation
- **WHEN** the check runs
- **THEN** the check fails at the format step
- **AND** the typecheck, unit and integration steps do not run

#### Scenario: Typecheck error

- **GIVEN** a Pull Request that introduces a strict typecheck error in
  `backend/app` and passes lint and format
- **WHEN** the check runs
- **THEN** the check fails at the typecheck step
- **AND** the unit and integration steps do not run

#### Scenario: Broken unit test

- **GIVEN** a Pull Request with a failing unit test that passes lint, format
  and typecheck
- **WHEN** the check runs
- **THEN** the check fails at the unit step
- **AND** the integration step does not run

### Requirement: Integration suite runs against a real, fresh PostgreSQL

The integration suite SHALL run against a PostgreSQL 16 instance created for
that run and discarded after it, never SQLite and never a shared database.
Before the suite starts, the check SHALL wait until the database accepts
connections, SHALL create the application database and its twin
`<application database>_test`, both owned by the role the tests connect with,
and SHALL apply every Alembic migration up to head on the application
database. An unreachable database SHALL fail the check; integration tests
SHALL never be reported as skipped or passed without a database.

#### Scenario: Broken integration test

- **GIVEN** a Pull Request with a failing integration test that passes every
  earlier step
- **WHEN** the check runs
- **THEN** the check fails at the integration step

#### Scenario: Database unreachable

- **GIVEN** the database the integration suite is configured to use does not
  accept connections
- **WHEN** the check runs
- **THEN** the check fails
- **AND** no integration test is reported as passed or skipped

#### Scenario: Migrations applied before the suite

- **WHEN** the integration suite starts
- **THEN** the application database is at the Alembic head revision
- **AND** the `<application database>_test` database exists and is owned by
  the same role

### Requirement: Destructive migration tests never touch the application database

Integration tests that reset a schema SHALL run only against
`<application database>_test`. After the integration suite, the check SHALL
confirm that the application database still holds the schema produced by
`alembic upgrade head`, and SHALL fail otherwise.

#### Scenario: Migration tests use the twin database

- **GIVEN** the integration suite includes the destructive migration tests
- **WHEN** the check runs
- **THEN** those tests run against `<application database>_test`
- **AND** after the suite the application database is still at the Alembic
  head revision with its tables present

#### Scenario: Application database damaged by the suite

- **GIVEN** the integration suite leaves the application database without the
  Alembic head revision or without its tables
- **WHEN** the check runs
- **THEN** the check fails after the integration step

### Requirement: Conflict markers block the Pull Request before any test

The check SHALL fail, before installing dependencies or running any
verification step, when any version-controlled file contains a line that
starts with a conflict opening marker (the regular expression `<{7}`: seven
`<` characters) or a conflict closing marker (`>{7}`: seven `>` characters).
A real conflict always leaves both. The failure SHALL name every offending
file and line.

No file of this change (spec, design, `README`, workflow definition) SHALL
contain either marker literally at the start of a line: wherever the markers
must be named, they SHALL be written as a quantified pattern, so the check
never fails on its own definition.

#### Scenario: File with conflict markers

- **GIVEN** a Pull Request where a tracked file contains a line starting with
  the opening marker and a later line starting with the closing marker
- **WHEN** the check runs
- **THEN** the check fails at the conflict-marker step
- **AND** the report names the file and line of each marker
- **AND** none of the five verification steps runs

#### Scenario: No conflict markers

- **GIVEN** no tracked file has a line starting with the opening or the
  closing marker
- **WHEN** the check runs
- **THEN** the conflict-marker step passes

#### Scenario: The check does not flag its own definition

- **GIVEN** the change's spec, design, `README` section and workflow
  definition, which describe the markers
- **WHEN** the conflict-marker step runs on them
- **THEN** it reports no offending line

### Requirement: Lockfile drift fails the check

The check SHALL fail when `uv.lock` is not in sync with `pyproject.toml`, and
SHALL install dependencies exactly as recorded in `uv.lock`, without resolving
or rewriting it. Python SHALL be version 3.12.

#### Scenario: Declarations and lockfile out of sync

- **GIVEN** a Pull Request that changes a dependency in `pyproject.toml`
  without regenerating `uv.lock`
- **WHEN** the check runs
- **THEN** the check fails before any of the five verification steps

#### Scenario: Installation from the lockfile

- **GIVEN** `uv.lock` is in sync with `pyproject.toml`
- **WHEN** the check installs dependencies
- **THEN** the installed versions are exactly those in `uv.lock`
- **AND** `uv.lock` is not modified

### Requirement: No real secrets and no environment dump

The workflow SHALL NOT reference any GitHub secret. The database credentials it
uses SHALL be throwaway test values declared in the workflow itself, valid only
for the database container of that run. No step SHALL print the environment or
trace shell commands with their expanded values.

#### Scenario: Workflow inspected for secrets

- **WHEN** the workflow definition is inspected
- **THEN** it contains no reference to `secrets`
- **AND** every credential it declares is a test value with no use outside the
  run's database container

#### Scenario: Workflow inspected for environment dumps

- **WHEN** the workflow definition is inspected
- **THEN** no step prints the whole environment or enables shell command
  tracing

### Requirement: Runs are reproducible and superseded runs are cancelled

Two runs of the check on the same commit SHALL produce the same result: no run
SHALL depend on database state, files or caches left by a previous run, other
than the dependency download cache keyed by the lockfile. When a newer commit
is pushed to a Pull Request, any run still in progress for that Pull Request
SHALL be cancelled.

#### Scenario: Same commit twice

- **GIVEN** a commit whose check already ran
- **WHEN** the check is re-run on the same commit
- **THEN** it produces the same result, step by step

#### Scenario: New commit while a run is in progress

- **GIVEN** a run in progress for a Pull Request
- **WHEN** a new commit is pushed to that Pull Request
- **THEN** the run in progress is cancelled
- **AND** a new run starts for the new commit

### Requirement: CI is documented and reproducible locally

The `README` SHALL describe what the check runs and in which order, the exact
commands to reproduce it on a local machine (including the two databases and
the migration step), and that requiring the check on `develop` and `main` is a
manual branch-protection setting in GitHub.

#### Scenario: Contributor reproduces a red check locally

- **GIVEN** a contributor whose Pull Request failed the check
- **WHEN** they follow the `README` instructions
- **THEN** they can run the same steps, in the same order, on their machine
