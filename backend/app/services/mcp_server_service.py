import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy.exc import IntegrityError

from app.core.security import CredentialCipher
from app.db.models import MCPServer
from app.repositories.mcp_server_repository import _UNSET, MCPServerRepository


_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_HEADER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,100}$")
# Headers the HTTP stack or the MCP transport owns; a credential must not override them.
_FORBIDDEN_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "content-type",
        "transfer-encoding",
        "connection",
        "accept",
        "mcp-session-id",
        "mcp-protocol-version",
    }
)
AUTH_TYPES = frozenset({"none", "bearer", "header"})
# Sentinel meaning "leave the stored credential as it is".
KEEP = _UNSET


@dataclass(frozen=True)
class MCPServerRecord:
    id: int
    name: str
    slug: str
    endpoint_url: str
    is_enabled: bool
    description: str | None
    auth_type: str = "none"
    auth_header_name: str | None = None
    # The credential itself is never exposed; callers only learn whether one is set.
    has_credential: bool = False
    updated_at: datetime | None = None


@dataclass(frozen=True)
class UpstreamTarget:
    """Everything the gateway needs to call one upstream, including decrypted headers."""

    id: int
    slug: str
    name: str
    url: str
    version: str
    headers: dict[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class MCPServerCreate:
    name: str
    slug: str
    endpoint_url: str
    description: str | None = None
    is_enabled: bool = True
    auth_type: str = "none"
    auth_header_name: str | None = None
    credential: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class MCPServerUpdate:
    name: str | None = None
    endpoint_url: str | None = None
    description: str | None = None
    is_enabled: bool | None = None
    auth_type: str | None = None
    auth_header_name: object = KEEP
    credential: object = field(default=KEEP, repr=False)


class MCPServerNotFoundError(LookupError):
    pass


class DuplicateMCPServerSlugError(ValueError):
    pass


class MCPServerValidationError(ValueError):
    pass


class MCPServerService:
    def __init__(self, repository: MCPServerRepository, cipher: CredentialCipher) -> None:
        self._repository = repository
        self._cipher = cipher

    def list_servers(self) -> list[MCPServerRecord]:
        return [self._to_record(server) for server in self._repository.list_servers()]

    def list_enabled_targets(self) -> list[UpstreamTarget]:
        return [
            self._to_target(server)
            for server in self._repository.list_servers(enabled_only=True)
        ]

    def get_target(self, server_id: int) -> UpstreamTarget:
        return self._to_target(self._require(server_id))

    def get_server(self, server_id: int) -> MCPServerRecord:
        return self._to_record(self._require(server_id))

    def get_server_by_slug(self, slug: str) -> MCPServerRecord:
        server = self._repository.get_by_slug(slug)
        if server is None:
            raise MCPServerNotFoundError(f"MCP server with slug '{slug}' was not found")
        return self._to_record(server)

    def find_slug(self, slug: str) -> MCPServerRecord | None:
        server = self._repository.get_by_slug(slug)
        return self._to_record(server) if server is not None else None

    def create_server(self, data: MCPServerCreate) -> MCPServerRecord:
        name = self._validate_name(data.name)
        slug = self._validate_slug(data.slug)
        endpoint_url = self._validate_endpoint_url(data.endpoint_url)
        description = self._validate_description(data.description)
        auth_type, header_name, credential = self._validate_auth(
            data.auth_type, data.auth_header_name, data.credential
        )

        if self._repository.get_by_slug(slug) is not None:
            raise DuplicateMCPServerSlugError(f"MCP server slug '{slug}' already exists")

        try:
            server = self._repository.create(
                name=name,
                slug=slug,
                endpoint_url=endpoint_url,
                description=description,
                is_enabled=data.is_enabled,
                auth_type=auth_type,
                auth_header_name=header_name,
                credential_encrypted=(
                    self._cipher.encrypt(credential) if credential else None
                ),
            )
        except IntegrityError as exc:
            original_error = exc.orig
            sqlstate = getattr(
                original_error,
                "sqlstate",
                getattr(original_error, "pgcode", None),
            )
            if sqlstate != "23505":
                raise
            raise DuplicateMCPServerSlugError(
                f"MCP server slug '{slug}' already exists"
            ) from exc
        return self._to_record(server)

    def update_server(self, server_id: int, data: MCPServerUpdate) -> MCPServerRecord:
        server = self._require(server_id)

        name = self._validate_name(data.name) if data.name is not None else None
        endpoint_url = (
            self._validate_endpoint_url(data.endpoint_url)
            if data.endpoint_url is not None
            else None
        )
        description = (
            self._validate_description(data.description)
            if data.description is not None
            else None
        )

        # Work out the resulting credential configuration and validate it as a whole.
        auth_type = data.auth_type if data.auth_type is not None else server.auth_type
        header_name = (
            data.auth_header_name
            if data.auth_header_name is not KEEP
            else server.auth_header_name
        )
        if data.credential is not KEEP:
            new_credential = data.credential  # type: ignore[assignment]
        else:
            new_credential = (
                self._cipher.decrypt(server.credential_encrypted)
                if server.credential_encrypted
                else None
            )
        auth_type, header_name, credential = self._validate_auth(
            auth_type, header_name, new_credential  # type: ignore[arg-type]
        )

        server = self._repository.update(
            server,
            name=name,
            endpoint_url=endpoint_url,
            description=description,
            is_enabled=data.is_enabled,
            auth_type=auth_type,
            auth_header_name=header_name,
            credential_encrypted=self._cipher.encrypt(credential) if credential else None,
        )
        return self._to_record(server)

    def remove_server(self, server_id: int) -> None:
        self._repository.remove(self._require(server_id))

    def ensure_seed_server(self, data: MCPServerCreate) -> bool:
        """Register a server at startup if its slug is not taken. Returns True if created."""
        if self._repository.get_by_slug(data.slug) is not None:
            return False
        self.create_server(data)
        return True

    def _require(self, server_id: int) -> MCPServer:
        server = self._repository.get_by_id(server_id)
        if server is None:
            raise MCPServerNotFoundError(f"MCP server {server_id} was not found")
        return server

    @staticmethod
    def _validate_name(name: str) -> str:
        normalized = name.strip()
        if not normalized or len(normalized) > 200:
            raise MCPServerValidationError("Server name must be 1 to 200 characters")
        return normalized

    @staticmethod
    def _validate_slug(slug: str) -> str:
        normalized = slug.strip()
        if len(normalized) > 40 or not _SLUG_PATTERN.fullmatch(normalized):
            raise MCPServerValidationError(
                "Server slug must be at most 40 characters of lowercase letters, "
                "digits and single hyphens"
            )
        return normalized

    @staticmethod
    def _validate_endpoint_url(endpoint_url: str) -> str:
        normalized = endpoint_url.strip()
        try:
            parsed = urlsplit(normalized)
            valid = (
                parsed.scheme in {"http", "https"}
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
            )
            _ = parsed.port
        except ValueError:
            valid = False

        if not valid or len(normalized) > 2048:
            raise MCPServerValidationError(
                "Endpoint must be an HTTP(S) URL without embedded credentials"
            )
        return normalized

    @staticmethod
    def _validate_description(description: str | None) -> str | None:
        if description is not None and len(description) > 10_000:
            raise MCPServerValidationError("Description must be at most 10000 characters")
        return description

    @staticmethod
    def _validate_auth(
        auth_type: str, header_name: str | None, credential: str | None
    ) -> tuple[str, str | None, str | None]:
        if auth_type not in AUTH_TYPES:
            raise MCPServerValidationError("Auth type must be none, bearer or header")
        credential = credential.strip() if credential else None
        header_name = header_name.strip() if header_name else None

        if auth_type == "none":
            return "none", None, None
        if not credential or len(credential) > 4096:
            raise MCPServerValidationError(
                "A credential of at most 4096 characters is required for this auth type"
            )
        if "\n" in credential or "\r" in credential:
            raise MCPServerValidationError("Credential must not contain line breaks")
        if auth_type == "bearer":
            return "bearer", None, credential
        if (
            not header_name
            or not _HEADER_NAME_PATTERN.fullmatch(header_name)
            or header_name.lower() in _FORBIDDEN_HEADERS
        ):
            raise MCPServerValidationError(
                "Header auth needs a valid header name that is not a reserved HTTP header"
            )
        return "header", header_name, credential

    def _to_target(self, server: MCPServer) -> UpstreamTarget:
        headers: dict[str, str] = {}
        if server.auth_type != "none" and server.credential_encrypted:
            secret = self._cipher.decrypt(server.credential_encrypted)
            if server.auth_type == "bearer":
                headers["Authorization"] = f"Bearer {secret}"
            elif server.auth_header_name:
                headers[server.auth_header_name] = secret
        updated = server.updated_at.isoformat() if server.updated_at else ""
        return UpstreamTarget(
            id=server.id,
            slug=server.slug,
            name=server.name,
            url=server.endpoint_url,
            version=f"{server.id}:{updated}",
            headers=headers,
        )

    @staticmethod
    def _to_record(server: MCPServer) -> MCPServerRecord:
        return MCPServerRecord(
            id=server.id,
            name=server.name,
            slug=server.slug,
            endpoint_url=server.endpoint_url,
            is_enabled=server.is_enabled,
            description=server.description,
            auth_type=server.auth_type,
            auth_header_name=server.auth_header_name,
            has_credential=bool(server.credential_encrypted),
            updated_at=server.updated_at,
        )
