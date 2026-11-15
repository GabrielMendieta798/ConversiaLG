# Design: CI pipeline on Pull Requests

## Context

Motivación en `proposal.md`. Requisitos en
`specs/continuous-integration/spec.md`. Estado del repo que condiciona el
diseño (verificado el 11/11/2026):

- No existe `.github/`. Ningún archivo versionado tiene hoy marcadores de
  conflicto al inicio de línea (`git grep` vacío), así que la compuerta nueva
  no rompe nada existente.
- `app.main` arma `create_app()` al importarse: pytest, incluso solo
  `-m unit`, necesita `APP_ENV` y `DATABASE_URL` en el entorno. Sin `.env` en
  el runner, pydantic-settings lee solo el entorno.
- `tests/integration/conftest.py` corta la sesión con `pytest.exit(returncode=1)`
  si la base no responde (`VER-6`), y `migration_database_url` deriva
  `<base>_test`, se niega a resetear cualquier otra y exige que exista con el
  rol de `DATABASE_URL` como dueño (hace `DROP SCHEMA public CASCADE` +
  `CREATE SCHEMA public AUTHORIZATION CURRENT_USER`).
- Los tests normales de integración asumen las migraciones aplicadas sobre la
  base de la app (consultan `customers` y `menu_items`).
- `backend/alembic/env.py` importa `settings` de la app: `alembic upgrade head`
  necesita el mismo entorno y se corre desde `backend/` (`prepend_sys_path = .`).
  Historia lineal, un único head (`c3a7b9d2e641`).
- uv 0.11: `uv sync --frozen` instala desde el lock **sin** comprobar que esté
  en sync con `pyproject.toml`. Por sí solo no cumple el escenario de drift.
- La base local de desarrollo se llama `bot_resto`, su gemela `bot_resto_test`,
  ambas de `bot_resto_user` (rol sin superusuario).

## Goals / Non-Goals

**Goals:**

- Un único job lineal cuyo orden de pasos sea legible en la UI de GitHub y
  coincida 1:1 con `run-verify`.
- Que el CI ejerza los mismos permisos de base que el entorno local, para que
  un test no pase en CI solo por correr como superusuario.
- Reproducibilidad total: nada sobrevive entre corridas salvo la cache de
  descargas de uv.

**Non-Goals (de diseño):**

- Matriz de versiones de Python o de PostgreSQL.
- Paralelizar pasos o separar jobs (unit e integration en jobs distintos).
- Acciones de terceros más allá de las oficiales de GitHub y la de uv.

## Decisions

### D1 — Un solo job, pasos secuenciales

Orden: checkout → marcadores de conflicto → setup uv/Python → `uv lock --check`
→ `uv sync --frozen` → **ruff check → ruff format --check → mypy → pytest -m
unit** → crear rol y bases → `alembic upgrade head` → **pytest -m integration**
→ chequeo de base de la app intacta.

GitHub Actions corta el job en el primer paso con exit code ≠ 0 y marca los
siguientes como salteados: eso da "corta en el primer rojo" sin lógica propia.
Ningún paso usa `continue-on-error` ni `if: always()`.

La preparación de la base va **después** de `pytest -m unit` y no al principio:
así un error de lint no espera a la base, y el orden de los cinco pasos
visibles es exactamente el de `run-verify`.

*Alternativa descartada:* jobs separados para unit e integration con `needs:`.
Más rápido en paralelo, pero obliga a repetir checkout e instalación y el
reporte deja de leerse como `run-verify`.

### D2 — Lockfile: `uv lock --check` + `uv sync --frozen`

Se mantiene `uv sync --frozen` (pedido explícito) y se agrega antes
`uv lock --check`, que falla si `uv.lock` no corresponde a `pyproject.toml`
(escenario de drift). Los pasos siguientes usan `uv run --frozen` para que
`uv run` no intente re-lockear ni re-sincronizar.

*Alternativa:* `uv sync --locked` cubre ambas cosas en un comando. Se prefirió
el paso aparte porque el fallo por drift queda con nombre propio en el reporte.
No se usa `UV_FROZEN` a nivel job: para `uv lock` esa variable equivale a
`--check-exists` y anularía el chequeo.

### D3 — Versiones fijas (`SEC-5`)

- Acciones (`actions/checkout`, `astral-sh/setup-uv`) fijadas por SHA de
  commit completo, con el tag como comentario. Los SHA se leen del release
  oficial en el apply, no se escriben de memoria.
- Versión de uv fija (la misma minor que usa el equipo, hoy 0.11.x), con
  `enable-cache: true` y `cache-dependency-glob: uv.lock`.
- Python `3.12` vía `uv python install` / `python-version` de setup-uv; no
  depende del Python del runner.
- Imagen `postgres:16.<minor>` con tag exacto (se elige en el apply).
- Runner `ubuntu-24.04`, no `ubuntu-latest`.

### D4 — Base: service container + rol no superusuario

Service `postgres:16.x` con healthcheck `pg_isready`, puerto 5432 mapeado al
runner. El contenedor arranca con un superusuario de bootstrap
(`POSTGRES_USER=postgres`, clave de prueba). Un paso con `psql` (cliente
incluido en la imagen del runner) crea:

```sql
CREATE ROLE bot_resto_ci LOGIN PASSWORD '<clave de prueba>';
CREATE DATABASE bot_resto_ci OWNER bot_resto_ci;
CREATE DATABASE bot_resto_ci_test OWNER bot_resto_ci;
```

`DATABASE_URL` del job apunta a `bot_resto_ci` con el rol `bot_resto_ci`, sin
privilegios de superusuario ni `CREATEDB`, igual que `bot_resto_user` en local
(decisión del 11/10). En PostgreSQL 15+ el schema `public` pertenece a
`pg_database_owner`, así que el dueño de la base puede hacer el `DROP SCHEMA`
del fixture.

*Alternativa descartada:* que la app use el superusuario del contenedor. Un
test que dependa sin querer de privilegios extra pasaría en CI y fallaría en
local o en producción.

Antes del `psql` hay un loop corto de `pg_isready` con tope (el healthcheck
del service ya lo garantiza; el loop es cinturón). Si la base no responde, el
paso falla y el job corta: nunca se llega a pytest con la base caída.

### D5 — Migraciones sobre la base de la app

`cd backend && uv run --frozen alembic upgrade head` con el `env` del job.
Esto además valida en cada PR que la cadena de migraciones aplica desde cero
sobre PostgreSQL 16 (refuerza `VER-4`; el downgrade lo prueban los tests
destructivos de integración).

### D6 — Chequeo de base intacta (escenario propio)

Después de `pytest -m integration`, un paso con `psql` como `bot_resto_ci`
contra `bot_resto_ci` verifica que `alembic_version.version_num` es el head
(leído con `alembic heads`, no escrito a mano) y que existen las tablas
`customers` y `menu_items`. Si un test destructivo hubiera apuntado a la base
de la app, `DROP SCHEMA public CASCADE` se habría llevado ambas cosas y el
paso falla. Complementa la guarda que ya tiene el fixture
(`current_database()` debe terminar en `_test`).

### D7 — Marcadores de conflicto

`git grep -nE '^(<{7}|>{7})( |$)' -- .` sobre los archivos versionados del
checkout: solo los marcadores de apertura y de cierre. Un conflicto real
siempre deja los dos, así que no hace falta buscar el separador del medio, y
no buscarlo elimina cualquier falso positivo con subrayados de Markdown. Si
encuentra algo, imprime `archivo:línea:` y sale con 1; si no, sale con 0 (se
invierte el código de `git grep`, que devuelve 1 sin coincidencias). Corre
antes de instalar nada.

El chequeo no puede fallar sobre sí mismo: ni la spec, ni este design, ni el
README, ni `ci.yml` contienen los marcadores literales al inicio de línea.
Donde hay que nombrarlos se escriben con cuantificador (`<{7}`, `>{7}`), nunca
como siete caracteres seguidos.

### D8 — Entorno, secretos y permisos

- `env` a nivel job: `APP_ENV=test`, `DATABASE_URL=postgresql+psycopg://bot_resto_ci:<clave de prueba>@localhost:5432/bot_resto_ci`,
  `PGPASSWORD` del bootstrap solo en el paso que lo usa. La clave de prueba no
  contiene `%` (evita D-15) y no tiene uso fuera del contenedor descartable.
- Sin `secrets.*`, sin `env`/`printenv`/`set -x`, sin `ACTIONS_STEP_DEBUG`.
- `permissions: contents: read` a nivel workflow.
- `concurrency: group: ci-${{ github.event.pull_request.number }}`,
  `cancel-in-progress: true`.
- `on: pull_request: branches: [develop, main]` (tipos por defecto: opened,
  synchronize, reopened). La corrida usa el merge ref del PR, o sea lo que
  quedaría en la rama base.
- `timeout-minutes: 20` en el job, para que un test colgado (ver D-26) no
  consuma la cuota de Actions.

### D9 — Cómo se verifica el propio CI

El workflow no tiene tests automáticos propios. Los escenarios se prueban con
un PR de prueba en borrador hacia `develop`, desde una rama descartable,
empujando un commit roto por vez (lint, formato, mypy, test unitario, test de
integración, marcador de conflicto, drift del lock, base inalcanzable vía
`DATABASE_URL` con puerto equivocado en el workflow) y revirtiéndolo. Esos
commits no se mergean nunca y la rama se borra al terminar. Re-run del mismo
commit para el escenario de reproducibilidad. Los commits y pushes los hace
Lautaro.

## Risks / Trade-offs

- [En local se usa Python 3.13 (hay cache de mypy 3.13) y el CI usa 3.12] →
  CI prueba el piso de `requires-python`. Si diverge (p. ej. mypy distinto
  por versión), gana el CI y se anota.
- [Tests de integración con timing (concurrencia) pueden ser más lentos en el
  runner] → `timeout-minutes`; si aparece un flaky, se discute (`VER-6`), no
  se reintenta automáticamente.
- [El check no es obligatorio hasta configurar branch protection] → paso
  manual documentado; tarea explícita para Lautaro/Gabriel.
- [Fijar acciones por SHA hace que las actualizaciones sean manuales] → es el
  mismo trato que las dependencias (`SEC-5`): se actualizan a propósito.

## Migration Plan

1. Merge del PR que agrega el workflow (el propio PR ya corre el check).
2. A mano en GitHub, en `develop` y `main`: exigir el check en verde y una
   revisión aprobada.
3. Rollback: borrar `.github/workflows/ci.yml` y quitar el check requerido.

## Open Questions

- Nombre visible del check en GitHub (p. ej. `ci / verify`): se fija en el
  apply y se usa tal cual en la branch protection.
