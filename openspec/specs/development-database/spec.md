# development-database Specification

## Purpose

Defines the local PostgreSQL development database that any contributor brings
up with one command: what it creates, with which privileges, how it matches
the CI database contract, how it persists and resets, which secrets it
requires, and how destructive verification stays away from it.

## Requirements

### Requirement: One command brings up a ready development database

The repository SHALL define, in a `docker-compose.yml` at its root, a single
PostgreSQL service such that `docker compose up -d --wait` on an empty data
volume exits with code 0 only after the service is healthy. At that point the
application role, the application database and its twin
`<application database>_test` SHALL exist, both databases owned by the
application role.

#### Scenario: First start on an empty volume

- **GIVEN** a `.env` with both required passwords and no existing data volume
  for the compose project
- **WHEN** `docker compose up -d --wait` runs
- **THEN** it exits with code 0 and the service status is `healthy`
- **AND** the application role exists
- **AND** the application database and `<application database>_test` exist,
  and `pg_database.datdba` of both is the application role

### Requirement: The application role has minimum privilege

The application role SHALL be able to log in and SHALL NOT be superuser, SHALL
NOT create databases, SHALL NOT create roles and SHALL NOT have replication
rights. The application role SHALL NOT be the image's bootstrap superuser: a
separate bootstrap superuser creates it and nothing else uses the bootstrap
superuser.

#### Scenario: Role attributes

- **WHEN** `pg_roles` is queried for the application role
- **THEN** `rolcanlogin` is true
- **AND** `rolsuper`, `rolcreatedb`, `rolcreaterole` and `rolreplication` are
  false

#### Scenario: Application role cannot create a database

- **GIVEN** a connection to the application database as the application role
- **WHEN** it runs `CREATE DATABASE probe`
- **THEN** the statement fails with SQLSTATE `42501`
- **AND** no database named `probe` exists

### Requirement: Healthy means usable by the application role

The service SHALL report `healthy` only when the application role can open a
TCP connection to `<application database>_test` with its password and run a
query. It SHALL NOT report `healthy` while the image's temporary
initialization server is running, since that server does not listen on TCP.

#### Scenario: Health probe uses the application role over TCP

- **WHEN** the service's healthcheck definition is inspected
- **THEN** it connects over TCP, not over the Unix socket
- **AND** it connects as the application role to `<application database>_test`

#### Scenario: Not healthy during initialization

- **GIVEN** a first start on an empty volume
- **WHEN** the initialization script is still running
- **THEN** the service is not reported as `healthy`
- **AND** `docker compose up -d --wait` has not exited yet

### Requirement: Initialization creates no schema

The initialization SHALL create only the application role and the two
databases. It SHALL NOT create tables, schemas, extensions or any other object
inside them: the schema comes only from Alembic migrations.

#### Scenario: Fresh application database has no tables

- **GIVEN** a first start on an empty volume
- **WHEN** `information_schema.tables` of the application database is queried
  for schemas other than `pg_catalog` and `information_schema`, before any
  migration
- **THEN** it returns no rows

### Requirement: The development database honours the CI contract

With `DATABASE_URL` pointing to the application database as the application
role, `alembic upgrade head` and `pytest -m integration` SHALL both exit with
code 0, and afterwards the application database SHALL still be at the Alembic
head with `alembic_version`, `customers` and `menu_items` present, the same
assertions as the CI "Application database intact" step.

#### Scenario: Migrations and integration suite against the new database

- **GIVEN** a freshly started development database with no tables
- **WHEN** `alembic upgrade head` runs from `backend/` and then
  `pytest -m integration` runs
- **THEN** both exit with code 0
- **AND** `alembic_version.version_num` of the application database equals
  the single head reported by `alembic heads`
- **AND** `customers` and `menu_items` exist in the application database

### Requirement: Image and collation match the CI database

The service SHALL use exactly the image tag the CI workflow uses for its
PostgreSQL service, today `postgres:16.15` (Debian variant), and SHALL NOT
override the image's locale. Both databases SHALL have `datcollate`
`en_US.utf8`, so the menu ordering test passes unmodified.

#### Scenario: Same image as CI

- **WHEN** the compose file and `.github/workflows/ci.yml` are inspected
- **THEN** the service image is exactly `postgres:16.15`, equal to the CI
  service image

#### Scenario: Same collation as CI

- **WHEN** `pg_database.datcollate` is queried for both databases
- **THEN** both values are `en_US.utf8`
- **AND** `test_order_follows_the_database_collation` passes without being
  modified

### Requirement: A unit test guards image parity with CI

A unit test, running without network or database, SHALL read the compose file
and the CI workflow and SHALL fail when the PostgreSQL image tag of the
compose service differs from the image tag of the CI PostgreSQL service.

#### Scenario: Image tag drifts

- **GIVEN** the compose image tag is changed to a value different from the CI
  service image
- **WHEN** `pytest -m unit` runs
- **THEN** the parity test fails naming both tags

#### Scenario: Image tags equal

- **GIVEN** both files declare `postgres:16.15`
- **WHEN** `pytest -m unit` runs
- **THEN** the parity test passes

### Requirement: Unit tests guard the compose file shape and secrecy

Unit tests, running without network or database, SHALL fail when the compose
file declares more than one service (`db`) or more than one top-level volume
(`pgdata`), when any service declares `network_mode`, or when the healthcheck
reads the application password without the doubled `$$` escape that keeps
Compose from writing its value into the healthcheck definition.

#### Scenario: Single-dollar password in the healthcheck

- **GIVEN** the healthcheck references `$POSTGRES_APP_PASSWORD` instead of
  `$$POSTGRES_APP_PASSWORD`
- **WHEN** `pytest -m unit` runs
- **THEN** the healthcheck guard fails

#### Scenario: Host networking declared

- **GIVEN** a service of the compose file declares `network_mode: host`
- **WHEN** `pytest -m unit` runs
- **THEN** the network guard fails

#### Scenario: Extra service or volume

- **GIVEN** the compose file declares a second service or a second top-level
  volume
- **WHEN** `pytest -m unit` runs
- **THEN** the file-shape guard fails

#### Scenario: Current compose file

- **GIVEN** the compose file of this change
- **WHEN** `pytest -m unit` runs
- **THEN** every compose guard passes

### Requirement: A unit test guards what the init script creates

A unit test, reading the init script as text, SHALL fail unless its role
statement carries each of `LOGIN`, `NOSUPERUSER`, `NOCREATEDB`,
`NOCREATEROLE` and `NOREPLICATION`, it has exactly two `CREATE DATABASE`
statements (the application database and its twin) both with the application
role as `OWNER`, and it contains no `CREATE TABLE`, `CREATE SCHEMA`,
`CREATE EXTENSION` nor `GRANT`, since the schema comes only from Alembic.

#### Scenario: Privilege attribute removed

- **GIVEN** the role statement of the init script lacks one of `NOSUPERUSER`,
  `NOCREATEDB`, `NOCREATEROLE` or `NOREPLICATION`
- **WHEN** `pytest -m unit` runs
- **THEN** the init script guard fails naming the missing attribute

#### Scenario: Third database or database without the role as owner

- **GIVEN** the init script has a third `CREATE DATABASE` statement, or one
  `CREATE DATABASE` without the application role as `OWNER`
- **WHEN** `pytest -m unit` runs
- **THEN** the init script guard fails

#### Scenario: Schema object or grant in the init script

- **GIVEN** the init script contains `CREATE TABLE`, `CREATE SCHEMA`,
  `CREATE EXTENSION` or `GRANT`
- **WHEN** `pytest -m unit` runs
- **THEN** the init script guard fails naming the statement

#### Scenario: Current init script passes the creation guards

- **GIVEN** the init script of this change, unmodified
- **WHEN** `pytest -m unit` runs
- **THEN** every creation guard of the init script passes

### Requirement: A unit test guards how the init script handles the password

A unit test, reading the init script as text, SHALL fail when the script
passes the password to `psql` with a `-v` assignment, when the SQL heredoc
delimiter is not quoted, or when the SQL block contains a `$` shell expansion.

#### Scenario: Password passed on the command line

- **GIVEN** the init script passes the password to `psql` with `-v`
- **WHEN** `pytest -m unit` runs
- **THEN** the init script guard fails

#### Scenario: Shell expansion inside the SQL

- **GIVEN** the SQL heredoc delimiter is unquoted, or the SQL block contains a
  `$` expansion
- **WHEN** `pytest -m unit` runs
- **THEN** the init script guard fails

#### Scenario: Current init script passes the password guards

- **GIVEN** the init script of this change, unmodified
- **WHEN** `pytest -m unit` runs
- **THEN** every password-handling guard of the init script passes

### Requirement: Data survives restarts and initialization runs once

Stopping and starting the service without removing its volume SHALL keep
every row. The initialization SHALL run only when the data volume is empty and
SHALL NOT run again on later starts. Running `up -d` while the service is
already running SHALL change nothing.

#### Scenario: Row survives down and up

- **GIVEN** a row inserted into a table of the application database
- **WHEN** `docker compose down` (without `-v`) and then
  `docker compose up -d --wait` run
- **THEN** the row is still there
- **AND** the service logs of that start show no initialization run and no
  error

#### Scenario: Second up is a no-op

- **GIVEN** the service is running and healthy
- **WHEN** `docker compose up -d` runs again
- **THEN** it exits with code 0
- **AND** the container is not recreated

### Requirement: Explicit reset is the only destructive operation

Removing the data volume SHALL be the only documented way to discard the
development data, and SHALL be done only with `docker compose down -v`. After
the reset, `up` SHALL recreate the same role and the two empty databases.

#### Scenario: Reset recreates an empty database

- **GIVEN** a development database with migrations applied and rows inserted
- **WHEN** `docker compose down -v` and then `docker compose up -d --wait` run
- **THEN** the application database and its twin exist with no tables
- **AND** the application role exists with the same attributes

#### Scenario: Password change requires reset

- **WHEN** the README section on the development database is read
- **THEN** it marks `down -v` as the only destructive command
- **AND** it states that changing a password in `.env` after the first start
  requires that reset, because initialization runs only on an empty volume

### Requirement: Passwords are mandatory and have no default

The bootstrap superuser password and the application role password SHALL be
required inputs with no default value in any file. When either is missing or
empty, `docker compose up` SHALL exit with code 1 with a message naming the
missing variable, before creating any container or volume.

#### Scenario: Missing bootstrap password

- **GIVEN** an environment and `.env` without the bootstrap password
- **WHEN** `docker compose up -d` runs
- **THEN** it exits with code 1
- **AND** the message names the bootstrap password variable
- **AND** no container and no volume of the compose project exist afterwards

#### Scenario: Missing application password

- **GIVEN** an environment and `.env` without the application role password
- **WHEN** `docker compose up -d` runs
- **THEN** it exits with code 1
- **AND** the message names the application password variable
- **AND** no container and no volume of the compose project exist afterwards

#### Scenario: No default password anywhere

- **WHEN** the compose file is inspected by the unit tests
- **THEN** both password variables are referenced with the required-value
  form (`:?`) and with no default value

### Requirement: No secret value is stored in the repository

The compose file, the initialization script, `.env.example` and the README
SHALL contain no password value. `.env` SHALL be ignored by Git and SHALL NOT
be tracked.

#### Scenario: Files inspected for secrets

- **WHEN** the compose file, the initialization script, `.env.example` and the
  README section are inspected
- **THEN** none of them contains a password value
- **AND** the compose file declares no literal value for either password

#### Scenario: .env stays out of Git

- **WHEN** `git ls-files .env` and `git check-ignore .env` run
- **THEN** the first prints nothing
- **AND** the second exits with code 0

### Requirement: The database listens only on loopback

The service SHALL publish its port only on `127.0.0.1`, mapping a configurable
host port to container port `5432`. The default host port SHALL be `5433`.

#### Scenario: Loopback binding with default port

- **GIVEN** no host port variable is set
- **WHEN** `docker compose config` runs
- **THEN** the published port has `host_ip` `127.0.0.1`, `published` `5433`
  and `target` `5432`

#### Scenario: Configurable host port

- **GIVEN** the host port variable is set to another free port
- **WHEN** the service starts
- **THEN** it is reachable on `127.0.0.1` at that port
- **AND** `docker compose port db 5432` reports `127.0.0.1:<that port>` and
  no other binding

### Requirement: Names are configurable and the twin is derived

The application role name and the application database name SHALL be
configurable with documented defaults (`bot_resto_user` and `bot_resto`). The
twin database name SHALL always be the application database name followed by
`_test`, the name the integration fixtures derive.

#### Scenario: Custom database name

- **GIVEN** the application database variable is set to `foo` on an empty
  volume
- **WHEN** `docker compose up -d --wait` runs
- **THEN** the databases `foo` and `foo_test` exist, both owned by the
  application role

### Requirement: Application configuration is unaffected

The compose variables SHALL be documented in `.env.example` only as comment
lines. The uncommented keys of `.env.example` SHALL remain exactly the
application settings fields, and the application settings SHALL load without
error when the compose variables are present in the environment.

#### Scenario: Existing environment template tests

- **WHEN** `pytest -m unit` runs
- **THEN** `test_env_example_lists_exactly_the_settings_keys` and
  `test_env_example_carries_no_real_values` pass without being edited

#### Scenario: Settings ignore compose variables

- **GIVEN** a valid application environment plus every compose variable set
- **WHEN** the application configuration loads
- **THEN** it loads without error

### Requirement: Shell scripts keep LF line endings

The repository SHALL declare LF line endings for every `.sh` file, so the
initialization script runs inside the container without carriage-return
errors from a Windows checkout.

#### Scenario: Windows checkout

- **GIVEN** a checkout on Windows
- **WHEN** `git ls-files --eol` runs on the initialization script
- **THEN** it shows `i/lf` and `w/lf`
- **AND** the script runs in the container on first start without an error
  caused by `\r`

### Requirement: Data lives outside the repository

The database data SHALL live in a named Docker volume, never in a bind mount
of a repository directory. Bringing the service up SHALL leave the working
tree unchanged.

#### Scenario: Working tree after up

- **GIVEN** the output of `git status --porcelain` recorded right before
- **WHEN** `docker compose up -d --wait` runs on an empty volume
- **THEN** `git status --porcelain` prints exactly the same output (empty on
  a clean tree)
- **AND** the data directory of the service is backed by a named volume

### Requirement: The database restarts on its own

The service SHALL use the restart policy `unless-stopped`, so it comes back
after a Docker or host restart unless a contributor stopped it on purpose.

#### Scenario: Restart policy

- **WHEN** `docker inspect` runs on the service container
- **THEN** `HostConfig.RestartPolicy.Name` is `unless-stopped`

### Requirement: Coexistence with the hand-made container

The compose project SHALL NOT use the container name `bot-resto-db`. The
README SHALL explain how to move from that container to the compose service:
stop the old one, note that its `botresto*` databases do not migrate on their
own, and how to keep hand-loaded data with `pg_dump` and a restore into
`bot_resto` with `--no-owner`.

#### Scenario: No name clash

- **WHEN** the compose file is inspected
- **THEN** it does not declare the container name `bot-resto-db`

#### Scenario: Migration documented

- **WHEN** the README section on the development database is read
- **THEN** it lists stopping `bot-resto-db`, that its databases do not migrate
  automatically, and the `pg_dump` plus restore with `--no-owner` path

### Requirement: Data volume is scoped to the compose project

The data volume SHALL be declared without a fixed global name and SHALL NOT be
external, so each compose project name gets its own volume and removing the
volume of one project never touches the volume of another.

#### Scenario: Volume declaration inspected

- **WHEN** the unit tests inspect the compose file
- **THEN** the service data directory uses a named volume declared at top
  level
- **AND** that volume has no `name` and no `external` key

### Requirement: Destructive verification never targets the development volume

Every verification of this change that removes volumes or data SHALL run with
the compose project name `bot-resto-verify` and a host port different from
the development one. No task SHALL run `down -v` or `docker volume rm` without
that project name, and no task SHALL run `docker system prune`.

#### Scenario: Development volume untouched by verification

- **GIVEN** `docker volume ls` output recorded before the verification
- **WHEN** the destructive verification steps run under `bot-resto-verify`
- **THEN** `docker volume ls` afterwards still lists the development volume
- **AND** its creation time from `docker volume inspect` is unchanged

### Requirement: The development database is documented

The README SHALL describe how to start, stop, reset, migrate and test against
the development database, what `down -v` removes, that initialization runs
only on an empty volume, and that passwords SHALL NOT contain characters that
require percent-encoding in a URL (D-15).

#### Scenario: Contributor sets up the database from the README

- **GIVEN** a contributor with Docker and no development database
- **WHEN** they follow the README section
- **THEN** they can start the database, apply migrations and run
  `pytest -m integration` against it

#### Scenario: Compose configuration output is not shared

- **WHEN** the README section is read
- **THEN** it warns that `docker compose config` prints the interpolated
  passwords and its output must not be pasted anywhere
