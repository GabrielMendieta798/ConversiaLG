"""Migration contract for ``processed_whatsapp_messages``."""

from typing import Any

import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.engine import URL

from alembic import command
from app.core.config import PROJECT_ROOT

PARENT_REVISION = "88d85f44fb7d"
IDEMPOTENCY_REVISION = "57f88cf9ee63"
TABLE_NAME = "processed_whatsapp_messages"


def _alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))


def _ordered(values: list[tuple[object, ...]]) -> tuple[tuple[object, ...], ...]:
    return tuple(sorted(values, key=repr))


def _schema_contract(engine: sa.Engine) -> dict[str, dict[str, Any]]:
    inspector = sa.inspect(engine)
    contract: dict[str, dict[str, Any]] = {}

    for table_name in inspector.get_table_names():
        columns = tuple(
            (
                column["name"],
                column["type"].compile(dialect=engine.dialect).upper(),
                column["nullable"],
                column.get("default"),
                repr(column.get("identity")),
                repr(column.get("computed")),
            )
            for column in inspector.get_columns(table_name)
        )
        foreign_keys = _ordered(
            [
                (
                    tuple(foreign_key["constrained_columns"]),
                    foreign_key["referred_table"],
                    tuple(foreign_key["referred_columns"]),
                    foreign_key.get("options", {}).get("ondelete"),
                    foreign_key.get("options", {}).get("onupdate"),
                )
                for foreign_key in inspector.get_foreign_keys(table_name)
            ]
        )
        indexes = _ordered(
            [
                (
                    index["name"],
                    tuple(index["column_names"]),
                    index["unique"],
                )
                for index in inspector.get_indexes(table_name)
            ]
        )
        unique_constraints = _ordered(
            [
                (
                    constraint["name"],
                    tuple(constraint["column_names"]),
                )
                for constraint in inspector.get_unique_constraints(table_name)
            ]
        )
        check_constraints = _ordered(
            [
                (constraint["name"], constraint["sqltext"])
                for constraint in inspector.get_check_constraints(table_name)
            ]
        )
        contract[table_name] = {
            "columns": columns,
            "primary_key": tuple(
                inspector.get_pk_constraint(table_name)["constrained_columns"]
            ),
            "foreign_keys": foreign_keys,
            "indexes": indexes,
            "unique_constraints": unique_constraints,
            "check_constraints": check_constraints,
        }

    return contract


def _current_revision(engine: sa.Engine) -> str:
    with engine.connect() as connection:
        return connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one()


def _assert_processed_message_contract(engine: sa.Engine) -> None:
    inspector = sa.inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns(TABLE_NAME)}

    assert set(columns) == {"message_id", "received_at"}

    message_id = columns["message_id"]
    assert isinstance(message_id["type"], sa.Text)
    assert message_id["nullable"] is False
    assert message_id["default"] is None

    received_at = columns["received_at"]
    assert isinstance(received_at["type"], sa.DateTime)
    assert received_at["type"].timezone is True
    assert received_at["nullable"] is False
    assert str(received_at["default"]).lower() == "now()"

    assert inspector.get_pk_constraint(TABLE_NAME)["constrained_columns"] == [
        "message_id"
    ]
    assert inspector.get_foreign_keys(TABLE_NAME) == []
    assert inspector.get_indexes(TABLE_NAME) == []
    assert inspector.get_unique_constraints(TABLE_NAME) == []
    assert inspector.get_check_constraints(TABLE_NAME) == []


def test_upgrade_and_downgrade_preserve_the_parent_schema(
    migration_database_url: URL,
) -> None:
    expected_database = migration_database_url.database
    assert expected_database is not None
    assert expected_database.endswith("_test")

    config = _alembic_config()
    command.upgrade(config, PARENT_REVISION)

    engine = sa.create_engine(migration_database_url)
    try:
        with engine.connect() as connection:
            actual_database = connection.execute(
                sa.text("SELECT current_database()")
            ).scalar_one()
            assert actual_database == expected_database
        parent_contract = _schema_contract(engine)
        assert _current_revision(engine) == PARENT_REVISION
        assert TABLE_NAME not in parent_contract
    finally:
        engine.dispose()

    command.upgrade(config, IDEMPOTENCY_REVISION)

    engine = sa.create_engine(migration_database_url)
    try:
        upgraded_contract = _schema_contract(engine)
        assert set(upgraded_contract) == {*parent_contract, TABLE_NAME}
        assert {
            table_name: upgraded_contract[table_name] for table_name in parent_contract
        } == parent_contract
        _assert_processed_message_contract(engine)
        assert _current_revision(engine) == IDEMPOTENCY_REVISION
    finally:
        engine.dispose()

    command.downgrade(config, PARENT_REVISION)

    engine = sa.create_engine(migration_database_url)
    try:
        assert _schema_contract(engine) == parent_contract
        assert not sa.inspect(engine).has_table(TABLE_NAME)
        assert _current_revision(engine) == PARENT_REVISION
    finally:
        engine.dispose()
