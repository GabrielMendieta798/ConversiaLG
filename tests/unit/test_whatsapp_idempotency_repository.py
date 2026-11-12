"""Unit contract for the WhatsApp idempotency repository."""

from unittest.mock import Mock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.integrations.whatsapp.idempotency_repository import (
    MessageClaimResult,
    WhatsAppMessageIdempotencyRepository,
)


@pytest.mark.parametrize(
    ("returned_message_id", "expected"),
    [
        ("wamid.test.claimed", MessageClaimResult.CLAIMED),
        (None, MessageClaimResult.DUPLICATE),
    ],
)
def test_claim_maps_the_returning_row_to_an_explicit_result(
    returned_message_id: str | None,
    expected: MessageClaimResult,
) -> None:
    session = Mock(spec=Session)
    session.scalar.return_value = returned_message_id

    result = WhatsAppMessageIdempotencyRepository(session).claim("wamid.test.claimed")

    assert result is expected
    session.scalar.assert_called_once()


def test_claim_uses_one_atomic_postgresql_statement() -> None:
    session = Mock(spec=Session)
    session.scalar.return_value = "wamid.test.atomic"

    WhatsAppMessageIdempotencyRepository(session).claim("wamid.test.atomic")

    statement = session.scalar.call_args.args[0]
    sql = " ".join(str(statement.compile(dialect=postgresql.dialect())).upper().split())

    assert sql.startswith("INSERT INTO PROCESSED_WHATSAPP_MESSAGES")
    assert "ON CONFLICT (MESSAGE_ID) DO NOTHING" in sql
    assert "RETURNING PROCESSED_WHATSAPP_MESSAGES.MESSAGE_ID" in sql
