# BOT-RESTO

WhatsApp bot for restaurants built with FastAPI and PostgreSQL.

## Base de desarrollo local

`docker-compose.yml` levanta una PostgreSQL de desarrollo con el mismo contrato
que el CI: imagen `postgres:16.15`, un rol de la app sin privilegios
(`NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION`) y dos bases de ese rol,
la de la app y su gemela `<base>_test`, que usan los tests destructivos de
migración. Solo crea eso: el esquema sale de Alembic, y las migraciones no
corren solas al levantar la base.

Los datos viven en el volumen nombrado `bot-resto-dev_pgdata`, fuera del repo.
El puerto se publica solo en `127.0.0.1`, y el contenedor vuelve solo después
de reiniciar Docker o la máquina (`restart: unless-stopped`).

Los comandos son de PowerShell y se corren desde la raíz del repo. Necesitan
Docker Desktop y uv.

### Configuración (`.env`)

Descomentá en tu `.env` el bloque "Base de desarrollo local" de `.env.example`
y completá las dos claves. Con los defaults queda así (los valores de las
claves no van en ningún archivo versionado):

```dotenv
POSTGRES_PASSWORD=<clave del superusuario de bootstrap>
POSTGRES_APP_USER=bot_resto_user
POSTGRES_APP_PASSWORD=<clave del rol de la app>
POSTGRES_APP_DB=bot_resto
POSTGRES_HOST_PORT=5433
DATABASE_URL=postgresql+psycopg://bot_resto_user:<clave del rol de la app>@127.0.0.1:5433/bot_resto
```

- **Las dos claves son obligatorias y no tienen default.** Sin alguna,
  `docker compose up` sale con código 1, nombra la variable que falta y no
  crea nada.
- **Tienen que ser distintas.** Quien conozca la clave de la app no debería
  conocer también la del superusuario.
- **Solo letras y números.** Ningún carácter que en una URL se codifique con
  `%` (`@`, `/`, `:`, `%`, `#`, `?`, espacios): con esa URL, alembic falla y
  muestra la URL completa, clave incluida (D-15). Para generar una:

  ```powershell
  $bytes = New-Object byte[] 16
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  ($bytes | ForEach-Object { $_.ToString('x2') }) -join ''
  ```

- **Reemplazá el `DATABASE_URL` que ya tenías** por el de arriba. No alcanza
  con agregar las `POSTGRES_*`: si queda el viejo, la app y los tests siguen
  apuntando a la base anterior.
- **En `DATABASE_URL` va `127.0.0.1`, nunca `localhost`.** En Windows,
  `localhost` resuelve primero a `::1` (IPv6), y el puerto se publica solo en
  `127.0.0.1`: la conexión se cuelga sin dar error.
- **La clave del rol va dos veces:** en `POSTGRES_APP_PASSWORD` y dentro de
  `DATABASE_URL`. Si difieren, la app falla al conectar con un error de
  autenticación.
- **No definas `COMPOSE_PROJECT_NAME`** ni en el `.env` ni en el shell. Le
  cambiaría el nombre al proyecto y al volumen, y la base "desaparecería"
  (seguiría en el volumen viejo).
- **`docker compose config` imprime las claves interpoladas.** Su salida no se
  pega en ningún lado: ni en un issue, ni en un PR, ni en un chat (`SEC-2`).

### Uso diario

```powershell
docker compose up -d --wait     # levanta y espera a que la base esté usable
docker compose ps               # estado; tiene que decir "healthy"
docker compose logs db          # logs del servicio
docker compose stop             # la para sin borrar nada
docker compose down             # borra el contenedor y la red, NO los datos
```

`up -d --wait` devuelve el control recién cuando el rol de la app puede
conectarse por TCP a `<base>_test`, que es lo último que crea el
inicializador. Si la base ya está corriendo, un segundo `up -d` no hace nada.

Migraciones y tests contra esta base, con el `DATABASE_URL` del `.env`. Las
dos suites van por separado, como en el CI:

```powershell
Push-Location backend; uv run alembic upgrade head; Pop-Location
uv run pytest -m unit
uv run pytest -m integration
```

Para mirar la base como superusuario por el socket del contenedor (no pide
clave):

```powershell
docker compose exec db psql -U postgres -d bot_resto
```

### Cuándo corre el inicializador

El script `docker/postgres/init/10-create-app-role-and-databases.sh` corre
**una sola vez, con el volumen vacío**. Con datos presentes la imagen lo
saltea (en los logs: "Skipping initialization"). Por eso:

- **Cambiar una clave o un nombre en el `.env` después del primer arranque no
  hace nada.** Para que tome efecto hay que resetear (ver abajo).
- **Si el primer `up` falla** (`up --wait` sale con error, o el servicio queda
  `unhealthy`), el volumen puede haber quedado inicializado pero sin el rol ni
  las bases. Como los arranques siguientes saltean el inicializador, queda
  `unhealthy` para siempre. Mirá la causa con `docker compose logs db`,
  corregí el `.env` y recién ahí:

  ```powershell
  docker compose down -v
  docker compose up -d --wait
  ```

  **Esto solo vale si todavía no restauraste ni cargaste ningún dato.** Si ya
  hay datos, no es un volumen roto: es tu base, y `down -v` la borra.

### Resetear: el único comando destructivo

```powershell
docker compose down -v
```

Borra el contenedor, la red **y el volumen `bot-resto-dev_pgdata` con todos
los datos de desarrollo**: el esquema, y el menú o lo que hayas cargado a mano,
que no tiene seed. No hay forma de recuperarlos. Antes, si te importa algo de
lo que hay, sacá un dump fuera del repo:

```powershell
docker compose exec db pg_dump -U bot_resto_user -d bot_resto -Fc -f /tmp/bot_resto.dump
docker compose cp db:/tmp/bot_resto.dump "$env:USERPROFILE\bot_resto.dump"
```

Después del reset, `docker compose up -d --wait` vuelve a crear el rol y las
dos bases vacías, y hay que correr `alembic upgrade head` de nuevo.

### Pasar del contenedor manual `bot-resto-db`

El compose usa por default el mismo puerto (`5433`), así que los dos no pueden
estar levantados a la vez. Las bases `botresto*` del contenedor viejo **no
migran solas**: si no hacés el dump, la base nueva arranca vacía.

1. *(Opcional, si querés conservar lo cargado a mano.)* Ver qué bases hay y
   sacar un dump de la que uses, fuera del repo. `<usuario_viejo>` y
   `<base_vieja>` son los de tu contenedor:

   ```powershell
   docker exec bot-resto-db psql -U <usuario_viejo> -d postgres -l
   docker exec bot-resto-db pg_dump -U <usuario_viejo> -d <base_vieja> -Fc -f /tmp/botresto.dump
   docker cp bot-resto-db:/tmp/botresto.dump "$env:USERPROFILE\botresto.dump"
   ```

   El dump se escribe dentro del contenedor y se copia con `docker cp`, sin
   pasar por una redirección de PowerShell: `>` en PowerShell 5.1 reescribe
   la salida como texto y rompe un dump binario.

2. Anotar la política de reinicio del contenedor viejo, sacársela y pararlo.
   Sin el `docker update`, vuelve a ocupar el `5433` al reiniciar Docker o la
   máquina. No se borra: queda como respaldo.

   ```powershell
   docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' bot-resto-db
   docker update --restart=no bot-resto-db
   docker stop bot-resto-db
   ```

3. Configurar el `.env` (ver arriba) y levantar la base nueva:

   ```powershell
   docker compose up -d --wait
   ```

4. *(Solo si hiciste el paso 1.)* Restaurar el dump **sobre la base recién
   creada y vacía**, como `bot_resto_user` por el socket del contenedor.
   `--no-owner --no-privileges` deja todo a nombre del rol de la app, y
   `--single-transaction --exit-on-error` hace que se cargue entero o nada:

   ```powershell
   docker compose cp "$env:USERPROFILE\botresto.dump" db:/tmp/botresto.dump
   docker compose exec db pg_restore -U bot_resto_user -d bot_resto --no-owner --no-privileges --single-transaction --exit-on-error /tmp/botresto.dump
   ```

   Si falla, como fue en una sola transacción, la base sigue vacía: se puede
   corregir y reintentar.

5. **Recién después**, aplicar las migraciones. Si el dump venía de una
   revisión anterior, esto la lleva al head; si ya estaba en el head, no hace
   nada. Nunca al revés: con las migraciones ya aplicadas, el restore choca
   con las tablas que existen.

   ```powershell
   Push-Location backend; uv run alembic upgrade head; Pop-Location
   ```

6. Comprobar que todo anda contra la base nueva:

   ```powershell
   uv run pytest -m unit
   uv run pytest -m integration
   ```

Si algo sale mal, la vuelta atrás es `docker compose down` (sin `-v`), y después
`docker start bot-resto-db` y `docker update --restart=<la política anotada en el paso 2> bot-resto-db`.

Quien use un PostgreSQL nativo en el `5432` puede seguir con él: el compose no
lo toca.

## CI

Cada Pull Request hacia `develop` o `main` corre el workflow `ci`
(`.github/workflows/ci.yml`), con un único job `verify`. Corre al abrir el PR,
con cada commit nuevo y al reabrirlo. Si llega un commit nuevo mientras una
corrida del mismo PR sigue en curso, esa corrida se cancela. El workflow solo
lee el repo (`permissions: contents: read`): no despliega, no publica y no
escribe ramas. No usa secrets de GitHub; las credenciales que declara son de
prueba y valen solo para el contenedor de PostgreSQL de esa corrida.

### Qué corre y en qué orden

El job corre en `ubuntu-24.04` con un tope de 20 minutos. Los pasos van en
orden, y el primero que falla corta el job: los siguientes quedan salteados.

| # | Paso | Qué hace |
|---|------|----------|
| 1 | `Checkout` | `actions/checkout` fijado por SHA, sin dejar el token en `.git/config`. |
| 2 | `Conflict markers` | `git grep -nE '^(<{7}\|>{7})( \|$)' -- .`: falla si un archivo versionado tiene un marcador de conflicto de apertura (`<{7}`) o de cierre (`>{7}`) al inicio de línea, y lista archivo y línea. |
| 3 | `Set up uv and Python 3.12` | `astral-sh/setup-uv` fijado por SHA, uv `0.11.21`, Python `3.12` y cache de descargas invalidada por `uv.lock`. |
| 4 | `Lockfile in sync` | `uv lock --check`: falla si `uv.lock` no corresponde a `pyproject.toml`. |
| 5 | `Install dependencies` | `uv sync --frozen`: instala exactamente lo que dice `uv.lock`. |
| 6 | `Lint (ruff check)` | `uv run --frozen ruff check .` |
| 7 | `Format (ruff format --check)` | `uv run --frozen ruff format --check .` |
| 8 | `Typecheck (mypy backend/app)` | `uv run --frozen mypy backend/app` |
| 9 | `Unit tests (pytest -m unit)` | `uv run --frozen pytest -m unit` |
| 10 | `Create database role and databases` | Espera a PostgreSQL y, con el superusuario de bootstrap, crea el rol `bot_resto_ci` (sin superusuario, sin `CREATEDB`, sin `CREATEROLE`) y las bases `bot_resto_ci` y `bot_resto_ci_test`, ambas con ese rol como dueño. |
| 11 | `Apply migrations (alembic upgrade head)` | Desde `backend/`: `uv run --frozen alembic upgrade head` sobre `bot_resto_ci`. |
| 12 | `Integration tests (pytest -m integration)` | `uv run --frozen pytest -m integration`. Los tests destructivos de migración usan `bot_resto_ci_test`. |
| 13 | `Application database intact` | Como `bot_resto_ci`, confirma que `bot_resto_ci` sigue en el head de Alembic y conserva `alembic_version`, `customers` y `menu_items`. |

Los pasos 6, 7, 8, 9 y 12 son los cinco de `run-verify`, en el mismo orden.

La base es un service container `postgres:16.15` con healthcheck
`pg_isready`, creado para la corrida y descartado al terminar. El job le pasa
a todos los pasos `APP_ENV=test` y
`DATABASE_URL=postgresql+psycopg://bot_resto_ci:ci_test_only_password@localhost:5432/bot_resto_ci`.

### Reproducirlo en local

Los comandos son de bash (en Windows, Git Bash) y se corren desde la raíz del
repo. Necesitan Docker, uv y el cliente `psql`. La base es un contenedor
descartable en el puerto `55432`, para no chocar con la base de desarrollo.

1. Levantar PostgreSQL con la misma imagen y el mismo bootstrap que el CI:

   ```bash
   docker run --rm -d --name bot-resto-ci-local \
     -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=ci_bootstrap_test_password \
     -p 55432:5432 postgres:16.15
   ```

2. Exportar el mismo entorno que el job, cambiando solo el puerto. Las
   variables del proceso tienen prioridad sobre el `.env`:

   ```bash
   export APP_ENV=test
   export DATABASE_URL=postgresql+psycopg://bot_resto_ci:ci_test_only_password@localhost:55432/bot_resto_ci
   ```

3. Correr los pasos en el orden del CI, y frenar en el primero que falle:

   ```bash
   git grep -nE '^(<{7}|>{7})( |$)' -- .   # tiene que salir vacío
   uv lock --check
   uv sync --frozen
   uv run --frozen ruff check .
   uv run --frozen ruff format --check .
   uv run --frozen mypy backend/app
   uv run --frozen pytest -m unit
   ```

4. Crear el rol y las dos bases. Si `pg_isready` no da `accepting
   connections`, esperar unos segundos y repetir:

   ```bash
   PGPASSWORD=ci_bootstrap_test_password pg_isready -h localhost -p 55432 -U postgres
   PGPASSWORD=ci_bootstrap_test_password psql -h localhost -p 55432 -U postgres \
     -d postgres -v ON_ERROR_STOP=1 <<'SQL'
   CREATE ROLE bot_resto_ci LOGIN PASSWORD 'ci_test_only_password'
     NOSUPERUSER NOCREATEDB NOCREATEROLE;
   CREATE DATABASE bot_resto_ci OWNER bot_resto_ci;
   CREATE DATABASE bot_resto_ci_test OWNER bot_resto_ci;
   SQL
   ```

5. Aplicar las migraciones, correr la suite de integración y comprobar que la
   base de la app quedó intacta:

   ```bash
   (cd backend && uv run --frozen alembic upgrade head)
   uv run --frozen pytest -m integration
   (cd backend && uv run --frozen alembic heads)    # el head esperado
   psql "${DATABASE_URL/+psycopg/}" -At \
     -c "SELECT version_num FROM alembic_version" \
     -c "SELECT to_regclass('public.customers'), to_regclass('public.menu_items')"
   ```

   `version_num` tiene que coincidir con el head, y las dos tablas tienen que
   aparecer con su nombre, no vacías.

6. Borrar la base descartable:

   ```bash
   docker stop bot-resto-ci-local
   ```

Corré `pytest -m unit` y `pytest -m integration` por separado, como el CI. Hoy
`uv run pytest` sin marker, en un solo proceso, falla en 9 tests unitarios de
logging: `backend/alembic/env.py` llama a `fileConfig` sin
`disable_existing_loggers=False` y apaga los loggers de la app cuando los tests
de migración corren antes que los unitarios. Es un hallazgo abierto, ajeno a
este CI.

### Branch protection (paso manual en GitHub)

El workflow corre en cada PR, pero no bloquea el merge por sí solo. Para que
el check sea obligatorio, alguien con permisos de administración del repo
tiene que configurarlo a mano en GitHub (Settings → Branches o Rules), en
`develop` y en `main`:

- exigir que el status check `verify` pase antes de mergear (en el PR aparece
  como `ci / verify`);
- exigir al menos una revisión aprobada.

Esta configuración no está versionada en el repo: si se cambia, hay que
hacerlo en GitHub.

## Bitácora

### 02–03/10/2026 — `add-application-skeleton`

**Qué se hizo.** La aplicación pasó a existir: `backend/app/main.py` con lifespan, `GET /health` (200, o 503 con cuerpo si la base está caída), routers vacíos del webhook y del panel en prefijos disjuntos, configuración validada al arrancar (entorno + `config/restaurant.yaml`, `messages.yaml` y `http_errors.yaml`), formato de error uniforme, logging JSON con enmascarado de teléfonos y cliente HTTP saliente con timeouts. Toolchain: dependencias pineadas, ruff, mypy estricto, pytest con suites `unit` e `integration`. No cubre ningún caso de uso.

**Prompt usado.** `/opsx:propose` con alcance, 11 escenarios obligatorios, non-goals y restricciones de `.instructions.md`; después `/opsx:apply` por bloques (grupo 1; grupos 2 a 5; grupos 6 a 8), con `run-verify` después de cada tarea.

**Ajustes a mano sobre la spec.**
- Sin override de mypy para `app.models.*`: los modelos pasaban estricto igual.
- Textos de error HTTP en `http_errors.yaml`, separados del copy del cliente en `messages.yaml`, que tiene solo comentarios mientras no haya copy.
- Tres escenarios agregados durante el apply, cada uno aprobado antes de implementarse:
  - httpx no loguea URLs.
  - Los errores inesperados se loguean una vez y sin datos del cliente.
  - Los mensajes de excepción no llevan datos del cliente.

**Lo que corrigió el loop de `run-verify` y de la verificación.**
- **Tests que leían el `.env` real.** Un test que borraba `APP_ENV` del entorno igual levantaba la app, porque la clave volvía desde el `.env` del desarrollador. Se cortó esa lectura en la fixture de configuración.
- **Filtro de rutas vacío.** FastAPI 0.142 envuelve los routers incluidos en un objeto privado y el filtro por `APIRoute` devolvía una lista vacía. Los tests ahora recorren los routers propios y comparan contra el esquema OpenAPI.
- **Fuga de datos de clientes en el log del 500** (`@security`, reproducido con uvicorn real). Un `IntegrityError` con una dirección como parámetro dejaba dos records con la dirección: el de nuestro handler y el que uvicorn agregaba al re-levantarse la excepción. Ahora deja un solo record y sin la dirección:
  - un middleware propio reemplaza al handler de `Exception`;
  - el engine usa `hide_parameters=True`;
  - los errores de SQLAlchemy y de validación se loguean sin mensaje en cualquier eslabón de la cadena.

**Verificación.** `run-verify` en verde: 92 tests unitarios y 1 de integración contra PostgreSQL 16. Verificación funcional con `@api-explorer`: 8 PASS, 0 FAIL. `@security` en tres pasadas; en la última, 0 altos y 0 medios. Los bajos que quedaron están anotados como deuda para los changes que los disparan.

**Casos de uso cubiertos.** Ninguno, por diseño.

### 10/10/2026 — `add-menu-and-customer-repositories`

**Qué se hizo.** La capa de datos y servicios de UC-03 (ver el menú por categorías), UC-17 (marcar un item agotado o disponible) y UC-18 (editar nombre, descripción, precio y tiempo estimado), más obtener o crear un customer por teléfono. Servicios y schemas en `backend/app/domain/menu/` y `backend/app/domain/customers/`; repositorios en `backend/app/db/repositories/`. Los repositorios no commitean (la transacción es de quien llama), los servicios no ven la `Session`, y un mismo número nunca produce dos customers aunque lleguen dos mensajes a la vez (índice único + `INSERT ... ON CONFLICT DO NOTHING`). Sin endpoints, pantallas ni migraciones. Hecho en Claude Code (extensión de VS Code); los comandos `/opsx:*` no estaban instalados en la sesión, así que se siguió el mismo flujo con el CLI de OpenSpec 1.7.0. Los commits los hizo Lautaro, tarea por tarea.

**Prompt usado.** `/opsx:propose` con alcance, 16 escenarios obligatorios (dos propios: creación concurrente y repositorio que nunca commitea), non-goals y restricciones de `.instructions.md`: [COMPLETAR: prompt original de propose]. Después, dos rondas de decisiones sobre la propuesta y `/opsx:apply` por grupo, con `run-verify` después de cada tarea y el commit a cargo de Lautaro.

**Ajustes a mano sobre la spec.**
- **Lectura estricta de `GEN-1`:** los repositorios pasaron de `domain/<modulo>/repository.py` a `backend/app/db/repositories/`, y el test de AST recorre todo el paquete `domain`.
- **`MenuItemUpdate` tipado en lugar de `Mapping`**, con el criterio forma (Pydantic: tipo, largo, nulidad, campos desconocidos) contra reglas (servicio: límites de valor y de negocio). Un `null` explícito en el tiempo estimado pasó a ser error de forma.
- **Precio como texto decimal exacto (`SDD-2`, durante el apply de 2.1):** FastAPI valida el payload en modo Python después de `json.loads`, y con `strict` puro ningún request podía traer un precio. Ahora precio y tiempo aceptan texto exacto (`"9200.00"`, `"25"`) y siguen rechazando `float` y `bool` (`DAT-8`). Escenario aprobado antes de implementarlo.
- **Regrupado de tareas** (grupos 2 a 5) para que cada tarea entregue el comportamiento junto con sus tests, y cada repositorio con sus tests de integración.
- **`set_available` explícito** como interpretación de `DAT-5` ("togglea"), y el `NOT NULL` de `estimated_time_min` registrado como prerrequisito del change de pedidos.

**Lo que corrigió el loop de `run-verify` y de la verificación.**
- **Ningún request podía traer un precio.** `test_valid_payload_reaches_the_handler` (`tests/unit/test_menu_schemas.py`) devolvió 400 con `{"price": "9200.00"}`: `strict=True` rechazaba el `str` que entrega `json.loads`. Se frenó la tarea, se aprobó el escenario nuevo y `backend/app/domain/menu/schemas.py` sumó validadores `mode="before"` con un patrón ASCII explícito (el parser de Pydantic aceptaba `"١٢٣"` y `"1_000"`).
- **mypy contra "opcional pero no nulo".** El plugin de Pydantic rechazó `name: str = Field(default=None)` en `domain/menu/schemas.py`; quedó `X | None = None` más un validador que rechaza el `null` explícito.
- **`Row[Any]` en SQLAlchemy 2.1.** mypy rechazó el helper de `db/repositories/menu.py` porque `Row` es variádico; pasó a recibir `RowMapping`.
- **Orden con Ñ.** El test de collation esperaba "Nuez, Ñoquis" y PostgreSQL con `en_US.utf8` devolvió "Ñoquis, Nuez" (la Ñ como N acentuada). Se corrigió la expectativa y el design D7; la collation española quedó como deuda (D-33).
- **Ruff:** S603 en `tests/unit/test_domain_boundaries.py` (`subprocess` con script constante, `noqa` justificado), RUF003 por un dígito árabe en un comentario de `domain/customers/service.py`, un `noqa` sin uso y un import sin usar.

**Verificación.** `run-verify` en verde: ruff, format, mypy estricto (35 archivos), 242 tests unitarios y 34 de integración contra PostgreSQL 16. La suite de integración se corrió dos veces seguidas (la segunda con los archivos en orden inverso) y dejó 0 filas. Los tests de concurrencia pasaron 10 de 10 corridas. Pruebas de mutación: con `SELECT` antes del `INSERT` fallan los tests de carrera; con un `commit()` en el repositorio fallan los de rollback y el chequeo de fronteras. Auditoría: `/security-review` y un subagente independiente de solo lectura (en lugar de `@security`, no disponible en la sesión); 0 altos, 0 medios, 6 bajos anotados como deuda (D-24 a D-29).

**Casos de uso cubiertos.** La capa de dominio de UC-03, UC-17 y UC-18, sin pantallas ni guion de WhatsApp, y la identificación del customer por teléfono que usan todos los flujos.

### 11/10/2026 — `migrate-identifiers-to-bigint`

**Qué se hizo.** Las seis PK internas, sus seis FK dependientes y las seis secuencias de PostgreSQL quedaron en `BIGINT`. Los modelos declaran `BigInteger` explícitamente y conservan `int` en Python. Las migraciones incluyen guards que rechazan un downgrade cuando una PK o secuencia ya supera el rango de `INTEGER`. No cambió ningún contrato HTTP ni regla de negocio.

**Prompt usado.** “Quiero que completemos los tasks de migrate identifiers en el código”. El trabajo continuó task por task contra el change aprobado `migrate-identifiers-to-bigint`, ejecutando `run-verify` antes de marcar cada bloque.

**Ajustes realizados durante el apply.**
- Se creó `bot_resto_test`, una base PostgreSQL dedicada a migraciones destructivas. La fixture confirma el sufijo `_test`, reinicia solamente su esquema y nunca altera `bot_resto`.
- La preparación local se hace una sola vez con `sudo -u postgres createdb --owner=bot_resto_user bot_resto_test`; no requiere otorgar `CREATEDB` al rol de la aplicación.
- Se comprobó el contrato completo de PK, FK, acciones `ON DELETE`, índices y unicidad antes y después del upgrade.
- Las secuencias se llevaron al límite de `INTEGER`; las seis tablas generaron automáticamente el ID `2.147.483.648`.
- Se retiró de `alter_column` el argumento `autoincrement=True`, que Alembic ignora y advierte que solo aplica a MySQL. La autogeneración real quedó demostrada por los tests de PostgreSQL.
- Los downgrades se verifican por revisión: columnas (`88d85f44fb7d`) y secuencias (`c3a7b9d2e641`). No se reordenó el historial de migraciones.

**Lo que corrigió el loop de `run-verify`.**
- El rol `bot_resto_user` no tenía `CREATEDB`, como corresponde por mínimo privilegio. Se reemplazó la creación dinámica de bases por una base de test fija y protegida.
- `psycopg` rechazó inicialmente la URL SQLAlchemy `postgresql+psycopg://`; la fixture la normaliza a `postgresql://` solamente para la conexión directa de limpieza.
- Pytest podía incluir la contraseña en el reporte de un test fallido porque el fixture devolvía el DSN como texto. Ahora entrega un objeto `URL` de SQLAlchemy, cuyo `repr` enmascara el secreto, y conserva la cadena completa solo dentro de la fixture.
- PostgreSQL expone `conversations_customer_id_key` como restricción única y como índice físico. El contrato del test ahora protege ambas representaciones.
- Ruff detectó a tiempo bloques de tests desplazados durante la edición; se reubicaron antes de ejecutar la suite funcional.

**Verificación.** `run-verify` en verde: Ruff check y format, mypy estricto sobre 24 archivos, 92 tests unitarios y 8 de integración. Siete escenarios de integración prueban upgrade, datos relacionados, constraints, generación de IDs más allá de `INTEGER`, enforcement de FK, downgrade válido y rechazo atómico por overflow.

**Casos de uso cubiertos.** Ninguno de negocio. Se cubre la capacidad técnica de almacenamiento y generación de identificadores internos en PostgreSQL.

### 11–14/11/2026 — `add-ci-pipeline`

**Qué se hizo.** Cada Pull Request hacia `develop` o `main` pasa por el workflow `ci` (`.github/workflows/ci.yml`), un único job `verify` de 13 pasos. Arranca con una compuerta de marcadores de conflicto, sigue con uv y Python 3.12 fijados, el chequeo del lockfile y `uv sync --frozen`. Después corren los cinco pasos de `run-verify`: lint, format, mypy, unit e integración. Antes de integración se preparan un rol sin superusuario y las bases `bot_resto_ci` y `bot_resto_ci_test` sobre `postgres:16.15`, y se aplica `alembic upgrade head`. Al final se verifica que la base de la app quedó intacta. El token solo lee, no hay secrets de GitHub y las corridas viejas del mismo PR se cancelan. El README ganó la sección "CI" con la reproducción local y la branch protection como paso manual. Sin cambios en `backend/`, `tests/`, `config/`, `pyproject.toml` ni `uv.lock`.

**Prompt usado.** `/opsx:propose`: [COMPLETAR: prompt original de propose]. Después, `/opsx:apply add-ci-pipeline` grupo por grupo ("Trabajá SOLO el grupo N"), con la verificación del CI después de cada tarea y frenando en el primer rojo. Los commits los hizo Lautaro. Para la 6.1 se corrió la verificación local junto con una revisión de solo lectura del subagente `security`. Las tareas 6.2 a 6.6 se verificaron en GitHub y se registraron a partir de esos resultados.

**Ajustes realizados durante el apply.**
- **Imagen de PostgreSQL:** `postgres:16.15`, el último tag de la rama 16 que existía (verificado con `docker manifest inspect`; 16.16 todavía no estaba). La base local de desarrollo usa 16.13.
- **Verificación por marker:** como corre el CI, unit e integración van en procesos separados (ver el hallazgo de abajo).
- **Validación local de los pasos de base, migración e integridad** contra un contenedor descartable en el puerto 55432, nunca contra la base de desarrollo. El chequeo de base intacta se probó también con la revisión cambiada y con el esquema borrado: falló en los dos casos.

**Lo que corrigió el loop de `run-verify` y de la verificación.**
- **Logging apagado por Alembic (hallazgo abierto, no corregido).** `uv run pytest` sin marker, en un solo proceso, falla en 9 tests unitarios de logging. `test_bigint_migration.py` corre Alembic dentro del proceso, y `backend/alembic/env.py:25` llama a `fileConfig` con el default `disable_existing_loggers=True`, que deshabilita los loggers de la app para el resto de la sesión. Quedó documentado. No se arregló porque `backend/` está fuera del alcance (`SDD-3`).
- **Base de desarrollo apagada.** Dos veces `pytest -m integration` encontró `bot-resto-db` detenido. Se frenó y se esperó a que Lautaro la levantara; no fue un problema del workflow.

**Verificación.**
- **Local (6.1):** ruff, format, mypy estricto (35 archivos), 242 tests unitarios y 41 de integración.
- **GitHub, PR #30 a `develop` (corrida #2):** los 20 pasos del job en verde.
- **Borrador #31, cerrado sin mergear:** cada caso falló en el paso previsto, sin ejecutar los siguientes. Los casos fueron lint, formato, mypy, test unitario, test de integración, marcador de conflicto, lockfile desfasado y base inalcanzable.
- **Reproducibilidad:** el re-run del mismo commit dio el mismo resultado, y un push seguido canceló la corrida anterior.
- **Integración en CI:** los 7 tests de migración pasaron con el rol sin superusuario, y "Application database intact" dio verde.
- **Seguridad:** el subagente `security` dio 0 altos, 0 medios y 0 bajos, con 5 notas informativas.
- **Branch protection:** la configuró Gabriel el 14/11 en `develop` y `main`.

**Casos de uso cubiertos.** Ninguno de negocio. Se cubre la verificación automática y obligatoria de cada Pull Request.

### 16/11/2026 — `add-docker-compose-dev-db`

**Qué se hizo.** `docker compose up -d --wait` levanta la base de desarrollo con el mismo contrato que el CI: `postgres:16.15`, un rol `bot_resto_user` sin privilegios y las bases `bot_resto` y `bot_resto_test`, las dos de ese rol. Un script de init (`docker/postgres/init/`) crea solo eso, una única vez y con el volumen vacío; el esquema sigue saliendo de Alembic. El healthcheck se conecta por TCP como el rol de la app a la gemela `_test`, así que `healthy` significa que la base ya se puede usar. Los datos viven en un volumen nombrado acotado al proyecto (`bot-resto-dev_pgdata`), el puerto se publica solo en `127.0.0.1:5433` y el contenedor vuelve solo después de un reinicio. `.gitattributes` fuerza LF en los `.sh`. `.env.example` documenta las variables del compose solo como comentarios, y el README ganó la sección "Base de desarrollo local". 19 tests unitarios estáticos guardan el compose y el script. Sin cambios en `backend/`, `config/`, `pyproject.toml`, `uv.lock`, `ci.yml` ni en tests existentes.

**Prompt usado.** `/opsx:propose add-docker-compose-dev-db`, con el contexto de negocio, el estado verificado del repo, el alcance, las decisiones que el design tenía que resolver, 18 escenarios obligatorios, los non-goals y, como punto de control humano, que ningún comando destructivo pudiera alcanzar el volumen de desarrollo. Después, `/opsx:apply add-docker-compose-dev-db` de a un grupo o una task por vez, frenando al final de cada uno. Los commits los hizo Lautaro.

**Ajustes realizados durante el apply.**
- **Clave fuera de la línea de comando:** el script de init lee las variables con `\getenv` de psql en lugar de `-v`, para que la clave del rol no quede en la línea de comando de `psql`, y fija `log_min_error_statement = panic` (design D4).
- **Wrapper de verificación:** toda verificación que escribe o borra pasó por un `vc.sh` fuera del repo. Fija `-p bot-resto-verify`, el env file y `-f`, y rechaza cualquier `-p`, `-f`, `--env-file` o `--project-directory`. También limpia las variables `POSTGRES_*` y `COMPOSE_*` heredadas, solo deja correr `config` dentro de un pipe y solo apunta al puerto 55433.
- **Escenario nuevo (`SDD-2`):** los 4 hallazgos bajos de la revisión de seguridad eran huecos de los tests estáticos. Se cerraron con tres requirements y 8 tests nuevos, sin tocar el compose ni el script.
- **Hallazgos de la migración real (4.2), sumados al README:** con `localhost` en `DATABASE_URL`, la conexión se cuelga sin error, porque en Windows `localhost` resuelve primero a `::1` y el puerto se publica solo en `127.0.0.1`. Además, hay que reemplazar el `DATABASE_URL` viejo, no solo agregar las `POSTGRES_*`.

**Verificación.**
- **run-verify** en verde después de cada grupo: ruff, format, mypy estricto (35 archivos), 265 tests unitarios y 46 de integración.
- **Runtime en el proyecto aislado `bot-resto-verify`** (puerto 55433, claves descartables):
  - sin cada clave, `up` sale con 1 y no crea nada;
  - el primer `up --wait` queda `healthy` después de que termina el init;
  - el rol es `t|f|f|f|f` y `CREATE DATABASE` falla con `42501`;
  - 0 tablas antes de migrar, `en_US.utf8`, `unless-stopped`;
  - `alembic upgrade head` más la suite de integración pasan contra la base nueva, y la base de la app queda intacta;
  - una fila sobrevive a `down` + `up`, y un segundo `up` no hace nada;
  - `down -v` borra solo su propio volumen;
  - con `POSTGRES_APP_DB=foo` se crean `foo` y `foo_test`.
- **Aislamiento:** `docker volume ls` quedó idéntico a la línea base de 15 volúmenes, incluidos los de `bot-resto-db`.
- **Guards estáticos:** cada guard falló ante una mutación temporal del compose o del script; después de restaurar, `git diff` quedó vacío.
- **Seguridad:** el subagente `security` dio 0 altos, 0 medios, 4 bajos (cerrados por el escenario nuevo) y 5 informativos. Los informativos se resolvieron con dos notas en el README y dos deudas, D-34 y D-35; el pin por digest quedó aceptado.
- **Migración real:** Lautaro paró `bot-resto-db` (sin datos; le sacó el reinicio automático), levantó la base nueva y aplicó las 4 revisiones desde cero. run-verify dio verde contra ella.

**Casos de uso cubiertos.** Ninguno de negocio. Se cubre una base de desarrollo reproducible, con el mismo contrato que el CI, para los changes que siguen (`add-menu-seed`, `add-orders-core`).
