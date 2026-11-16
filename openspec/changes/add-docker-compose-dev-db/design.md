# Design: Docker Compose development database

## Context

Motivación en `proposal.md`. Requisitos en
`specs/development-database/spec.md`. Estado del repo que condiciona el diseño
(verificado sobre `develop` en `2f21f90`, 14/11/2026):

- No existen `docker-compose.yml`, `Dockerfile` ni `.gitattributes`.
- `Settings` (`backend/app/core/config.py`) usa `extra="ignore"` y lee `.env`:
  una clave `POSTGRES_*` en el `.env` no rompe la app. `DATABASE_URL` es una
  cadena completa; la app no la arma a partir de partes.
- `tests/unit/test_config.py` exige que las claves **no comentadas** de
  `.env.example` sean exactamente los campos de `Settings` y que ningún valor
  tenga `@` ni `://`. Su parser ignora las líneas que empiezan con `#`.
- `tests/integration/conftest.py` deriva `<base>_test` de `DATABASE_URL`, se
  niega si la base ya termina en `_test`, y resetea la gemela con
  `DROP SCHEMA public CASCADE` / `CREATE SCHEMA public AUTHORIZATION
  CURRENT_USER` conectado como el rol de la app. Ese rol tiene que ser dueño
  de la gemela; no tiene `CREATEDB`, así que no puede crearla él.
- El CI usa `postgres:16.15`, un superusuario de bootstrap `postgres`, el rol
  `bot_resto_ci` `NOSUPERUSER NOCREATEDB NOCREATEROLE` y las bases
  `bot_resto_ci` y `bot_resto_ci_test`. No se toca.
- `test_order_follows_the_database_collation` exige `datcollate` con prefijo
  `en_US` y el orden "Ñoquis, Nuez, Orecchiette, Ravioles".
- Lautaro tiene un contenedor manual `bot-resto-db` en `5433` con bases
  `botresto*` y un menú cargado a mano, sin seed. Gabriel usa PostgreSQL
  nativo (típicamente en `5432`).
- Funcionamiento de la imagen oficial que importa acá: con el directorio de
  datos vacío, el entrypoint corre `initdb`, levanta un **servidor temporal
  que escucha solo en el socket Unix** (`listen_addresses=''`), ejecuta los
  archivos de `/docker-entrypoint-initdb.d/`, apaga ese servidor y recién ahí
  arranca el definitivo con TCP. Con datos presentes, saltea todo eso. Un
  `.sh` con bit de ejecución se ejecuta; sin él, se hace `source`.

## Goals / Non-Goals

**Goals:**

- Un solo comando (`docker compose up -d --wait`) que devuelve el control
  cuando la base está realmente usable por el rol de la app.
- Paridad de contrato con el CI: imagen, locale, privilegios del rol y par de
  bases; solo cambian los nombres (configurables) y el puerto.
- Que el error peligroso de este change (un `down -v` sobre la base de
  desarrollo) no pueda ocurrir durante la verificación.

**Non-Goals (de diseño):**

- Unificar el CI con el compose (el CI sigue con su service container).
- Derivar `DATABASE_URL` de las variables del compose: obligaría a tocar
  `backend/`.
- Hacer configurable el superusuario de bootstrap o su base.

## Decisions

### D1 — Archivos y dónde viven

- `docker-compose.yml` en la raíz (lo encuentra `docker compose` sin `-f`).
- `docker/postgres/init/10-create-app-role-and-databases.sh`: único script de
  init, montado **como archivo, de solo lectura**, en
  `/docker-entrypoint-initdb.d/`.
- `.gitattributes` en la raíz con `*.sh text eol=lf`.

El montaje del script es un bind mount, pero de un archivo versionado y de
solo lectura: el contenedor no escribe nada en el repo. Lo que no puede ser
bind mount son los **datos** (D6).

*Alternativas descartadas:* un `Dockerfile` que copie el script (es el
non-goal del Dockerfile y agrega un build); SQL inline en el compose (no puede
leer la clave del rol sin interpolarla en el YAML, que es justo lo que
queremos evitar).

### D2 — Variables, defaults y obligatoriedad

| Variable | Uso | Default |
|---|---|---|
| `POSTGRES_PASSWORD` | clave del superusuario de bootstrap (variable propia de la imagen) | ninguno, `:?` |
| `POSTGRES_APP_USER` | rol de la app | `bot_resto_user` |
| `POSTGRES_APP_PASSWORD` | clave del rol de la app | ninguno, `:?` |
| `POSTGRES_APP_DB` | base de la app; la gemela es `<valor>_test` | `bot_resto` |
| `POSTGRES_HOST_PORT` | puerto de host publicado en `127.0.0.1` | `5433` |

- Las dos claves usan `${VAR:?mensaje}`: Compose falla al interpolar, antes de
  crear nada, y el mensaje nombra la variable. `:?` también cubre la variable
  definida pero vacía. Ninguna clave tiene default en ningún archivo
  (`GEN-3`).
- Los no secretos usan `${VAR:-default}`, y coinciden con los nombres que ya
  documentan README y ESTADO (`bot_resto`, `bot_resto_user`): el `.env` de
  Lautaro y Gabriel converge a un único esquema.
- `POSTGRES_HOST_PORT` solo se usa en `ports:`; no entra al contenedor.
- El superusuario de bootstrap queda fijo en `postgres` (no se setea
  `POSTGRES_USER` ni `POSTGRES_DB`): nadie necesita cambiarlo y es una perilla
  menos.
- Prefijo `POSTGRES_` para agrupar todo lo del compose y distinguirlo de las
  claves de la app. Riesgo: que una versión futura de la imagen interprete
  alguna `POSTGRES_APP_*`; mitigado porque el tag está fijo (`SEC-5`) y
  cualquier cambio de tag es un cambio a propósito.

*Alternativa:* prefijo propio (`DEV_DB_*`). Evita cualquier choque con la
imagen, pero obliga a mapear `POSTGRES_PASSWORD` igual y parte las variables
en dos familias. No compensa.

**La clave del rol aparece dos veces en el `.env`:** en `POSTGRES_APP_PASSWORD`
y dentro de `DATABASE_URL`. Es duplicación consciente: la app lee una URL
entera y no se toca `backend/`. El README lo explica y muestra la plantilla de
`DATABASE_URL` con el puerto y los nombres por default.

### D3 — Por qué el rol de la app NO es `POSTGRES_USER`

`POSTGRES_USER` es el usuario que la imagen crea con `initdb`, y siempre es
**superusuario**. Si fuera `bot_resto_user`, los tests de integración
correrían como superusuario y dejarían de ejercitar el mínimo privilegio
decidido el 11/10 y que el CI ya prueba con `bot_resto_ci`: un test que dependa
sin querer de `CREATEDB` o de saltear permisos pasaría en local y fallaría en
CI o en producción. Por eso hay dos roles: `postgres` (bootstrap, solo lo usa
el init) y el rol de la app, creado por el script sin privilegios.

### D4 — Script de init

Corre como `postgres` contra la base `postgres`, solo con volumen vacío (lo
garantiza el entrypoint de la imagen). Hace exactamente:

```sql
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
CREATE DATABASE :"app_db" OWNER :"app_user";
CREATE DATABASE :"app_test_db" OWNER :"app_user";
```

- Los valores entran como **variables de psql** (`-v app_user=...`), y
  `:"x"` / `:'x'` las citan como identificador y como literal. No se arma SQL
  concatenando strings en bash (`SEC-4`), y la clave no aparece en la línea de
  comando de ningún proceso ni en el SQL que se loguea.
- `app_test_db` se deriva en el script como `${POSTGRES_APP_DB}_test`: es la
  misma regla que aplica el conftest.
- Antes de crear nada, el script valida que el rol y la base sean
  identificadores simples (`^[a-z_][a-z0-9_]*$`, y la base con largo que deje
  lugar a `_test` dentro de los 63 caracteres de PostgreSQL). Un nombre raro
  falla el init con un mensaje claro en vez de crear objetos con comillas.
- `psql -v ON_ERROR_STOP=1`, sin `-a`/`-e`, sin `set -x`, sin imprimir
  variables: la salida es solo `CREATE ROLE` / `CREATE DATABASE` (`SEC-2`).
- Las bases heredan de `template1` el locale de la imagen (`en_US.utf8`, D8).
  En PostgreSQL 15+ el esquema `public` es de `pg_database_owner`, o sea del
  rol de la app en cada base, que es lo que necesita el `DROP SCHEMA` del
  conftest.
- Debe funcionar tanto ejecutado como con `source` (en Windows el bit de
  ejecución del bind mount no está garantizado): no usa `exit` a nivel tope ni
  cambia opciones del shell que hereda el entrypoint; ante error, falla con
  código distinto de 0 y el entrypoint aborta el arranque.
- Nada de tablas, esquemas, extensiones ni grants extra (`DAT-6`).

### D5 — Healthcheck por TCP, como el rol de la app, contra la gemela

```yaml
healthcheck:
  test: ["CMD-SHELL", "PGPASSWORD=\"$$POSTGRES_APP_PASSWORD\" psql -h 127.0.0.1 -U \"$$POSTGRES_APP_USER\" -d \"$${POSTGRES_APP_DB}_test\" -tAc 'SELECT 1' >/dev/null"]
```

(forma ilustrativa; intervalos y reintentos se fijan en el apply)

- **TCP (`-h 127.0.0.1`)**: el servidor temporal del init escucha solo en el
  socket. Un `pg_isready` por socket da "listo" mientras el script todavía
  corre, y `--wait` devolvería el control con la base a medio crear.
- **Como el rol de la app, con clave**: el `pg_hba.conf` de la imagen pide
  `scram-sha-256` por TCP, así que el chequeo prueba además que el rol existe
  y su clave es la del `.env`.
- **Contra `<base>_test`**: es lo último que crea el init; si existe, el init
  terminó.
- **`$$`**: Compose no interpola esas variables; las resuelve el shell del
  contenedor en cada chequeo. La clave no queda escrita en la definición del
  healthcheck.
- Con reintentos acotados, un init roto termina en `unhealthy` y
  `up --wait` sale con error en vez de colgarse.

*Alternativas descartadas:* `pg_isready` por socket (falso positivo durante el
init); `pg_isready -h 127.0.0.1` (usa TCP, pero no autentica ni prueba que
existan el rol y las bases).

### D6 — Proyecto, servicio, contenedor y volumen

- `name: bot-resto-dev` en el compose; servicio `db`.
- **Sin `container_name`**: Compose lo deriva (`bot-resto-dev-db-1`). Así no
  choca con `bot-resto-db` y, sobre todo, permite levantar el proyecto de
  verificación (`-p bot-resto-verify`) al lado sin conflicto de nombres.
- Datos en el volumen nombrado `pgdata`, montado en
  `/var/lib/postgresql/data`, declarado a nivel tope **sin `name:` y sin
  `external:`**. Compose lo prefija con el proyecto: `bot-resto-dev_pgdata` en
  desarrollo, `bot-resto-verify_pgdata` en verificación. Un `name:` fijo haría
  que ambos proyectos compartan el mismo volumen y que un `down -v` de
  verificación borre el de desarrollo: por eso un test unitario lo prohíbe.
- Nunca bind mount para los datos: OneDrive sincroniza el árbol del repo y
  PostgreSQL sobre un directorio sincronizado (y sobre el filesystem de
  Windows montado en la VM de Docker) tiene permisos y fsync poco confiables.

### D7 — Puerto

`"127.0.0.1:${POSTGRES_HOST_PORT:-5433}:5432"`.

- Solo loopback: la base no queda expuesta en la red local.
- `5433` por default: evita el `5432` del PostgreSQL nativo de Gabriel y
  conserva el puerto que ya tiene el `DATABASE_URL` de Lautaro.
- Costo: choca con `bot-resto-db` si sigue corriendo. El `up` falla con un
  error visible de puerto ocupado (no hay corrupción posible), y el README
  dice que primero se para el contenedor viejo.

### D8 — Collation: no se cambia (D-33 sigue abierta)

Decisión ya tomada por Lautaro. Misma imagen y mismo locale que el CI
(`en_US.utf8`): `test_order_follows_the_database_collation` pasa sin tocarse.
D-33 queda abierta y al cierre se reetiqueta en `ESTADO.md` como "para un
change de `menu-catalog`".

- *B, `es-AR-x-icu` a nivel base:* rompería la paridad con el CI y el test
  existente; es una decisión de producto del menú, no de infraestructura.
- *C, `ORDER BY ... COLLATE`:* es un escenario nuevo de `menu-catalog` que toca
  `backend/`, fuera de alcance.

La rama `fix/menu-collation-test` de Gabriel (`386f765`) no está integrada:
este change no depende de ella ni la toca.

### D9 — Reinicio

`restart: unless-stopped`: vuelve sola después de reiniciar Docker Desktop o
la máquina, que es lo que cortó `pytest -m integration` dos veces en
`add-ci-pipeline`. Si alguien la para a propósito (`docker compose stop`),
queda parada.

### D10 — `.env.example`

Un bloque nuevo al final, **todo en líneas comentadas**: qué es cada
variable, su default, que las dos claves son obligatorias, que no pueden
llevar caracteres que en una URL se codifican con `%` (D-15:
`backend/alembic/env.py` rompe y filtra la URL), y la plantilla de
`DATABASE_URL` que corresponde. Sin valores de ejemplo para las claves. No
cambia ninguna línea no comentada, así que los dos tests existentes siguen
pasando sin editarse.

### D11 — Tests unitarios estáticos

Archivo nuevo `tests/unit/test_dev_database_compose.py` (marker `unit` por
carpeta). Leen `docker-compose.yml` y `.github/workflows/ci.yml` con
`yaml.safe_load` (PyYAML ya es dependencia); sin Docker, sin red, sin base
(`VER-3`). Protegen:

- imagen del servicio == `jobs.verify.services.postgres.image` del CI, y tag
  exacto (no `latest`, no `alpine`);
- cada puerto publicado empieza con `127.0.0.1:` y apunta a `5432`;
- `POSTGRES_PASSWORD` y `POSTGRES_APP_PASSWORD` son exactamente referencias
  `${VAR:?...}`: sin literal y sin default;
- el directorio de datos usa un volumen nombrado declarado a nivel tope, sin
  `name` ni `external`, y ningún montaje apunta a una ruta del repo salvo el
  script de init de solo lectura;
- sin `container_name: bot-resto-db`; `restart: unless-stopped`;
- el healthcheck usa `127.0.0.1` y la base `_test`;
- `Settings` carga sin error con `config_env` más todas las `POSTGRES_*`
  seteadas.

Estos tests no prueban el comportamiento en runtime: eso lo cubre la
verificación manual de los tasks (D12). Protegen contra regresiones del
archivo, que es lo que puede cambiar sin que nadie levante Docker.

### D12 — Aislamiento de la verificación

Toda verificación que levanta, rompe o borra pasa por un único wrapper,
`$VERIFY_DIR/vc.sh`, que vive **fuera del repo** junto con sus env files.
Desde que existe, nadie escribe `docker compose` a mano en la verificación. El
wrapper:

- fija `docker compose -p bot-resto-verify -f <repo>/docker-compose.yml
  --env-file "$VERIFY_DIR/<env file>"`. El nombre de proyecto está escrito en
  el wrapper y no se puede sobreescribir. `-p` reemplaza el `name:` del
  archivo, así que contenedores y volumen quedan con prefijo
  `bot-resto-verify`;
- usa `verify.env` por default (claves descartables, `POSTGRES_HOST_PORT=55433`,
  nombres por default) y acepta `--env <nombre>` solo como nombre de archivo
  dentro de `$VERIFY_DIR`, para los escenarios de clave faltante, puerto por
  default y base `foo`. `--env-file` reemplaza al `.env` del repo: la
  verificación no lee las claves reales de Lautaro ni las imprime;
- hace `unset` de toda `POSTGRES_*` heredada del shell, para que nada del
  entorno principal se cuele en la interpolación;
- arma y exporta la `DATABASE_URL` de verificación (`127.0.0.1:55433`)
  **solo dentro de su propio proceso**, y con `--run <comando>` corre un
  comando del host (alembic, pytest) con esa URL. El entorno del proceso tiene
  prioridad sobre `.env` para `Settings`, así que esos comandos apuntan a la
  base de verificación sin tocar el `.env`.

La `DATABASE_URL` de verificación nunca queda exportada en el shell principal:
cada `run-verify` usa la del `.env`. Si quedara exportada, un `run-verify`
posterior correría contra la base de verificación (o contra nada, después del
teardown) y daría un verde o un rojo que no dice nada de la base real.

- Ningún task usa `down -v` ni `docker volume rm` fuera del wrapper. Nunca
  `docker system prune` ni `docker volume prune`.
- Antes y después: `docker volume ls` y, si existe,
  `docker volume inspect bot-resto-dev_pgdata --format '{{.CreatedAt}}'`, para
  probar que el volumen de desarrollo no cambió.
- `docker compose config` solo se usa filtrado en el mismo comando (p. ej.
  `vc.sh config --format json | python -c …` imprimiendo solo `ports`): la
  salida completa nunca llega a la terminal, a un log ni al chat (`SEC-2`).
- El paso al proyecto de desarrollo real (parar `bot-resto-db`, `up` sin `-p`)
  es no destructivo y lo hace Lautaro al final.

## Risks / Trade-offs

- [La clave del rol queda en el entorno del contenedor y se ve con
  `docker inspect`] → aceptable para una base local en loopback; Docker
  secrets es non-goal.
- [Clave duplicada en `POSTGRES_APP_PASSWORD` y `DATABASE_URL`] → si difieren,
  la app falla al conectar con un error de autenticación, no en silencio. El
  README muestra ambas juntas.
- [Cambiar una clave en `.env` después del primer arranque no hace nada,
  porque el init no se re-ejecuta] → documentado; el único camino es
  `down -v` + `up` (o `ALTER ROLE` a mano, que el README no recomienda).
- [`down -v` en el proyecto de desarrollo borra el menú cargado a mano, sin
  seed] → el README lo marca como el único comando destructivo y recomienda
  `pg_dump` antes; la verificación nunca lo corre sobre ese proyecto (D12).
- [Puerto 5433 ocupado por `bot-resto-db`] → error visible en `up`; el README
  dice cómo pasar de uno a otro.
- [En Windows, el script de init podría perder el bit de ejecución o llegar
  con CRLF] → D4 lo hace apto para `source`; `.gitattributes` fuerza LF; un
  task verifica `git ls-files --eol` y el primer arranque real.
- [Los tests estáticos no prueban runtime] → el runtime se verifica a mano en
  los tasks; los tests cubren las regresiones del archivo.

## Migration Plan

1. Merge del change (el CI no cambia; los tests unitarios nuevos corren en
   `verify`).
2. Cada desarrollador: si quiere conservar datos de `bot-resto-db`, `pg_dump`
   antes; parar `bot-resto-db`; agregar las `POSTGRES_*` a su `.env` y ajustar
   `DATABASE_URL`; `docker compose up -d --wait`; `alembic upgrade head`; si
   corresponde, restaurar en `bot_resto` con `--no-owner`.
3. Gabriel puede seguir con su PostgreSQL nativo; el compose usa 5433 y no lo
   pisa.
4. Rollback: `docker compose down` (sin `-v`) y volver a arrancar
   `bot-resto-db`; los archivos se revierten con un commit.
