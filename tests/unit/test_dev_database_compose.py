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
