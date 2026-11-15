"""Escenarios de la spec `domain-data-boundary` (GEN-1, DAT-2, CONV-10).

El dominio no ve la base: ningún módulo bajo `app/domain/` importa SQLAlchemy,
los modelos ORM, la configuración de la base ni los repositorios. Y los
repositorios no controlan la transacción: la cierra quien la abrió.

Los módulos se descubren recorriendo las carpetas, no con una lista fija, así
un módulo que sume un change futuro queda cubierto sin tocar este archivo.
"""

import ast
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.config import PROJECT_ROOT

BACKEND = PROJECT_ROOT / "backend"
DOMAIN_ROOT = BACKEND / "app" / "domain"
REPOSITORIES_ROOT = BACKEND / "app" / "db" / "repositories"
WHATSAPP_IDEMPOTENCY_REPOSITORY = (
    BACKEND / "app" / "integrations" / "whatsapp" / "idempotency_repository.py"
)

FORBIDDEN_IN_DOMAIN = ("sqlalchemy", "app.models", "app.core.database", "app.db")
TRANSACTION_CONTROL = frozenset(
    {"commit", "rollback", "begin", "begin_nested", "close"}
)


def _module_name(path: Path) -> str:
    parts = path.relative_to(BACKEND).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imported_modules(source: str, module: str, is_package: bool) -> Iterator[str]:
    """Todo nombre de módulo que el código importa, en cualquier nivel del árbol.

    Los imports relativos se resuelven contra el paquete del módulo. Para
    `from X import Y` se informan `X` y `X.Y`, porque `Y` puede ser un módulo
    (`from app import models`).
    """
    package = module if is_package else module.rpartition(".")[0]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base_parts = parts[: len(parts) - (node.level - 1)]
                if node.module:
                    base_parts.append(node.module)
                base = ".".join(base_parts)
            else:
                base = node.module or ""
            yield base
            yield from (f"{base}.{alias.name}" for alias in node.names)


def _forbidden_imports(source: str, module: str, is_package: bool) -> list[str]:
    return sorted(
        {
            name
            for name in _imported_modules(source, module, is_package)
            for forbidden in FORBIDDEN_IN_DOMAIN
            if name == forbidden or name.startswith(f"{forbidden}.")
        }
    )


def _transaction_calls(source: str) -> list[str]:
    return sorted(
        {
            node.func.attr
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in TRANSACTION_CONTROL
        }
    )


def _python_files(root: Path) -> list[Path]:
    files = sorted(root.rglob("*.py"))
    # Sin archivos el chequeo pasaría sin verificar nada.
    assert files, f"no Python files found under {root}"
    return files


TRANSACTION_OWNER_FILES = [
    *_python_files(REPOSITORIES_ROOT),
    WHATSAPP_IDEMPOTENCY_REPOSITORY,
]


# --- El detector mismo --------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import sqlalchemy", ["sqlalchemy"]),
        (
            "from sqlalchemy.orm import Session",
            ["sqlalchemy.orm", "sqlalchemy.orm.Session"],
        ),
        ("from app import models", ["app.models"]),
        (
            "from app.core.database import Base",
            ["app.core.database", "app.core.database.Base"],
        ),
        (
            "from ...db.repositories import menu",
            ["app.db.repositories", "app.db.repositories.menu"],
        ),
        ("def f():\n    import sqlalchemy\n", ["sqlalchemy"]),
        ("from app.core.exceptions import NotFoundError", []),
        ("from .schemas import MenuItemRead", []),
    ],
)
def test_detector_resolves_absolute_relative_and_nested_imports(
    source: str, expected: list[str]
) -> None:
    assert (
        _forbidden_imports(source, "app.domain.menu.service", is_package=False)
        == expected
    )


def test_detector_flags_transaction_control_calls() -> None:
    source = "def save(session):\n    session.flush()\n    session.commit()\n"

    assert _transaction_calls(source) == ["commit"]


# --- El dominio no ve la base ------------------------------------------------


@pytest.mark.parametrize(
    "path", _python_files(DOMAIN_ROOT), ids=lambda p: _module_name(p)
)
def test_domain_module_imports_no_persistence(path: Path) -> None:
    forbidden = _forbidden_imports(
        path.read_text(encoding="utf-8"),
        _module_name(path),
        is_package=path.name == "__init__.py",
    )

    assert forbidden == []


def test_importing_the_whole_domain_loads_no_sqlalchemy() -> None:
    # Intérprete limpio: cubre lo transitivo, que el AST no ve (un módulo
    # permitido que a su vez importe SQLAlchemy).
    script = (
        "import importlib, json, pkgutil, sys\n"
        "import app.domain\n"
        "names = ['app.domain'] + [m.name for m in pkgutil.walk_packages("
        "app.domain.__path__, 'app.domain.')]\n"
        "for name in names:\n"
        "    importlib.import_module(name)\n"
        "loaded = sorted(m for m in sys.modules "
        "if m == 'sqlalchemy' or m.startswith('sqlalchemy.'))\n"
        "print(json.dumps({'imported': names, 'sqlalchemy': loaded}))\n"
    )

    result = subprocess.run(  # noqa: S603 — el script es una constante del test
        [sys.executable, "-c", script],
        cwd=BACKEND,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert "app.domain.menu" in report["imported"]
    assert "app.domain.customers" in report["imported"]
    assert report["sqlalchemy"] == []


# --- Los repositorios no controlan la transacción -----------------------------


@pytest.mark.parametrize("path", TRANSACTION_OWNER_FILES, ids=lambda p: _module_name(p))
def test_repository_module_never_controls_the_transaction(path: Path) -> None:
    assert _transaction_calls(path.read_text(encoding="utf-8")) == []
