# Tasks: Add CI pipeline on Pull Requests

No implementation task starts until this proposal, design and spec are
explicitly approved (`SDD-1`). Nothing under `backend/`, `tests/`, `config/`,
`pyproject.toml` or `uv.lock` changes in this change; if the CI cannot work
without touching them, stop and propose a new scenario (`SDD-2`).

## 1. Workflow skeleton

- [x] 1.1 Create `.github/workflows/ci.yml` triggered by `pull_request` on `develop` and `main`, runner `ubuntu-24.04`, `permissions: contents: read`, per-PR `concurrency` with `cancel-in-progress: true`, and `timeout-minutes` on the job.
- [ ] 1.2 Look up the commit SHA of the current releases of `actions/checkout` and `astral-sh/setup-uv` from their official repositories (GitHub API and `git ls-remote`). Pin `actions/checkout` by full SHA with the tag as a comment (v7.0.1 → `3d3c42e5aac5ba805825da76410c181273ba90b1`). `astral-sh/setup-uv` is not used until 2.1: its SHA is recorded here (v10.3.3 → `68559e99cc807c1eb7e9238fd43a15bba460eec2`, verified 2026-11-11) and it is pinned in 2.1.
- [x] 1.3 Add the conflict-marker step right after checkout (D7), matching only the opening and closing markers with the quantified pattern `^(<{7}|>{7})( |$)`; confirm locally that `git grep` finds no marker in the current tree, including `ci.yml` and the change's own files.

## 2. Toolchain installation

- [ ] 2.1 Set up uv with a pinned version, cache enabled and keyed by `uv.lock`, and Python 3.12. Re-verify the `astral-sh/setup-uv` SHA recorded in 1.2 against the official repository before using it, and pin it by full SHA with the tag as a comment.
- [ ] 2.2 Add `uv lock --check` followed by `uv sync --frozen` (D2).

## 3. Verification steps

- [ ] 3.1 Declare the job `env` with `APP_ENV=test` and the test `DATABASE_URL` (D8); confirm no `secrets.` reference and no environment dump.
- [ ] 3.2 Add, in order and each as its own named step, `uv run --frozen ruff check`, `ruff format --check`, `mypy backend/app` and `pytest -m unit`.
- [ ] 3.3 Run those four commands locally with the same `APP_ENV`/`DATABASE_URL` and no `.env` interference, and confirm they pass.

## 4. Database and integration

- [ ] 4.1 Add the `postgres:16.<minor>` service with a `pg_isready` healthcheck and test-only bootstrap credentials, with an exact image tag.
- [ ] 4.2 Add the step that waits for the database and creates the non-superuser role and both databases owned by it (D4).
- [ ] 4.3 Add `alembic upgrade head` on the application database from `backend/` (D5).
- [ ] 4.4 Add `uv run --frozen pytest -m integration`.
- [ ] 4.5 Add the post-integration check that the application database is still at the Alembic head with `customers` and `menu_items` present (D6).

## 5. Documentation

- [ ] 5.1 Add a CI section to `README.md`: what runs and in which order, how to reproduce it locally (both databases, migration, env vars, commands), and branch protection as a manual GitHub step. Name the conflict markers only with the quantified pattern, never literally at the start of a line.

## 6. Verification of the CI itself (D9)

- [ ] 6.1 Run the complete `run-verify` locally and record the result.
- [ ] 6.2 Lautaro opens the change PR to `develop`; record that the check passes and shows the five verification steps (scenario "Everything green").
- [ ] 6.3 On a throwaway draft PR, push one breaking commit at a time and record the failing step and the skipped later steps for: lint, format, mypy, unit test, integration test, conflict marker, lockfile drift, unreachable database. Revert each; never merge; delete the branch afterwards.
- [ ] 6.4 Re-run the check on the same green commit and record an identical result (scenario "Same commit twice").
- [ ] 6.5 Push two commits in quick succession to a PR and record that the first run was cancelled.
- [ ] 6.6 Record in the integration log that the migration tests ran against `bot_resto_ci_test` and that the post-integration check passed.

## 7. Close

- [ ] 7.1 Lautaro/Gabriel configure branch protection on `develop` and `main` requiring the check and one approved review; record it was done.
- [ ] 7.2 Add to `ESTADO.md` only the line for this change, without touching other outdated entries, and add the README log entry (`SDD-5`).
