import uvicorn

from woocommerce_mcp.config import get_settings
from woocommerce_mcp.server import build_app


def main() -> None:
    settings = get_settings()
    uvicorn.run(build_app(settings), host=settings.mcp_host, port=settings.mcp_port)


if __name__ == "__main__":
    main()
