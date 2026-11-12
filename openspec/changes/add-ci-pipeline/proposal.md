# Change: Add CI pipeline on Pull Requests

## Why

Dos personas y dos agentes distintos (Claude Code y Codex) escriben sobre el
mismo repo, y hoy lo único que frena código roto es que cada uno corra
`run-verify` a mano y lo informe. Eso ya falló: hubo que arreglar marcadores de
conflicto mergeados (`b66521a fix(bigint): resolve merge conflict markers`) y
un change quedó mergeado sin archivar. Antes de que exista un webhook público y
plata de por medio, la verificación de `VER-1` tiene que correr sola, siempre y
de la misma forma para todos, sobre cada PR hacia `develop` y `main`.

## What Changes

- Un workflow de GitHub Actions en `.github/workflows/` que corre en
  `pull_request` hacia `develop` y hacia `main`.
- Una compuerta previa que falla si algún archivo versionado tiene marcadores
  de conflicto al inicio de línea (`<<<<<<<`, `=======`, `>>>>>>>`).
- Instalación reproducible: Python 3.12, uv con cache, chequeo explícito de que
  `uv.lock` está en sync con `pyproject.toml` y `uv sync --frozen`.
- Los cinco pasos de `run-verify`, en su orden y cortando en el primer rojo:
  `ruff check`, `ruff format --check`, `mypy backend/app`, `pytest -m unit`,
  `pytest -m integration`.
- PostgreSQL 16 como service container con healthcheck; antes de los tests se
  crean la base de la app y su gemela `<nombre>_test` con el mismo dueño, y se
  aplica `alembic upgrade head` sobre la primera.
- Un chequeo posterior a integración que confirma que la base de la app quedó
  intacta (los tests destructivos tocan solo la `_test`).
- Credenciales de prueba como `env` del job; ningún `secret` de GitHub, ningún
  volcado de variables de entorno. Token con `contents: read` y concurrencia
  que cancela corridas viejas del mismo PR.
- Sección nueva en el `README`: qué corre el CI, cómo reproducirlo en local y
  la protección de ramas como paso manual en GitHub.

## Capabilities

### New Capabilities

- `continuous-integration`: la compuerta automática que todo PR hacia
  `develop` o `main` tiene que pasar — qué verifica, en qué orden, contra qué
  base, con qué permisos y garantías de aislamiento y reproducibilidad.

### Modified Capabilities

Ninguna. `development-toolchain` define *qué* se verifica (lockfile en sync,
lint, typecheck estricto, suites separadas) y no cambia; este change agrega
*dónde y cuándo* corre esa verificación.

## Impact

- Archivos nuevos: `.github/workflows/ci.yml`. Archivo modificado: `README.md`.
- Sin cambios en `backend/`, `tests/`, `config/`, `pyproject.toml` ni
  `uv.lock`. Si el CI no puede andar sin tocarlos, el apply frena (`SDD-2`).
- GitHub: aparece un check obligatorio de facto en cada PR. Hacerlo
  *requerido* (branch protection) es un paso manual de un humano.
- Reglas: `GEN-3`, `SEC-1`, `SEC-2`, `SEC-5`, `VER-1`, `VER-3`, `VER-4`,
  `VER-6`, `SDD-1`, `SDD-3`.

## Non-goals

- Deploy, publicación de imágenes, releases, y cualquier cosa que escriba en
  `develop` o en `main`.
- Cobertura mínima, badges, linters nuevos o cualquier herramienta que el
  proyecto no use hoy.
- Cambios en el código de la app o en los tests.
- `Dockerfile` y `docker-compose` (otro change).
- Branch protection como código: se configura a mano y queda documentada.
