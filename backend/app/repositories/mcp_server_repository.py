from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MCPServer

_UNSET: object = object()


class MCPServerRepository:
    """Persistence operations for MCP server registrations.

    Write methods flush changes but leave commit and rollback to the caller.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_servers(self, *, enabled_only: bool = False) -> list[MCPServer]:
        statement = select(MCPServer).order_by(MCPServer.id)
        if enabled_only:
            statement = statement.where(MCPServer.is_enabled.is_(True))
        return list(self._session.scalars(statement).all())

    def get_by_id(self, server_id: int) -> MCPServer | None:
        return self._session.get(MCPServer, server_id)

    def get_by_slug(self, slug: str) -> MCPServer | None:
        statement = select(MCPServer).where(MCPServer.slug == slug)
        return self._session.scalar(statement)

    def create(
        self,
        *,
        name: str,
        slug: str,
        endpoint_url: str,
        description: str | None,
        is_enabled: bool,
        auth_type: str = "none",
        auth_header_name: str | None = None,
        credential_encrypted: str | None = None,
    ) -> MCPServer:
        server = MCPServer(
            name=name,
            slug=slug,
            endpoint_url=endpoint_url,
            description=description,
            is_enabled=is_enabled,
            auth_type=auth_type,
            auth_header_name=auth_header_name,
            credential_encrypted=credential_encrypted,
        )
        self._session.add(server)
        self._session.flush()
        return server

    def update(
        self,
        server: MCPServer,
        *,
        name: str | None = None,
        endpoint_url: str | None = None,
        description: str | None = None,
        is_enabled: bool | None = None,
        auth_type: str | None = None,
        auth_header_name: object = _UNSET,
        credential_encrypted: object = _UNSET,
    ) -> MCPServer:
        if name is not None:
            server.name = name
        if endpoint_url is not None:
            server.endpoint_url = endpoint_url
        if description is not None:
            server.description = description
        if is_enabled is not None:
            server.is_enabled = is_enabled
        if auth_type is not None:
            server.auth_type = auth_type
        if auth_header_name is not _UNSET:
            server.auth_header_name = auth_header_name  # type: ignore[assignment]
        if credential_encrypted is not _UNSET:
            server.credential_encrypted = credential_encrypted  # type: ignore[assignment]

        self._session.flush()
        self._session.refresh(server)
        return server

    def remove(self, server: MCPServer) -> None:
        self._session.delete(server)
        self._session.flush()
