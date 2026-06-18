"""ChatRunRow creation factory — shared between HTTP and scheduler layers."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from astracore.infrastructure.db.models import ChatRunRow
from astracore.infrastructure.db.session import get_session


async def create_chat_run_row(
    db_url: str,
    session_id: UUID,
    prompt: str,
    user_id: str = "default",
    trigger_source: str = "user",
    request_payload: dict[str, Any] | None = None,
) -> ChatRunRow:
    """Create and persist a new ChatRunRow in 'running' status.

    ``request_payload`` — serialised HTTP request dict (from the HTTP layer).
    When omitted (scheduler path), a minimal dict with ``trigger_source`` is stored.
    """
    run_id = str(uuid4())
    now = datetime.now(UTC)
    row = ChatRunRow(
        id=run_id,
        session_id=str(session_id),
        user_id=user_id,
        status="running",
        request=request_payload or {"message": prompt, "trigger_source": trigger_source},
        user_message=prompt,
        created_at=now,
        updated_at=now,
    )
    async with get_session(db_url) as db:
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row
