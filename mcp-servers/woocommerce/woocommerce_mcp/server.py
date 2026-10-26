import hmac
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from woocommerce_mcp.client import (
    WooCommerceAPIError,
    WooCommerceAuthenticationError,
    WooCommerceClient,
    WooCommerceTimeoutError,
)
from woocommerce_mcp.config import Settings, get_settings

mcp = MCPServer(
    name="woocommerce",
    instructions=(
        "Tools for reading and managing the products and orders of one WooCommerce store."
    ),
)


def _product_summary(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": product.get("id"),
        "name": product.get("name"),
        "sku": product.get("sku"),
        "status": product.get("status"),
        "price": product.get("price"),
        "regular_price": product.get("regular_price"),
        "stock_status": product.get("stock_status"),
        "permalink": product.get("permalink"),
    }


def _order_summary(order: dict[str, Any]) -> dict[str, Any]:
    billing = order.get("billing") or {}
    return {
        "id": order.get("id"),
        "status": order.get("status"),
        "total": order.get("total"),
        "currency": order.get("currency"),
        "date_created": order.get("date_created"),
        "customer": " ".join(
            part for part in (billing.get("first_name"), billing.get("last_name")) if part
        ),
        "email": billing.get("email"),
        "payment_method": order.get("payment_method_title"),
        "items": [
            {
                "product_id": item.get("product_id"),
                "name": item.get("name"),
                "quantity": item.get("quantity"),
                "total": item.get("total"),
            }
            for item in order.get("line_items") or []
        ],
    }


async def _call(operation: Any) -> Any:
    """Await a client call and convert failures into safe tool errors."""
    try:
        return await operation
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    except WooCommerceAuthenticationError:
        raise ToolError(
            "The store rejected the server's credentials; check the WooCommerce API key"
        ) from None
    except WooCommerceTimeoutError:
        raise ToolError("The store did not respond in time") from None
    except WooCommerceAPIError as exc:
        raise ToolError(str(exc)) from None


@mcp.tool()
async def list_products(
    page: Annotated[int, Field(ge=1, description="Page number, starting at 1")] = 1,
    per_page: Annotated[int, Field(ge=1, le=100, description="Items per page")] = 20,
) -> list[dict[str, Any]]:
    """List products in the store, newest first."""
    async with WooCommerceClient() as client:
        products = await _call(client.list_products(page=page, per_page=per_page))
    return [_product_summary(product) for product in products]


@mcp.tool()
async def create_product(
    name: Annotated[str, Field(min_length=1, max_length=200, description="Product name")],
    price: Annotated[str, Field(description="Regular price as a number, e.g. 499 or 499.50")],
    description: Annotated[str | None, Field(description="Optional long description")] = None,
    status: Annotated[
        str, Field(description="publish (visible on the storefront), draft, pending or private")
    ] = "publish",
) -> dict[str, Any]:
    """Create a simple product. Published products appear on the storefront immediately."""
    async with WooCommerceClient() as client:
        product = await _call(
            client.create_product(
                name=name, regular_price=price, description=description, status=status
            )
        )
    return _product_summary(product)


@mcp.tool()
async def list_orders(
    after: Annotated[
        str | None,
        Field(
            description=(
                "Only orders created on or after this ISO 8601 date or datetime, "
                "e.g. 2026-10-22 for orders from the start of that day"
            )
        ),
    ] = None,
    status: Annotated[
        str | None,
        Field(description="Filter by status: pending, processing, on-hold, completed, "
              "cancelled, refunded or failed"),
    ] = None,
    page: Annotated[int, Field(ge=1, description="Page number, starting at 1")] = 1,
    per_page: Annotated[int, Field(ge=1, le=100, description="Items per page")] = 20,
) -> list[dict[str, Any]]:
    """List orders, newest first. Use `after` to answer questions like 'orders today'."""
    async with WooCommerceClient() as client:
        orders = await _call(
            client.list_orders(page=page, per_page=per_page, status=status, after=after)
        )
    return [_order_summary(order) for order in orders]


@mcp.tool()
async def get_order(
    order_id: Annotated[int, Field(ge=1, description="WooCommerce order ID")],
) -> dict[str, Any]:
    """Get one order with its line items."""
    async with WooCommerceClient() as client:
        order = await _call(client.get_order(order_id))
    return _order_summary(order)


@mcp.tool()
async def update_order_status(
    order_id: Annotated[int, Field(ge=1, description="WooCommerce order ID")],
    status: Annotated[
        str,
        Field(description="New status: pending, processing, on-hold, completed, "
              "cancelled, refunded or failed"),
    ],
) -> dict[str, Any]:
    """Change the status of an order."""
    async with WooCommerceClient() as client:
        order = await _call(client.update_order_status(order_id, status))
    return _order_summary(order)


class _BearerAuthMiddleware:
    """Rejects requests that do not carry the gateway's shared secret.

    /healthz stays open so Kubernetes probes work without the secret.
    """

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
