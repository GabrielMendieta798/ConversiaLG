"""PostgreSQL transaction contract for inbound WhatsApp message claims."""

import secrets

import pytest
from sqlalchemy import Connection, Engine, delete, func, select, text
from sqlalchemy.orm import Session

from app.core.database import build_session_factory
from app.integrations.whatsapp.idempotency_repository import (
    MessageClaimResult,
    WhatsAppMessageIdempotencyRepository,
)
from app.models.processed_whatsapp_message import ProcessedWhatsAppMessage


@pytest.fixture
def message_id() -> str:
    return f"wamid.test.{secrets.token_hex(12)}"


def _count(
    session_or_connection: Session | Connection,
    message_id: str,
) -> int:
    return session_or_connection.execute(
        select(func.count())
        .select_from(ProcessedWhatsAppMessage)
        .where(ProcessedWhatsAppMessage.message_id == message_id)
    ).scalar_one()


def _delete_claim(db_engine: Engine, message_id: str) -> None:
    with db_engine.begin() as connection:
        connection.execute(
            delete(ProcessedWhatsAppMessage).where(
                ProcessedWhatsAppMessage.message_id == message_id
            )
        )


def test_new_message_is_claimed_once_in_the_caller_transaction(
    db_session: Session,
    message_id: str,
) -> None:
    repository = WhatsAppMessageIdempotencyRepository(db_session)

    assert repository.claim(message_id) is MessageClaimResult.CLAIMED
    assert _count(db_session, message_id) == 1


def test_committed_message_is_reported_as_duplicate(
    db_engine: Engine,
    message_id: str,
) -> None:
    session_factory = build_session_factory(db_engine)

    try:
        with session_factory.begin() as first:
            assert (
                WhatsAppMessageIdempotencyRepository(first).claim(message_id)
                is MessageClaimResult.CLAIMED
            )

        with session_factory() as duplicate:
            assert (
                WhatsAppMessageIdempotencyRepository(duplicate).claim(message_id)
                is MessageClaimResult.DUPLICATE
            )
            duplicate.rollback()

        with db_engine.connect() as connection:
            assert _count(connection, message_id) == 1
    finally:
        _delete_claim(db_engine, message_id)


def test_claim_is_invisible_from_another_connection_before_caller_commit(
    db_engine: Engine,
    message_id: str,
) -> None:
    session_factory = build_session_factory(db_engine)

    with session_factory() as claimant:
        assert (
            WhatsAppMessageIdempotencyRepository(claimant).claim(message_id)
            is MessageClaimResult.CLAIMED
        )
        claimant_backend = claimant.scalar(text("SELECT pg_backend_pid()"))

        with db_engine.connect() as observer:
            observer_backend = observer.scalar(text("SELECT pg_backend_pid()"))
            assert observer_backend != claimant_backend
            assert _count(observer, message_id) == 0

        claimant.rollback()


def test_caller_rollback_allows_a_later_transaction_to_claim(
    db_engine: Engine,
    message_id: str,
) -> None:
    session_factory = build_session_factory(db_engine)

    with session_factory() as first:
        assert (
            WhatsAppMessageIdempotencyRepository(first).claim(message_id)
            is MessageClaimResult.CLAIMED
        )
        first.rollback()

    with session_factory() as retry:
        assert (
            WhatsAppMessageIdempotencyRepository(retry).claim(message_id)
            is MessageClaimResult.CLAIMED
        )
        retry.rollback()

    with db_engine.connect() as observer:
        assert _count(observer, message_id) == 0
