import uvicorn

from docs_search_mcp.config import get_settings
from docs_search_mcp.server import build_app


def main() -> None:
    settings = get_settings()
    uvicorn.run(build_app(settings), host=settings.mcp_host, port=settings.mcp_port)


if __name__ == "__main__":
    main()
