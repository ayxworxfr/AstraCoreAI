"""SQL append-only transcript store."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from astracore.infrastructure.db.models import TranscriptEventRow
from astracore.infrastructure.db.session import get_session
from astracore.modules.chat.domain.message import Message
from astracore.modules.chat.domain.transcript import (
    TranscriptEntry,
    TranscriptKind,
    entries_to_messages,
    message_to_entries,
)


class SQLTranscriptStore:
    """Append-only JSON-in-SQL transcript；崩溃最多丢未 flush 的尾部事件。"""

    def __init__(self, db_url: str) -> None:
        self._db_url = db_url

    async def _next_seq(self, db: AsyncSession, conversation_id: str) -> int:
        result = await db.execute(
            select(func.coalesce(func.max(TranscriptEventRow.seq), 0)).where(
                TranscriptEventRow.conversation_id == conversation_id
            )
        )
        return int(result.scalar_one()) + 1

    async def _known_message_ids(self, db: AsyncSession, conversation_id: str) -> set[str]:
        result = await db.execute(
            select(TranscriptEventRow.message_id).where(
                TranscriptEventRow.conversation_id == conversation_id,
                TranscriptEventRow.message_id.is_not(None),
            )
        )
        return {row[0] for row in result.all() if row[0]}

    async def append_messages(self, conversation_id: str | UUID, messages: list[Message]) -> int:
        """将尚未记录的消息展开为事件并追加。返回新增事件数。"""
        cid = str(conversation_id)
        added = 0
        async with get_session(self._db_url) as db:
            known = await self._known_message_ids(db, cid)
            seq = await self._next_seq(db, cid)
            for message in messages:
                mid = str(message.id)
                if mid in known:
                    continue
                for entry in message_to_entries(message):
                    db.add(
                        TranscriptEventRow(
                            id=str(entry.id),
                            conversation_id=cid,
                            seq=seq,
                            kind=entry.kind.value,
                            content=entry.content,
                            tool_name=entry.tool_name,
                            tool_call_id=entry.tool_call_id,
                            tool_input=entry.tool_input,
                            message_id=mid,
                            meta=entry.metadata,
                            created_at=entry.created_at,
                        )
                    )
                    seq += 1
                    added += 1
                known.add(mid)
            if added:
                await db.commit()
        return added

    async def load_entries(self, conversation_id: str | UUID) -> list[TranscriptEntry]:
        cid = str(conversation_id)
        async with get_session(self._db_url) as db:
            result = await db.execute(
                select(TranscriptEventRow)
                .where(TranscriptEventRow.conversation_id == cid)
                .order_by(TranscriptEventRow.seq.asc())
            )
            rows = list(result.scalars().all())
        return [
            TranscriptEntry(
                id=UUID(row.id),
                kind=TranscriptKind(row.kind),
                content=row.content,
                tool_name=row.tool_name,
                tool_call_id=row.tool_call_id,
                tool_input=row.tool_input,
                message_id=row.message_id,
                metadata=row.meta or {},
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def load_messages(self, conversation_id: str | UUID) -> list[Message]:
        """从事件流完整重建消息（含工具轨迹）。"""
        return entries_to_messages(await self.load_entries(conversation_id))
