from collections.abc import Callable
from typing import Annotated, Any, TypeVar

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool

from app.api.deps import AdminUser, Gateway
from app.api.schemas import ServerCreate, ServerOut, ServerToolOut, ServerUpdate
from app.core.config import settings
from app.core.security import cipher
from app.core.urlguard import UnsafeURLError, check_upstream_url
from app.db.session import session_scope
from app.gateway.service import ServerStatus
from app.repositories.mcp_server_repository import MCPServerRepository
from app.services.mcp_server_service import (
    KEEP,
    DuplicateMCPServerSlugError,
    MCPServerCreate,
    MCPServerNotFoundError,
    MCPServerRecord,
    MCPServerService,
    MCPServerUpdate,
    MCPServerValidationError,
)

router = APIRouter(prefix="/servers", tags=["MCP servers"])
T = TypeVar("T")


def _server_service(session: Any) -> MCPServerService:
    return MCPServerService(MCPServerRepository(session), cipher)


async def _db(operation: Callable[[MCPServerService], T]) -> T:
    """Run one service operation in its own transaction, translating domain errors."""

    def run() -> T:
        with session_scope() as session:
            return operation(_server_service(session))

    try:
        return await run_in_threadpool(run)
    except MCPServerNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except DuplicateMCPServerSlugError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except MCPServerValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None


async def _check_destination(url: str | None) -> None:
    """Reject URLs that would let an admin aim the gateway at internal or metadata services."""
    if url is None:
        return
    try:
        await run_in_threadpool(
            lambda: check_upstream_url(
                url.strip(),
                allow_private=settings.upstream_allow_private_networks,
                allowed_hosts=settings.upstream_host_allowlist,
            )
        )
    except UnsafeURLError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None


def server_out(item: ServerStatus) -> ServerOut:
    record = item.server
    return ServerOut(
        id=record.id,
        name=record.name,
        slug=record.slug,
        endpoint_url=record.endpoint_url,
        description=record.description,
        is_enabled=record.is_enabled,
        auth_type=record.auth_type,  # type: ignore[arg-type]
        auth_header_name=record.auth_header_name,
        has_credential=record.has_credential,
        status=item.status,  # type: ignore[arg-type]
        status_detail=item.error,
        checked_at=item.checked_at,
        tools=[
            ServerToolOut(
                name=t.name, namespaced_name=t.namespaced_name, description=t.description
            )
            for t in item.tools
        ],
    )


async def _one(gateway: Any, record: MCPServerRecord, *, force: bool) -> ServerOut:
    for item in await gateway.overview(refresh={record.id} if force else False):
        if item.server.id == record.id:
            return server_out(item)
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail="MCP server not found")


@router.get("", response_model=list[ServerOut])
async def list_servers(
    _: AdminUser,
    gateway: Gateway,
    refresh: Annotated[bool, Query(description="Probe every server now instead of using the latest check")] = False,
) -> list[ServerOut]:
    """Every registered server with its status and the tools it exposes.

    Statuses come from a background check that runs every few seconds, so this returns
    immediately even when a server is down. Use ``refresh=true`` to probe them all now.
    """
    return [server_out(item) for item in await gateway.overview(refresh=refresh)]


@router.post("", response_model=ServerOut, status_code=status.HTTP_201_CREATED)
async def create_server(body: ServerCreate, _: AdminUser, gateway: Gateway) -> ServerOut:
    await _check_destination(body.endpoint_url)
    record = await _db(lambda svc: svc.create_server(MCPServerCreate(**body.model_dump())))
    return await _one(gateway, record, force=True)


@router.get("/{server_id}", response_model=ServerOut)
async def get_server(server_id: int, _: AdminUser, gateway: Gateway) -> ServerOut:
    record = await _db(lambda svc: svc.get_server(server_id))
    return await _one(gateway, record, force=True)


@router.patch("/{server_id}", response_model=ServerOut)
async def update_server(
    server_id: int, body: ServerUpdate, _: AdminUser, gateway: Gateway
) -> ServerOut:
    await _check_destination(body.endpoint_url)
    sent = body.model_fields_set
    update = MCPServerUpdate(
        name=body.name,
        endpoint_url=body.endpoint_url,
        description=body.description,
        is_enabled=body.is_enabled,
        auth_type=body.auth_type,
        # A field that was not sent keeps its stored value; one sent as null clears it.
        auth_header_name=body.auth_header_name if "auth_header_name" in sent else KEEP,
        credential=body.credential if "credential" in sent else KEEP,
    )
    record = await _db(lambda svc: svc.update_server(server_id, update))
    gateway.forget(server_id)
    return await _one(gateway, record, force=True)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(server_id: int, _: AdminUser, gateway: Gateway) -> None:
    await _db(lambda svc: svc.remove_server(server_id))
    gateway.forget(server_id)


@router.post("/{server_id}/refresh", response_model=ServerOut)
async def refresh_server(server_id: int, _: AdminUser, gateway: Gateway) -> ServerOut:
    """Re-probe a server now instead of waiting for the cached result to expire."""
    record = await _db(lambda svc: svc.get_server(server_id))
    return await _one(gateway, record, force=True)
