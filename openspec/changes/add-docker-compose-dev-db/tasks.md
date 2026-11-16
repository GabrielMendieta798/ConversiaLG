# Tasks: Add Docker Compose development database

No implementation task starts until proposal, design and spec are explicitly
approved (`SDD-1`). Nothing under `backend/`, `config/`, `pyproject.toml`,
`uv.lock`, `.github/workflows/ci.yml` or the existing tests changes in this
change; if something forces it, stop and propose a new scenario (`SDD-2`).

Working rules for every group:

- Apply one group at a time and stop at the end of the group for Lautaro's OK.
- Lautaro runs every `git add`, `commit` and `push`. Each group lists the exact
  paths and the commit message; types are `feat`, `fix`, `chore`, `test`,
  `refactor` (there is no `ci:` nor `docs:`).
- **run-verify**, as the CI runs it, each step separately and stopping at the
  first red: `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run mypy backend/app`, `uv run pytest -m unit`,
  `uv run pytest -m integration`. Never `uv run pytest` without a marker (open
  logging bug in `backend/alembic/env.py:25`). Integration runs against the
  database configured in `.env`; if it is down, stop and ask Lautaro to start
  it. The agent never starts, stops or modifies the development database.
- **Destructive commands (critical human checkpoint).** Every Compose command
  in group 3 goes through the wrapper `$VERIFY_DIR/vc.sh` created in 3.1
  (design D12), written below as `vc.sh`. `$VERIFY_DIR` is a temporary
  directory **outside the repository**. From 3.2 to 3.11 `docker compose` is
  never typed by hand: only `vc.sh` is invoked. Plain `docker` read-only
  commands (`docker volume ls`, `docker volume inspect`, `docker ps`,
  `docker inspect`) are allowed. No task runs `down -v` or `docker volume rm`
  outside `vc.sh`. No task runs `docker system prune` or `docker volume prune`.
  `docker compose config` output is never printed in full (`SEC-2`): it is
  filtered in the same command.
- **Two `DATABASE_URL`s, never mixed.** The verification `DATABASE_URL` exists
  only inside the `vc.sh` process; it is never exported in the main shell.
  Every run-verify (1.4, 2.5, 3.14, 4.5) uses the `DATABASE_URL` from `.env`.
  Before each run-verify, confirm with `printenv DATABASE_URL` exiting 1 (no
  value printed) that the main shell does not override `.env`.

## 1. Compose definition and init script

- [x] 1.1 Create `.gitattributes` at the repository root with `*.sh text eol=lf` (design D1); verify with `git check-attr text eol -- docker/postgres/init/10-create-app-role-and-databases.sh` reporting `text: set` and `eol: lf`.
- [x] 1.2 Create `docker/postgres/init/10-create-app-role-and-databases.sh` (design D4): validates the role and database names, then with `psql -v ON_ERROR_STOP=1` and psql variables (`:"…"`, `:'…'`) creates the application role `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION` and the databases `<POSTGRES_APP_DB>` and `<POSTGRES_APP_DB>_test` owned by it. No tables, schemas, extensions or grants; no `exit` at top level, no `set -x`, no echo of variables. Verify by reading it against D4 and with `git grep -nE 'PASSWORD *=|password *[:=] *[^$:]' -- docker/` returning nothing.
- [x] 1.3 Create `docker-compose.yml` (design D2, D5 to D9): `name: bot-resto-dev`; one service `db` with `image: postgres:16.15`; `environment` with `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?…}`, `POSTGRES_APP_PASSWORD: ${POSTGRES_APP_PASSWORD:?…}`, `POSTGRES_APP_USER: ${POSTGRES_APP_USER:-bot_resto_user}`, `POSTGRES_APP_DB: ${POSTGRES_APP_DB:-bot_resto}`; port `"127.0.0.1:${POSTGRES_HOST_PORT:-5433}:5432"`; named volume `pgdata` at `/var/lib/postgresql/data`, declared at top level without `name` or `external`; the init script mounted read-only as a single file; the TCP healthcheck of D5 with `$$` escapes and bounded retries; `restart: unless-stopped`; no `container_name`. Verify by reading it against D2 and D5 to D9 and with `git grep -n 'container_name' -- docker-compose.yml` returning nothing.
- [x] 1.4 Run run-verify; all five steps green (nothing in Python changed, this proves no regression).
  - 2026-11-16, local (Windows), against the `.env` database; `printenv DATABASE_URL` exited 1 first:
    - `uv run ruff check .`: All checks passed.
    - `uv run ruff format --check .`: 108 files already formatted.
    - `uv run mypy backend/app`: no issues found in 35 source files.
    - `uv run pytest -m unit`: 246 passed, 46 deselected.
    - `uv run pytest -m integration`: 46 passed, 246 deselected.
  - Only warning: `StarletteDeprecationWarning`, not blocking.
  - Extra checks for 1.1 to 1.3: `bash -n` on the init script OK; init script and compose have no `\r`; `docker compose --env-file /dev/null config --quiet` with dummy passwords exited 0 (nothing printed, nothing created).
  - Design D4 adjusted during the apply: psql reads the variables with `\getenv` instead of `-v`, so the password never appears on the `psql` command line; the session sets `log_min_error_statement = panic` and `log_statement = 'none'`.
- [x] 1.5 Commit (Lautaro). Paths: `.gitattributes`, `docker-compose.yml`, `docker/postgres/init/10-create-app-role-and-databases.sh`, `openspec/changes/add-docker-compose-dev-db/tasks.md`. Message: `feat(add-docker-compose-dev-db): add compose development database with init script and LF rule for shell scripts`. After the commit, verify `git ls-files --eol docker/postgres/init/10-create-app-role-and-databases.sh` shows `i/lf` and `w/lf`.

## 2. Static guards and environment template

- [x] 2.1 Create `tests/unit/test_dev_database_compose.py` (design D11) with one test per guarded property: image equals `jobs.verify.services.postgres.image` of `ci.yml` and is an exact non-alpine tag; every published port starts with `127.0.0.1:` and targets `5432`; both passwords are exactly `${VAR:?…}` references (no literal, no default); data directory on a top-level named volume without `name`/`external`, and no other bind mount than the read-only init script; no `container_name: bot-resto-db`; `restart: unless-stopped`; healthcheck uses `127.0.0.1` and the `_test` database. Verify `uv run pytest -m unit tests/unit/test_dev_database_compose.py` passes.
  - 2026-11-16: 11 passed (10 compose guards, the password one parametrized for both variables, plus the 2.3 test). The container-name guard is stricter than the minimum: no `container_name` at all (design D6), which also excludes `bot-resto-db`. The healthcheck guard also checks the app role (`-U "$$POSTGRES_APP_USER"`) and that `pg_isready` is not used.
- [x] 2.2 Prove the parity guard fails (scenario "Image tag drifts"): temporarily change the compose image to `postgres:16.14`, run the parity test and record that it fails naming both tags; restore `postgres:16.15` and confirm `git diff --exit-code docker-compose.yml` exits 0.
  - 2026-11-16: with `postgres:16.14`, `test_image_matches_the_ci_postgres_service` failed: `AssertionError: compose image 'postgres:16.14' differs from CI image 'postgres:16.15'`. Restored; `git diff --exit-code docker-compose.yml` exited 0.
- [x] 2.3 Add to the same test file a test that loads the configuration with the `config_env` fixture plus `POSTGRES_PASSWORD`, `POSTGRES_APP_USER`, `POSTGRES_APP_PASSWORD`, `POSTGRES_APP_DB` and `POSTGRES_HOST_PORT` set in the environment, and asserts it loads without error (scenario "Settings ignore compose variables"). Verify it passes.
  - 2026-11-16: `test_settings_load_with_compose_variables_present` passed (placeholder values, not passwords).
- [x] 2.4 Append to `.env.example` a block made only of comment lines (design D10): each compose variable, its default, that both passwords are mandatory with no default, the no-percent-encoding rule (D-15), and the matching `DATABASE_URL` template with the default names and port 5433. No password value, no new uncommented key. Verify `test_env_example_lists_exactly_the_settings_keys` and `test_env_example_carries_no_real_values` pass unedited and `git diff -U0 .env.example | grep -E '^\+[^#+]'` prints nothing.
- [x] 2.5 Run run-verify; all five steps green.
  - 2026-11-16, local (Windows), against the `.env` database; `printenv DATABASE_URL` exited 1 first:
    - `uv run ruff check .`: All checks passed.
    - `uv run ruff format --check .`: 109 files already formatted.
    - `uv run mypy backend/app`: no issues found in 35 source files.
    - `uv run pytest -m unit`: 257 passed (246 + the 11 new ones), 46 deselected.
    - `uv run pytest -m integration`: 46 passed, 257 deselected.
  - 2.4 checks: `git diff -U0 .env.example | grep -E '^\+[^#+]'` printed nothing; `test_env_example_lists_exactly_the_settings_keys` and `test_env_example_carries_no_real_values` passed unedited.
  - Group 1 follow-up (1.5, read-only): after commit `49e019c`, `git ls-files --eol` on the init script shows `i/lf w/lf`.
- [ ] 2.6 Commit (Lautaro), two commits. (a) Paths: `tests/unit/test_dev_database_compose.py`. Message: `test(add-docker-compose-dev-db): guard compose image parity, loopback port, required passwords and project-scoped volume`. (b) Paths: `.env.example`, `openspec/changes/add-docker-compose-dev-db/tasks.md`. Message: `chore(add-docker-compose-dev-db): document compose variables in env template as comments only`.

## 3. Runtime verification in the isolated project (critical checkpoint)

Before starting this group, Lautaro reviews every command below and confirms
none can reach the development volume. Results are recorded under each task.
Throwaway passwords are generated as random hexadecimal (`openssl rand -hex 16`),
written only to env files inside `$VERIFY_DIR` and never printed or recorded.

- [ ] 3.1 Wrapper and baseline (scenario "Development volume untouched by verification"). (a) Create, outside the repository, `$VERIFY_DIR/verify.env` (both passwords, `POSTGRES_HOST_PORT=55433`, default names) and the env files used later: `no-bootstrap.env` and `no-app.env` (each lacking one password), `default-port.env` (no `POSTGRES_HOST_PORT`) and `foo.env` (`verify.env` plus `POSTGRES_APP_DB=foo`). (b) Create `$VERIFY_DIR/vc.sh` (design D12), with `set -euo pipefail` and no tracing or echo of values, that: fixes `docker compose -p bot-resto-verify -f <repo>/docker-compose.yml --env-file "$VERIFY_DIR/<env file>"`, with the project name hardcoded and not overridable; uses `verify.env` by default and accepts `--env <name>` only as a bare file name inside `$VERIFY_DIR` (rejects any `/`); unsets every `POSTGRES_*` variable inherited from the main shell, plus `COMPOSE_PROJECT_NAME`, `COMPOSE_FILE` and `COMPOSE_PROFILES`; builds and exports, only inside its own process, the verification `DATABASE_URL` (`postgresql+psycopg://<app user>:<app password>@127.0.0.1:<host port>/<app db>`) from the selected env file; and runs either `docker compose … "$@"` or, with `--run <command…>`, a host command (alembic, pytest) with that `DATABASE_URL`. Lautaro reviews `vc.sh` before it is used. (c) Record `docker volume ls`, and `docker volume inspect bot-resto-dev_pgdata --format '{{.CreatedAt}}'` if that volume exists (it may not exist yet); record `docker ps -a --filter label=com.docker.compose.project=bot-resto-verify` and `docker volume ls --filter label=com.docker.compose.project=bot-resto-verify` both empty; record `git status --porcelain`.
- [ ] 3.2 Missing passwords (scenarios "Missing bootstrap password", "Missing application password"): run `vc.sh --env no-bootstrap.env up -d` and `vc.sh --env no-app.env up -d`; record exit code 1, the variable named in each message, and that the two filtered listings of 3.1 are still empty.
- [ ] 3.3 Loopback and default port (scenario "Loopback binding with default port"): in a single command, `vc.sh --env default-port.env config --format json | python -c "<print only services.db.ports>"`, so the unfiltered output never reaches the terminal; record `host_ip` `127.0.0.1`, `published` `5433`, `target` `5432`. Nothing is started.
- [ ] 3.4 First start (scenarios "First start on an empty volume", "Not healthy during initialization", "Working tree after up", "Configurable host port"): run `vc.sh up -d --wait`; record exit code 0 and `healthy`; compare `vc.sh logs -t db` (line "PostgreSQL init process complete") with the first successful entry of `docker inspect --format '{{json .State.Health}}'` on the container ID from `vc.sh ps -q db`, and record that healthy comes after init finished; record `vc.sh port db 5432` = `127.0.0.1:55433`; record `git status --porcelain` identical to 3.1.
- [ ] 3.5 Role, ownership and privilege (scenarios "Role attributes", "Application role cannot create a database"): through `vc.sh exec -T db psql -U postgres -d postgres -Atc …` record `pg_roles` attributes of `bot_resto_user` and `datdba::regrole` of `bot_resto` and `bot_resto_test`; through `vc.sh exec -T db sh -c '…'` using the container's own `$POSTGRES_APP_PASSWORD` over `-h 127.0.0.1`, run `\set VERBOSITY verbose` and `CREATE DATABASE probe` and record SQLSTATE `42501` and that `probe` does not exist.
- [ ] 3.6 No schema, collation, restart policy (scenarios "Fresh application database has no tables", "Same collation as CI", "Restart policy", "Volume declaration inspected"): record zero rows from `information_schema.tables` outside `pg_catalog`/`information_schema` in `bot_resto`; `datcollate` `en_US.utf8` for both databases; `HostConfig.RestartPolicy.Name` `unless-stopped`; the data mount of the container is the volume `bot-resto-verify_pgdata`.
- [ ] 3.7 CI contract (scenario "Migrations and integration suite against the new database"): run `vc.sh --run sh -c 'cd backend && uv run alembic upgrade head'` and `vc.sh --run uv run pytest -m integration`; record both exit 0 (including `test_order_follows_the_database_collation`); then, through `vc.sh exec -T db psql -U postgres -d bot_resto …`, record `alembic_version.version_num` equal to `alembic heads` and `customers` and `menu_items` present, as the CI "Application database intact" step. Afterwards, `printenv DATABASE_URL` in the main shell exits 1.
- [ ] 3.8 Persistence and idempotency (scenarios "Row survives down and up", "Second up is a no-op"): insert a customer row as `postgres` into `bot_resto` through `vc.sh exec`; record `vc.sh ps -q db`; run `vc.sh up -d` and record exit 0 and same container ID; run `vc.sh down` (no `-v`) and `vc.sh up -d --wait`; record the row is still there and the logs of that start show "Skipping initialization" and no error.
- [ ] 3.9 Reset (scenario "Reset recreates an empty database"): run `vc.sh down -v`, record that only `bot-resto-verify_pgdata` disappeared from `docker volume ls`, then `vc.sh up -d --wait`; record both databases exist with no tables and the role attributes match 3.5.
- [ ] 3.10 Custom name (scenario "Custom database name"): `vc.sh down -v`; `vc.sh --env foo.env up -d --wait`; record that `foo` and `foo_test` exist owned by `bot_resto_user`.
- [ ] 3.11 Teardown and final check: `vc.sh --env foo.env down -v`; record `docker volume ls` and the `bot-resto-dev_pgdata` creation time (if it exists) identical to 3.1, and both filtered listings empty; delete `$VERIFY_DIR` (wrapper and env files included).
- [ ] 3.12 Secrets check (scenarios "Files inspected for secrets", ".env stays out of Git"): record that `git grep -n -i -E 'password' -- docker-compose.yml docker/ .env.example` shows only variable names and references, never a value; `git ls-files .env` prints nothing; `git check-ignore .env` exits 0.
- [ ] 3.13 Read-only security review with the `security` subagent of the compose file, init script, `.env.example` block and test file; record findings; any medium or high finding is fixed before closing (`SDD-4`).
- [ ] 3.14 Run run-verify with the `DATABASE_URL` from `.env` (first `printenv DATABASE_URL` exits 1 in the main shell); all five steps green.
- [ ] 3.15 Commit (Lautaro). Paths: `openspec/changes/add-docker-compose-dev-db/tasks.md` (plus any fix from 3.13, listed explicitly). Message: `chore(add-docker-compose-dev-db): record isolated runtime verification and security review results`.

## 4. Documentation and close (committed before the archive)

- [ ] 4.1 Add the README section "Base de desarrollo local" (scenarios "Password change requires reset", "Migration documented", "Contributor sets up the database from the README", "Compose configuration output is not shared"): requirements, `.env` variables and the `DATABASE_URL` that matches; start (`docker compose up -d --wait`), stop (`docker compose stop` / `down`), status; migrations and `pytest -m unit` / `pytest -m integration` against it; `down -v` marked as the only destructive command, with what it removes and the recommendation of `pg_dump` first; init runs only on an empty volume, so changing a password needs the reset; if the init fails, the volume stays initialized without role or databases, later starts skip the init and the healthcheck stays `unhealthy`, and the way out is `down -v` + `up -d --wait` after fixing the cause; no characters needing percent-encoding in passwords (D-15); `docker compose config` prints interpolated passwords and is never pasted anywhere (`SEC-2`); moving from `bot-resto-db` (stop it, its `botresto*` databases do not migrate, optional `pg_dump` and restore into `bot_resto` with `--no-owner`). Verify no password value in the section and every command matches the compose file.
- [ ] 4.2 Lautaro follows the README on the real development project (non-destructive: optional `pg_dump`, stop `bot-resto-db`, `docker compose up -d --wait`, `alembic upgrade head`, optional restore) and run-verify passes against it; record the result. `down -v` here only if the first `up` failed and no data has been restored or loaded yet.
- [ ] 4.3 Add the README log entry for `add-docker-compose-dev-db` in the format of the previous entries (`SDD-5`).
- [ ] 4.4 Update `ESTADO.md`: the line for this change in "Hecho"; the development database line of the snapshot (compose, names `bot_resto` / `bot_resto_test` / `bot_resto_user`, port 5433); D-33 kept open and relabelled "para un change de `menu-catalog`"; remove from "Próximo paso" the item this change resolves, and if there is no such item, leave "Próximo paso" untouched. Without touching other outdated entries.
- [ ] 4.5 Run run-verify with the `DATABASE_URL` from `.env` (first `printenv DATABASE_URL` exits 1 in the main shell); all five steps green.
- [ ] 4.6 Commit (Lautaro), before the archive. Paths: `README.md`, `ESTADO.md`, `openspec/changes/add-docker-compose-dev-db/tasks.md`. Message: `chore(add-docker-compose-dev-db): add development database README section, log entry and ESTADO update`.

## Workflow follow-up

- Archive the change with `/opsx:archive` once every task is done and verified (`SDD-4`); the archive creates the main spec `development-database`.
- Lautaro opens the PR to `develop`; the `verify` check must pass, including the new unit tests.
