"""PostgreSQL implementation of the conversation store, plus chat records."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from receptionist.core.agent import context_tokens
from receptionist.core.ports import SessionState
from receptionist.db.models import Chat, Message, Session
from receptionist.db.repositories import Sessions


class SqlConversationStore:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def current_session(self, chat_id: int) -> SessionState | None:
        async with self._sessions() as db:
            session = await db.scalar(
                select(Session).where(Session.chat_id == chat_id, Session.ended_at.is_(None))
            )
            if session is None:
                return None
            rows = (
                await db.scalars(
                    select(Message)
                    .where(Message.session_id == session.id)
                    .order_by(Message.ordinal)
                )
            ).all()
        last_assistant = next((m for m in reversed(rows) if m.role == "assistant"), None)
        return SessionState(
            id=session.id,
            fingerprint=session.prompt_fingerprint,
            messages=[{"role": m.role, "content": m.content} for m in rows],
            last_activity=rows[-1].created_at if rows else session.started_at,
            context_tokens=context_tokens(last_assistant.usage if last_assistant else None),
        )

    async def start_session(self, chat_id: int, fingerprint: str, at: datetime) -> SessionState:
        async with self._sessions.begin() as db:
            session = Session(chat_id=chat_id, prompt_fingerprint=fingerprint, started_at=at)
            db.add(session)
            await db.flush()
            return SessionState(
                id=session.id,
                fingerprint=fingerprint,
                messages=[],
                last_activity=at,
                context_tokens=0,
            )

    async def end_session(self, session_id: int, summary: str | None, at: datetime) -> None:
        async with self._sessions.begin() as db:
            await db.execute(
                update(Session)
                .where(Session.id == session_id, Session.ended_at.is_(None))
                .values(ended_at=at, summary=summary)
            )

    async def append(
        self, session_id: int, role: str, content: list[dict], usage: dict | None, at: datetime
    ) -> None:
        next_ordinal = (
            select(func.coalesce(func.max(Message.ordinal) + 1, 0))
            .where(Message.session_id == session_id)
            .scalar_subquery()
        )
        async with self._sessions.begin() as db:
            await db.execute(
                insert(Message).values(
                    session_id=session_id,
                    ordinal=next_ordinal,
                    role=role,
                    content=content,
                    usage=usage,
                    created_at=at,
                )
            )


@dataclass(frozen=True)
class ChatRecord:
    id: int
    tg_chat_id: int
    language: str | None
    mode: str


class SqlChatRepository:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def ensure(self, tg_chat_id: int, language: str | None) -> ChatRecord:
        """The chat's record, created on first contact; the language is kept current."""
        new = pg_insert(Chat).values(tg_chat_id=tg_chat_id, language=language)
        stmt = new.on_conflict_do_update(
            index_elements=[Chat.tg_chat_id],
            set_={"language": func.coalesce(new.excluded.language, Chat.language)},
        ).returning(Chat.id, Chat.tg_chat_id, Chat.language, Chat.mode)
        async with self._sessions.begin() as db:
            row = (await db.execute(stmt)).one()
        return ChatRecord(*row)

    async def forget(self, tg_chat_id: int) -> bool:
        """Delete everything stored about the chat (/forget). True if it existed."""
        async with self._sessions.begin() as db:
            result = await db.execute(delete(Chat).where(Chat.tg_chat_id == tg_chat_id))
        return result.rowcount > 0
