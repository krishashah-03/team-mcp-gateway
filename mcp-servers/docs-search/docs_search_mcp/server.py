import hmac
import re
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from docs_search_mcp.config import Settings, get_settings

mcp = MCPServer(
    name="docs-search",
    instructions="Search and read the team's internal documents: store policies, FAQs, guides.",
)

_ALLOWED_SUFFIXES = {".md", ".txt"}
_MAX_FILE_BYTES = 512 * 1024
_SNIPPET_CHARS = 240
_WORD = re.compile(r"[a-z0-9]+")


def _docs_dir() -> Path:
    return get_settings().resolved_docs_dir


def _documents() -> list[Path]:
    # Only regular files directly inside the folder; no recursion, no symlinks, and
    # nothing hidden (dotfiles often hold settings or secrets).
    folder = _docs_dir()
    if not folder.is_dir():
        return []
    return sorted(
        path
        for path in folder.iterdir()
        if path.is_file()
        and not path.is_symlink()
        and not path.name.startswith(".")
        and path.suffix.lower() in _ALLOWED_SUFFIXES
        and path.stat().st_size <= _MAX_FILE_BYTES
    )


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:120]
    return fallback


def _find(name: str) -> Path:
    # Match against the listing instead of building a path from user input, so a name
    # like ../../secret can never reach outside the documents folder.
    wanted = name.strip().lower()
    for path in _documents():
        if wanted in (path.name.lower(), path.stem.lower()):
            return path
    raise ToolError(f"No document named '{name}'. Use list_documents to see what exists.")


@mcp.tool()
def list_documents() -> list[dict[str, Any]]:
    """List every available document with its title and size."""
    return [
        {"name": path.name, "title": _title(_read(path), path.stem), "bytes": path.stat().st_size}
        for path in _documents()
    ]


@mcp.tool()
def search_documents(
    query: Annotated[str, Field(min_length=2, max_length=200, description="Words to look for")],
    limit: Annotated[int, Field(ge=1, le=20, description="Maximum results")] = 5,
) -> list[dict[str, Any]]:
    """Find documents that mention the query words, best matches first, with a snippet."""
    terms = set(_WORD.findall(query.lower()))
    if not terms:
        raise ToolError("Query must contain letters or digits")

    results: list[dict[str, Any]] = []
    for path in _documents():
        text = _read(path)
        lowered = text.lower()
        words = _WORD.findall(lowered)
        score = sum(words.count(term) for term in terms)
        if score == 0:
            continue
        first_hit = min(lowered.find(term) for term in terms if term in lowered)
        start = max(0, first_hit - _SNIPPET_CHARS // 3)
        snippet = " ".join(text[start : start + _SNIPPET_CHARS].split())
        results.append(
            {"name": path.name, "title": _title(text, path.stem), "score": score, "snippet": snippet}
        )
    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:limit]


@mcp.tool()
def read_document(
    name: Annotated[str, Field(description="Document file name from list_documents, e.g. returns-policy.md")],
) -> dict[str, str]:
    """Read the full text of one document."""
    path = _find(name)
    return {"name": path.name, "content": _read(path)}


class _BearerAuthMiddleware:
    """Rejects requests without the gateway's shared secret; /healthz stays open."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] != "/healthz":
            header = Request(scope).headers.get("authorization", "")
            scheme, _, supplied = header.partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(
                supplied.strip().encode(), self._token
            ):
                response: Response = JSONResponse({"error": "unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)


def build_app(settings: Settings | None = None) -> Starlette:
    settings = settings or get_settings()
    app = mcp.streamable_http_app(
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
        ),
    )

    async def healthz(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    app.add_route("/healthz", healthz, methods=["GET"])
    if settings.mcp_auth_token:
        app.add_middleware(_BearerAuthMiddleware, token=settings.mcp_auth_token)
    return app
