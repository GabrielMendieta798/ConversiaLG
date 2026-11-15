"""Repositorio del menú contra PostgreSQL real (VER-3).

Spec `menu-catalog`: lo que solo la base puede probar (collation del orden,
que la fila no se borre, el snapshot de DAT-4, rango de columnas) y spec
`domain-data-boundary` (Decimal con dos decimales, fechas en UTC, datos planos).
Las reglas sin base están en `tests/unit/test_menu_service.py`.
"""

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.exceptions import InvalidInputError
from app.db.repositories.menu import SqlMenuRepository
from app.domain.menu.schemas import MenuItemRead, MenuItemUpdate
from app.domain.menu.service import MenuService
from app.models.menu_item import MenuItem
from app.models.order_item import OrderItem

MakeItem = Callable[..., int]


@pytest.fixture
def repo(db_session: Session) -> SqlMenuRepository:
    return SqlMenuRepository(db_session)


@pytest.fixture
def service(repo: SqlMenuRepository) -> MenuService:
    return MenuService(repo)


def _stored_row(session: Session, item_id: int) -> dict[str, Any] | None:
    """La fila tal como está en la tabla, sin pasar por el repositorio."""
    row = (
        session.execute(select(MenuItem.__table__).where(MenuItem.id == item_id))
        .mappings()
        .one_or_none()
    )
    return None if row is None else dict(row)


def _names_and_ids(items: list[MenuItemRead]) -> list[tuple[str, int]]:
    return [(item.name, item.id) for item in items]


# --- Categorías ----------------------------------------------------------------


def test_categories_only_with_an_available_item_once_and_sorted(
    repo: SqlMenuRepository, make_item: MakeItem, category: str
) -> None:
    with_available = f"{category}-a"
    later = f"{category}-b"
    sold_out = f"{category}-c"
    make_item("Bondiola", in_category=with_available)
    make_item("Vacío", in_category=with_available, available=False)
    make_item("Flan", in_category=later)
    make_item("Matambre", in_category=sold_out, available=False)

    categories = repo.list_categories()

    assert categories.count(with_available) == 1
    assert sold_out not in categories
    assert categories.index(with_available) < categories.index(later)


def test_last_available_item_sold_out_hides_the_category(
    repo: SqlMenuRepository, make_item: MakeItem, category: str
) -> None:
    only = make_item("Flan")
    assert category in repo.list_categories()

    repo.set_available(only, False)

    assert category not in repo.list_categories()


# --- Listados ------------------------------------------------------------------


def test_available_items_are_filtered_and_ordered_by_name_then_id(
    repo: SqlMenuRepository, make_item: MakeItem, category: str
) -> None:
    first_ravioles = make_item("Ravioles")
    bondiola = make_item("Bondiola")
    second_ravioles = make_item("Ravioles")
    make_item("Vacío", available=False)

    items = repo.list_available_items(category)

    assert _names_and_ids(items) == [
        ("Bondiola", bondiola),
        ("Ravioles", first_ravioles),
        ("Ravioles", second_ravioles),
    ]
    bondiola_item = items[0]
    assert bondiola_item.price == Decimal("8500.00")
    assert bondiola_item.estimated_time_min == 25


def test_order_follows_the_database_collation(
    repo: SqlMenuRepository, db_session: Session, make_item: MakeItem, category: str
) -> None:
    sensitive_names = ("Ravioles", "Ñoquis", "Nuez", "Orecchiette")
    for name in sensitive_names:
        make_item(name)

    expected_rows = db_session.execute(
        text(
            """
            SELECT name, pg_collation_for(name)::text AS collation
            FROM (
                VALUES
                    (CAST(:ravioles AS VARCHAR(150))),
                    (CAST(:noquis AS VARCHAR(150))),
                    (CAST(:nuez AS VARCHAR(150))),
                    (CAST(:orecchiette AS VARCHAR(150)))
            ) AS expected(name)
            ORDER BY name
            """
        ),
        {
            "ravioles": sensitive_names[0],
            "noquis": sensitive_names[1],
            "nuez": sensitive_names[2],
            "orecchiette": sensitive_names[3],
        },
    ).all()
    column_collation = db_session.execute(
        text(
            """
            SELECT pg_collation_for(name)::text
            FROM menu_items
            WHERE category = :category
            LIMIT 1
            """
        ),
        {"category": category},
    ).scalar_one()
    assert {row.collation for row in expected_rows} == {column_collation}

    names = [item.name for item in repo.list_available_items(category)]

    assert names == [row.name for row in expected_rows]


def test_unknown_category_is_an_empty_list(
    repo: SqlMenuRepository, category: str
) -> None:
    assert repo.list_available_items(f"{category}-none") == []


def test_staff_list_includes_sold_out_items_in_the_same_order(
    repo: SqlMenuRepository, make_item: MakeItem, category: str
) -> None:
    make_item("Vacío", available=False)
    make_item("Bondiola")
    make_item("Bondiola", available=False)

    ours = [item for item in repo.list_all_items() if item.category == category]

    assert [(item.name, item.available) for item in ours] == [
        ("Bondiola", True),
        ("Bondiola", False),
        ("Vacío", False),
    ]
    assert _names_and_ids(ours) == sorted(_names_and_ids(ours))


def test_lookup_finds_an_unavailable_item_and_none_when_missing(
    repo: SqlMenuRepository, make_item: MakeItem
) -> None:
    vacio = make_item("Vacío", available=False)

    found = repo.get_item(vacio)

    assert found is not None
    assert found.available is False
    assert repo.get_item(vacio + 1_000_000) is None


def test_item_without_estimated_time_reads_it_as_absent(
    repo: SqlMenuRepository, make_item: MakeItem, category: str
) -> None:
    agua = make_item("Agua", estimated_time_min=None)

    found = repo.get_item(agua)

    assert found is not None
    assert found.estimated_time_min is None
    assert repo.list_available_items(category)[0].estimated_time_min is None


# --- Disponibilidad ------------------------------------------------------------


def test_sold_out_keeps_the_row_and_every_other_column(
    repo: SqlMenuRepository, db_session: Session, make_item: MakeItem, category: str
) -> None:
    bondiola = make_item("Bondiola", description="Con papas")
    before = _stored_row(db_session, bondiola)

    repo.set_available(bondiola, False)

    after = _stored_row(db_session, bondiola)
    assert after is not None, "the item was deleted"
    assert before is not None
    assert after["available"] is False
    assert {k: v for k, v in after.items() if k != "available"} == {
        k: v for k, v in before.items() if k != "available"
    }
    assert repo.list_available_items(category) == []


@pytest.mark.parametrize("available", [False, True])
def test_setting_the_same_availability_twice(
    repo: SqlMenuRepository, make_item: MakeItem, available: bool
) -> None:
    item_id = make_item("Bondiola", available=not available)

    first = repo.set_available(item_id, available)
    second = repo.set_available(item_id, available)

    assert first is not None
    assert first.available is available
    assert second == first


def test_changes_on_a_missing_item_return_none(
    repo: SqlMenuRepository, make_item: MakeItem
) -> None:
    missing = make_item("Bondiola") + 1_000_000

    assert repo.set_available(missing, False) is None
    assert repo.update_item(missing, MenuItemUpdate(name="Matambre")) is None


# --- Edición -------------------------------------------------------------------


def test_editing_only_the_price_leaves_every_other_column(
    service: MenuService, db_session: Session, make_item: MakeItem
) -> None:
    bondiola = make_item("Bondiola", description="Con papas")
    before = _stored_row(db_session, bondiola)

    service.update_item(bondiola, MenuItemUpdate(price=Decimal("9200.00")))

    after = _stored_row(db_session, bondiola)
    assert before is not None
    assert after is not None
    assert after["price"] == Decimal("9200.00")
    assert {k: v for k, v in after.items() if k != "price"} == {
        k: v for k, v in before.items() if k != "price"
    }


def test_price_edit_keeps_the_confirmed_order_snapshot(
    service: MenuService,
    db_session: Session,
    make_item: MakeItem,
    make_order_line: Callable[[int, str], int],
) -> None:
    bondiola = make_item("Bondiola", price="8500.00")
    line = make_order_line(bondiola, "8500.00")

    updated = service.update_item(bondiola, MenuItemUpdate(price=Decimal("9200.00")))

    assert updated.price == Decimal("9200.00")
    snapshot = db_session.execute(
        select(OrderItem.unit_price_snapshot).where(OrderItem.id == line)
    ).scalar_one()
    assert snapshot == Decimal("8500.00")


def test_trailing_zeros_are_stored_with_two_places(
    service: MenuService, db_session: Session, make_item: MakeItem
) -> None:
    bondiola = make_item("Bondiola")

    updated = service.update_item(bondiola, MenuItemUpdate(price="1500.500"))

    assert updated.price.as_tuple() == Decimal("1500.50").as_tuple()
    stored = _stored_row(db_session, bondiola)
    assert stored is not None
    assert stored["price"].as_tuple() == Decimal("1500.50").as_tuple()


@pytest.mark.parametrize(
    "changes",
    [
        {"price": Decimal("10000000000.00")},
        {"estimated_time_min": 2147483648},
    ],
    ids=["price-beyond-numeric-12-2", "time-beyond-integer"],
)
def test_value_beyond_the_column_is_invalid_input_not_a_database_error(
    service: MenuService,
    db_session: Session,
    make_item: MakeItem,
    changes: dict[str, Any],
) -> None:
    bondiola = make_item("Bondiola")
    before = _stored_row(db_session, bondiola)

    with pytest.raises(InvalidInputError):
        service.update_item(bondiola, MenuItemUpdate(**changes))

    # La transacción sigue sana: ningún error de la base la abortó.
    assert _stored_row(db_session, bondiola) == before


def test_name_of_150_characters_fits_the_column(
    service: MenuService, make_item: MakeItem
) -> None:
    bondiola = make_item("Bondiola")

    updated = service.update_item(bondiola, MenuItemUpdate(name="x" * 150))

    assert updated.name == "x" * 150


# --- Tipos que vuelven de la base ----------------------------------------------


def test_prices_are_decimals_with_two_places_and_never_floats(
    repo: SqlMenuRepository, make_item: MakeItem, category: str
) -> None:
    make_item("Bondiola", price="8500")
    make_item("Flan", price="3000.5")

    for item in repo.list_available_items(category):
        assert type(item.price) is Decimal
        assert item.price.as_tuple().exponent == -2


def test_created_at_is_aware_and_utc_whatever_the_session_time_zone(
    repo: SqlMenuRepository, db_session: Session, make_item: MakeItem, category: str
) -> None:
    db_session.execute(text("SET TIME ZONE 'America/Argentina/Buenos_Aires'"))
    bondiola = make_item("Bondiola")

    found = repo.get_item(bondiola)
    listed = repo.list_available_items(category)[0]
    updated = repo.set_available(bondiola, True)

    for item in (found, listed, updated):
        assert item is not None
        assert item.created_at.tzinfo is not None
        assert item.created_at.utcoffset() == timedelta(0)


def test_returned_values_are_plain_data_not_orm_instances(
    repo: SqlMenuRepository, make_item: MakeItem, category: str
) -> None:
    bondiola = make_item("Bondiola")

    returned = [
        repo.get_item(bondiola),
        repo.set_available(bondiola, True),
        repo.update_item(bondiola, MenuItemUpdate(name="Bondiola braseada")),
        *repo.list_available_items(category),
        *repo.list_all_items(),
    ]

    for value in returned:
        assert type(value) is MenuItemRead
        assert not isinstance(value, Base)
