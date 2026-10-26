from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ChatMessage, Conversation


class ConversationNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class ConversationRecord:
    id: int
    title: str
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class MessageRecord:
    id: int
    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None
    created_at: datetime | None


def _conversation(row: Conversation) -> ConversationRecord:
    return ConversationRecord(row.id, row.title, row.created_at, row.updated_at)


def _message(row: ChatMessage) -> MessageRecord:
    return MessageRecord(row.id, row.role, row.content, row.tool_calls, row.created_at)


class ConversationService:
    """Chat history. Every lookup is scoped to the owner, so one user can never read,
    extend or delete another user's conversation; a foreign id looks like a missing one."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _owned(self, user_id: int, conversation_id: int) -> Conversation:
        row = self._session.get(Conversation, conversation_id)
        if row is None or row.user_id != user_id:
            raise ConversationNotFoundError(f"Conversation {conversation_id} was not found")
        return row

    def list_conversations(self, user_id: int) -> list[ConversationRecord]:
        statement = (
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        )
        return [_conversation(r) for r in self._session.scalars(statement).all()]

    def create(self, user_id: int, first_message: str) -> ConversationRecord:
        title = " ".join(first_message.split())[:60] or "New conversation"
        row = Conversation(user_id=user_id, title=title)
        self._session.add(row)
        self._session.flush()
        return _conversation(row)

    def get_messages(
        self, user_id: int, conversation_id: int, *, last: int | None = None
    ) -> list[MessageRecord]:
        self._owned(user_id, conversation_id)
        statement = select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
        if last is not None:
            rows = list(
                self._session.scalars(statement.order_by(ChatMessage.id.desc()).limit(last)).all()
            )
            rows.reverse()
        else:
            rows = list(self._session.scalars(statement.order_by(ChatMessage.id)).all())
        return [_message(r) for r in rows]

    def append(
        self,
        user_id: int,
        conversation_id: int,
        *,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> MessageRecord:
        conversation = self._owned(user_id, conversation_id)
        row = ChatMessage(
            conversation_id=conversation_id, role=role, content=content, tool_calls=tool_calls
        )
        self._session.add(row)
        conversation.updated_at = datetime.now().astimezone()
        self._session.flush()
        return _message(row)

    def delete(self, user_id: int, conversation_id: int) -> None:
        self._session.delete(self._owned(user_id, conversation_id))
        self._session.flush()
