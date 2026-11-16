# Change: Add Docker Compose development database

## Why

Hoy la base de desarrollo se arma a mano: Lautaro usa un contenedor propio
(`bot-resto-db`, puerto 5433, bases `botresto*`) y Gabriel un PostgreSQL
nativo. La gemela `<base>_test`, que exigen los tests destructivos de migración
desde `migrate-identifiers-to-bigint`, también se crea a mano. Eso ya dejó
tres esquemas de nombres distintos (local de Lautaro, README/ESTADO y CI),
cortó `pytest -m integration` dos veces durante `add-ci-pipeline` porque el
contenedor estaba apagado, y no hay forma de reproducir en local el entorno
exacto del CI. `VER-3` pide integración contra PostgreSQL real en contenedor, y
`add-menu-seed` y después `add-orders-core` necesitan una base reproducible.

El objetivo es uno solo: que `docker compose up -d --wait` deje lista una
PostgreSQL con el mismo contrato que el CI (misma imagen, rol de la app sin
privilegios, base de la app y su gemela `_test`, ambas de ese rol). Nada más.

## What Changes

- `docker-compose.yml` en la raíz, con un único servicio de PostgreSQL:
  imagen `postgres:16.15` (tag exacto, Debian, igual que el CI), nombre de
  proyecto explícito, datos en un volumen nombrado (nunca bind mount, por
  OneDrive), `restart: unless-stopped`, puerto publicado solo en `127.0.0.1`
  con puerto de host configurable (default `5433`) y healthcheck por TCP.
- Un script de init (`.sh`) que corre una sola vez, con volumen vacío, con el
  superusuario de bootstrap, y crea solo el rol de la app (sin privilegios) y
  las dos bases con ese rol como dueño. Ni tablas, ni esquemas, ni extensiones:
  el esquema sale solo de Alembic (`DAT-6`).
- `.gitattributes` con `*.sh text eol=lf`, para que el script no llegue al
  contenedor con `\r` desde un checkout de Windows.
- `.env.example`: un bloque **comentado** que documenta las variables del
  compose, sin sumar ninguna clave no comentada (las no comentadas siguen
  siendo exactamente los campos de `Settings`).
- `README`: sección "Base de desarrollo local" (levantar, apagar, resetear,
  migrar y testear contra ella, qué hace `down -v`, cuándo corre el init, cómo
  pasar desde `bot-resto-db`, restricción de caracteres de la clave por D-15,
  y que la salida de `docker compose config` no se comparte por `SEC-2`).
- Tests unitarios estáticos (sin red ni base) que protegen el contrato del
  compose: imagen igual a la del CI, puerto en loopback, claves sin valor
  literal y obligatorias, volumen nombrado y aislado por proyecto, y que
  `Settings` carga con variables `POSTGRES_*` presentes en el entorno.

## Capabilities

### New Capabilities

- `development-database`: la base PostgreSQL local de desarrollo que se
  levanta con un comando — qué crea, con qué privilegios, con qué paridad
  respecto del CI, cómo persiste, cómo se resetea, qué secretos exige y cómo
  se aísla de cualquier verificación destructiva.

### Modified Capabilities

Ninguna. `continuous-integration` no cambia: el CI mantiene su service
container. `development-toolchain` define qué se verifica y tampoco cambia.

## Impact

- Archivos nuevos: `docker-compose.yml`, el script de init (en
  `docker/postgres/`), `.gitattributes`, un test unitario nuevo en
  `tests/unit/`.
- Archivos modificados: `.env.example` (solo comentarios), `README.md`,
  `ESTADO.md` (al cierre).
- Sin cambios en `backend/`, `config/`, `pyproject.toml`, `uv.lock`,
  `.github/workflows/ci.yml` ni en tests existentes. Si algo obliga a tocarlos,
  el apply frena (`SDD-2`).
- Desarrollo local: el contenedor manual `bot-resto-db` deja de ser la base
  recomendada; su contenido no migra solo (el README explica cómo).
- Riesgo operativo propio de este change: `down -v` borra el volumen de
  desarrollo. Toda verificación destructiva corre en un proyecto de compose
  aparte (ver spec y tasks).
- Reglas: `GEN-3`, `GEN-5`, `SEC-1`, `SEC-2`, `SEC-5`, `DAT-6`, `VER-1`,
  `VER-3`, `VER-4`, `VER-6`, `SDD-1` a `SDD-5`.

## Non-goals

- `Dockerfile` de la app y compose de producción (change aparte).
- Cualquier cambio a `ci.yml` o a la spec `continuous-integration`.
- Correr Alembic automáticamente al hacer `up`: las migraciones siguen siendo
  un paso explícito (`VER-4`).
- Seed del menú (es `add-menu-seed`).
- Cambiar la collation o el orden del menú: D-33 queda abierta.
- Corregir `backend/alembic/env.py` (bug de logging y D-15): change aparte,
  avisando a Gabriel.
- Tocar `backend/`, `config/`, `pyproject.toml`, `uv.lock` o tests existentes.
- Adminer/pgAdmin, backups, Docker secrets, otras versiones de PostgreSQL.
