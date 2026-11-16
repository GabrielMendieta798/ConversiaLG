"""Escenarios de la spec `development-database` que se prueban sin Docker.

Leen `docker-compose.yml` y el workflow del CI como YAML: sin red, sin base y
sin levantar contenedores (VER-3). Protegen el archivo contra regresiones; el
comportamiento en runtime se verifica a mano (tasks del grupo 3, design D11).
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.core.config import PROJECT_ROOT, load_config

COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
CI_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
DATA_DIRECTORY = "/var/lib/postgresql/data"
INIT_SCRIPT = "./docker/postgres/init/10-create-app-role-and-databases.sh"
INIT_SCRIPT_FILE = PROJECT_ROOT / INIT_SCRIPT
ROLE_ATTRIBUTES = (
    "LOGIN",
    "NOSUPERUSER",
    "NOCREATEDB",
    "NOCREATEROLE",
    "NOREPLICATION",
)


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.fixture
def compose() -> dict[str, Any]:
    return _load(COMPOSE_FILE)


@pytest.fixture
def db_service(compose: dict[str, Any]) -> dict[str, Any]:
    service = compose["services"]["db"]
    assert isinstance(service, dict)
    return service


# --- Paridad con el CI ---------------------------------------------------------


def test_image_matches_the_ci_postgres_service(db_service: dict[str, Any]) -> None:
    compose_image = db_service["image"]
    ci_image = _load(CI_WORKFLOW)["jobs"]["verify"]["services"]["postgres"]["image"]

    assert compose_image == ci_image, (
        f"compose image {compose_image!r} differs from CI image {ci_image!r}"
    )


def test_image_is_an_exact_debian_tag(db_service: dict[str, Any]) -> None:
    # `postgres:<major>.<minor>`: ni `latest`, ni solo el major, ni alpine.
    assert re.fullmatch(r"postgres:\d+\.\d+", db_service["image"])


# --- Red y secretos ------------------------------------------------------------


def test_every_published_port_is_loopback_to_5432(db_service: dict[str, Any]) -> None:
    ports = db_service["ports"]

    assert ports
    for port in ports:
        assert isinstance(port, str)
        assert port.startswith("127.0.0.1:")
        assert port.endswith(":5432")


@pytest.mark.parametrize("variable", ["POSTGRES_PASSWORD", "POSTGRES_APP_PASSWORD"])
def test_password_is_required_with_no_literal_and_no_default(
    db_service: dict[str, Any], variable: str
) -> None:
    value = db_service["environment"][variable]

    # Exactamente `${VAR:?mensaje}`: sin valor literal y sin `:-default`.
    assert re.fullmatch(rf"\$\{{{variable}:\?[^}}]+\}}", value)


# --- Datos y aislamiento entre proyectos ---------------------------------------


def test_data_directory_uses_a_project_scoped_named_volume(
    compose: dict[str, Any], db_service: dict[str, Any]
) -> None:
    data_mounts = [
        mount for mount in db_service["volumes"] if mount["target"] == DATA_DIRECTORY
    ]

    assert len(data_mounts) == 1
    mount = data_mounts[0]
    assert mount["type"] == "volume"
    # Sin `name` ni `external`: Compose lo prefija con el nombre del proyecto,
    # así el `down -v` de otro proyecto nunca alcanza este volumen (design D6).
    declaration = compose["volumes"][mount["source"]] or {}
    assert "name" not in declaration
    assert "external" not in declaration


def test_only_bind_mount_is_the_read_only_init_script(
    db_service: dict[str, Any],
) -> None:
    binds = [mount for mount in db_service["volumes"] if mount["type"] != "volume"]

    assert binds == [
        {
            "type": "bind",
            "source": INIT_SCRIPT,
            "target": "/docker-entrypoint-initdb.d/10-create-app-role-and-databases.sh",
            "read_only": True,
        }
    ]


# --- Convivencia y recuperación ------------------------------------------------


def test_no_fixed_container_name(db_service: dict[str, Any]) -> None:
    # Ni `bot-resto-db` (el contenedor manual) ni ningún otro: un nombre fijo
    # chocaría con el proyecto de verificación (design D6).
    assert "container_name" not in db_service


def test_restart_policy_is_unless_stopped(db_service: dict[str, Any]) -> None:
    assert db_service["restart"] == "unless-stopped"


def test_healthcheck_connects_over_tcp_as_the_app_role_to_the_twin(
    db_service: dict[str, Any],
) -> None:
    command = " ".join(db_service["healthcheck"]["test"])

    assert "pg_isready" not in command
    assert "-h 127.0.0.1" in command
    assert '-U "$$POSTGRES_APP_USER"' in command
    assert '-d "$${POSTGRES_APP_DB}_test"' in command


def test_healthcheck_reads_the_password_with_the_doubled_dollar(
    db_service: dict[str, Any],
) -> None:
    command = " ".join(db_service["healthcheck"]["test"])

    # Con un solo `$`, Compose interpola la clave y la escribe en la definición
    # del healthcheck (`docker inspect`, `docker compose config`): SEC-2.
    assert 'PGPASSWORD="$$POSTGRES_APP_PASSWORD"' in command
    single_dollar = re.findall(r"(?<!\$)\$(?=[{A-Za-z_])", command)
    assert single_dollar == [], "healthcheck has a single `$` before a variable"


# --- Forma del archivo ---------------------------------------------------------


def test_no_service_declares_network_mode(compose: dict[str, Any]) -> None:
    # `network_mode: host` ignora `ports` y expone la base en todas las
    # interfaces del host.
    for name, service in compose["services"].items():
        assert "network_mode" not in service, f"service {name!r} sets network_mode"


def test_compose_has_exactly_the_db_service_and_the_pgdata_volume(
    compose: dict[str, Any],
) -> None:
    # Los guards de puerto y de volumen miran `db` y `pgdata`: cualquier otro
    # servicio o volumen quedaría fuera de ellos.
    assert set(compose["services"]) == {"db"}
    assert set(compose["volumes"]) == {"pgdata"}


# --- Script de init --------------------------------------------------------------


def _init_script() -> str:
    return INIT_SCRIPT_FILE.read_text(encoding="utf-8")


def _heredoc(script: str) -> tuple[str, str]:
    """Devuelve la comilla del delimitador (`''` si no tiene) y el bloque SQL."""
    match = re.search(
        r"<<-?\s*(['\"]?)SQL\1[^\n]*\n(.*?)\n\s*SQL\s*$", script, re.S | re.M
    )
    assert match, "init script has no SQL heredoc"
    return match.group(1), match.group(2)


def _sql_statements(sql: str) -> list[str]:
    # Sin comentarios y sin meta-órdenes de psql (`\getenv …`), que no terminan
    # en `;` y quedarían pegadas a la sentencia siguiente.
    without_comments = re.sub(r"--[^\n]*|^\s*\\[^\n]*", "", sql, flags=re.M)
    return [
        " ".join(statement.split())
        for statement in without_comments.split(";")
        if statement.strip()
    ]


def test_init_role_has_every_minimum_privilege_attribute() -> None:
    _, sql = _heredoc(_init_script())
    roles = [s for s in _sql_statements(sql) if s.startswith("CREATE ROLE ")]

    assert len(roles) == 1
    # Uno por uno: `PASSWORD :'app_password'` queda entre los atributos.
    tokens = set(roles[0].split())
    for attribute in ROLE_ATTRIBUTES:
        assert attribute in tokens, f"CREATE ROLE lacks {attribute}"


def test_init_creates_exactly_the_two_databases_owned_by_the_app_role() -> None:
    _, sql = _heredoc(_init_script())
    databases = [s for s in _sql_statements(sql) if s.startswith("CREATE DATABASE ")]

    assert databases == [
        'CREATE DATABASE :"app_db" OWNER :"app_user"',
        'CREATE DATABASE :"app_test_db" OWNER :"app_user"',
    ]


def test_init_creates_no_schema_object_and_grants_nothing() -> None:
    # El esquema sale solo de Alembic (DAT-6).
    forbidden = re.findall(
        r"\bCREATE\s+(?:TABLE|SCHEMA|EXTENSION)\b|\bGRANT\b",
        _init_script(),
        re.I,
    )

    assert forbidden == [], f"init script contains {forbidden}"


def test_init_never_passes_the_password_with_a_psql_variable_flag() -> None:
    # `-v app_password=…` dejaría la clave en la línea de comando de psql,
    # visible en /proc del contenedor (design D4).
    assignments = re.findall(
        r"(?:-v|--set|--variable)[\s=]+\S*password\S*=", _init_script(), re.I
    )

    assert assignments == []


def test_init_sql_is_a_quoted_heredoc_without_shell_expansion() -> None:
    quote, sql = _heredoc(_init_script())

    assert quote in ("'", '"'), "SQL heredoc delimiter is not quoted"
    assert "$" not in sql, "SQL block contains a `$` expansion"


# --- La app no se ve afectada --------------------------------------------------


def test_settings_load_with_compose_variables_present(
    config_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for variable, value in {
        "POSTGRES_PASSWORD": "compose-bootstrap-placeholder",
        "POSTGRES_APP_USER": "bot_resto_user",
        "POSTGRES_APP_PASSWORD": "compose-app-placeholder",
        "POSTGRES_APP_DB": "bot_resto",
        "POSTGRES_HOST_PORT": "5433",
    }.items():
        monkeypatch.setenv(variable, value)

    config = load_config(env_file=None)

    assert config.settings.app_env == "test"
