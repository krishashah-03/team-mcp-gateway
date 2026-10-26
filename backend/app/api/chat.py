from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, Gateway, dashboard_principal
from app.chat.agent import run_agent
from app.chat.providers import ChatProviderError, LLMProvider, Turn, build_provider
from app.core.config import settings
from app.core.limiters import api_calls_by_user
from app.db.session import session_scope
from app.services.conversation_service import (
    ConversationNotFoundError,
    ConversationRecord,
    ConversationService,
    MessageRecord,
)

router = APIRouter(prefix="/chat", tags=["Chat agent"])


def get_chat_provider(request: Request) -> LLMProvider:
    """The configured model, or 503 with a clear reason when none is set up."""
    provider = getattr(request.app.state, "chat_provider", None) or build_provider(settings)
    if provider is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Chat is not configured: set OPENROUTER_API_KEY or GEMINI_API_KEY (and restart the gateway).",
        )
    return provider


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: int | None = Field(default=None, ge=1)


class ToolCallOut(BaseModel):
    name: str
    arguments: dict[str, Any]
    is_error: bool
    duration_ms: int
    result_preview: str


class ChatResponse(BaseModel):
    conversation_id: int
    reply: str
    tool_calls: list[ToolCallOut]
    steps: int


class ConversationOut(BaseModel):
    id: int
    title: str
    created_at: datetime | None
    updated_at: datetime | None


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    tool_calls: list[ToolCallOut] | None
    created_at: datetime | None


def _conversation_out(c: ConversationRecord) -> ConversationOut:
    return ConversationOut(id=c.id, title=c.title, created_at=c.created_at, updated_at=c.updated_at)


def _message_out(m: MessageRecord) -> MessageOut:
    return MessageOut(
        id=m.id,
        role=m.role,
        content=m.content,
        tool_calls=[ToolCallOut(**t) for t in m.tool_calls] if m.tool_calls else None,
        created_at=m.created_at,
    )


def _not_found(exc: ConversationNotFoundError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post("", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    user: CurrentUser,
    gateway: Gateway,
    provider: Annotated[LLMProvider, Depends(get_chat_provider)],
) -> ChatResponse:
    """Ask the assistant. It calls tools through the gateway *as you*, so its calls
    are checked against your permissions and appear in the audit log under your name."""
    allowed, retry_after = api_calls_by_user.hit(f"user:{user.id}")
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Slow down.",
            headers={"Retry-After": str(retry_after)},
        )

    def prepare() -> tuple[int, list[Turn]]:
        with session_scope() as session:
            service = ConversationService(session)
            if body.conversation_id is None:
                return service.create(user.id, body.message).id, []
            messages = service.get_messages(
                user.id, body.conversation_id, last=settings.chat_history_messages
            )
            return body.conversation_id, [
                Turn(role="user" if m.role == "user" else "model", text=m.content) for m in messages
            ]

    try:
        conversation_id, history = await run_in_threadpool(prepare)
    except ConversationNotFoundError as exc:
        raise _not_found(exc) from None

    try:
        outcome = await run_agent(
            provider=provider,
            gateway=gateway,
            principal=dashboard_principal(user, channel="chat"),
            history=history,
            message=body.message,
            settings=settings,
        )
    except ChatProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from None

    traces = [t.__dict__ for t in outcome.tool_calls]

    def save() -> None:
        with session_scope() as session:
            service = ConversationService(session)
            service.append(user.id, conversation_id, role="user", content=body.message)
            service.append(
                user.id,
                conversation_id,
                role="assistant",
                content=outcome.reply,
                tool_calls=traces or None,
            )

    await run_in_threadpool(save)
    return ChatResponse(
        conversation_id=conversation_id,
        reply=outcome.reply,
        tool_calls=[ToolCallOut(**t) for t in traces],
        steps=outcome.steps,
    )


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(user: CurrentUser) -> list[ConversationOut]:
    with session_scope() as session:
        return [_conversation_out(c) for c in ConversationService(session).list_conversations(user.id)]


@router.get("/conversations/{conversation_id}", response_model=list[MessageOut])
def get_conversation(conversation_id: int, user: CurrentUser) -> list[MessageOut]:
    try:
        with session_scope() as session:
            return [_message_out(m) for m in ConversationService(session).get_messages(user.id, conversation_id)]
    except ConversationNotFoundError as exc:
        raise _not_found(exc) from None


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: int, user: CurrentUser) -> None:
    try:
        with session_scope() as session:
            ConversationService(session).delete(user.id, conversation_id)
    except ConversationNotFoundError as exc:
        raise _not_found(exc) from None
