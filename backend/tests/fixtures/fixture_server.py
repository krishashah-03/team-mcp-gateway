"""A tiny MCP server used only by tests and demos.

It deliberately exposes ``list_orders`` (the same name as the WooCommerce server) to
prove that the gateway's namespacing prevents collisions, and ``slow`` to exercise
the gateway's upstream timeouts.
"""

import asyncio
import sys

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

mcp = MCPServer(name="fixture")


@mcp.tool()
def echo(text: str) -> str:
    """Return the text unchanged."""
    return text


@mcp.tool()
def list_orders() -> list[str]:
    """A fixture tool whose name collides with the WooCommerce server's list_orders."""
    return ["fixture-order-1"]


@mcp.tool()
async def slow(seconds: float = 30) -> str:
    """Sleep for a while, to test timeouts."""
    await asyncio.sleep(seconds)
    return "done"


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8002
    app = mcp.streamable_http_app(
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=["127.0.0.1:*", "localhost:*"]
        )
    )
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
