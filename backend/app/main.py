import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import audit, auth, chat, keys, metrics_endpoint, permissions, servers, tools, users
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.middleware import HardeningMiddleware, RequestLogMiddleware
from app.core.security import cipher
from app.db.session import session_scope
from app.gateway.lite_app import build_lite_app
from app.gateway.mcp_app import build_mcp_app
from app.gateway.service import GatewayService
from app.repositories.mcp_server_repository import MCPServerRepository
from app.repositories.user_repository import UserRepository
from app.services.mcp_server_service import MCPServerCreate, MCPServerService
from app.services.user_service import UserService

configure_logging(settings.log_format, settings.log_level)
logger = logging.getLogger("app.startup")

gateway = GatewayService(settings, cipher)
mcp_app = build_mcp_app(gateway, settings)
lite_app = build_lite_app(gateway, settings)


def _seed_servers() -> list[MCPServerCreate]:
    """Upstreams to register at startup when their URL is configured."""
    candidates = [
        ("WooCommerce", "woocommerce", "Products and orders of the WooCommerce store.",
         settings.seed_woocommerce_url, settings.seed_woocommerce_token),
        ("Utilities", "utilities", "Time, unit conversion and arithmetic helpers.",
         settings.seed_utilities_url, settings.seed_utilities_token),
        ("Docs Search", "docs", "Search and read internal documents such as store policies.",
         settings.seed_docs_url, settings.seed_docs_token),
    ]
    return [
        MCPServerCreate(
            name=name,
            slug=slug,
            endpoint_url=url,
            description=description,
            auth_type="bearer" if token else "none",
            credential=token,
        )
        for name, slug, description, url, token in candidates
        if url
    ]


def _bootstrap() -> None:
    """Create the first admin and the first upstreams, but only when they are missing."""
    with session_scope() as session:
        if settings.bootstrap_admin_email and settings.bootstrap_admin_password:
            created = UserService(UserRepository(session)).ensure_bootstrap_admin(
                settings.bootstrap_admin_email, settings.bootstrap_admin_password
            )
            if created:
                logger.info("Created bootstrap admin %s", settings.bootstrap_admin_email)

        servers_service = MCPServerService(MCPServerRepository(session), cipher)
        for seed in _seed_servers():
            if servers_service.ensure_seed_server(seed):
                logger.info("Registered the %s MCP server", seed.name)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    _bootstrap()
    app.state.gateway = gateway
    gateway.start_health_checks()
    # The mounted MCP apps do not get their own lifespan from the parent, so run them here.
    async with (
        mcp_app.router.lifespan_context(mcp_app),
        lite_app.router.lifespan_context(lite_app),
    ):
        try:
            yield
        finally:
            await gateway.stop_health_checks()


app = FastAPI(title="Team MCP Gateway API", version="0.3.0", lifespan=lifespan)

# Outermost last: the request id and log line must wrap everything else.
app.add_middleware(HardeningMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLogMiddleware)

API_PREFIX = "/api/v1"
for module in (auth, users, keys, servers, tools, audit, permissions, chat):
    app.include_router(module.router, prefix=API_PREFIX)
app.include_router(metrics_endpoint.router)


@app.get("/health", tags=["Health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


# MCP endpoints: /lite/mcp (search-first, three meta-tools) and /mcp (every tool).
# The catch-all mount must stay last so it never shadows the API or the lite endpoint.
app.mount("/lite", lite_app)
app.mount("/", mcp_app)
